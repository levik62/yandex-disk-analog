# -*- coding: utf-8 -*-
"""
sync_engine.py — Ядро двусторонней синхронизации между локальной папкой и Google Drive.

Основные компоненты:
    • LocalWatcher   — мониторинг файловой системы через watchdog
    • CloudPoller    — периодический опрос Google Drive Changes API (QTimer)
    • SyncQueue      — приоритетная очередь операций с дедупликацией
    • SyncWorker     — пул потоков (upload / download) через ThreadPoolExecutor
    • ConflictResolver — разрешение конфликтов (newer wins + суффикс _conflict)
    • SyncEngine     — главный класс-оркестратор (QObject)

Зависимости:
    models.py    — FileItem, SyncOperation, SyncStatus, OpType
    config.py    — Config
    database.py  — Database
    drive_api.py — DriveAPI
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future
from datetime import datetime
from pathlib import Path
from queue import PriorityQueue, Empty
from typing import Optional, Set, Dict, Tuple

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot

from watchdog.observers import Observer
from watchdog.events import (
    FileSystemEventHandler,
    FileCreatedEvent,
    FileModifiedEvent,
    FileDeletedEvent,
    FileMovedEvent,
    DirCreatedEvent,
    DirDeletedEvent,
    DirMovedEvent,
)

from models import FileItem, SyncOperation, SyncStatus, OpType
from config import Config
from database import Database
from drive_api import DriveAPI

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
#  Вспомогательные константы
# ---------------------------------------------------------------------------

# Паттерны файлов / папок, которые игнорируются при синхронизации
_IGNORED_NAMES = {".sync", "desktop.ini", "thumbs.db"}
_IGNORED_PREFIXES = ("~", ".")
_IGNORED_SUFFIXES = (".tmp", ".crdownload", ".partial")

# Debounce-интервал для событий файловой системы (секунды)
_DEBOUNCE_INTERVAL = 0.3  # 300 мс

# Количество параллельных потоков
_UPLOAD_WORKERS = 3
_DOWNLOAD_WORKERS = 3

# Размер буфера при вычислении хэша (8 КБ)
_HASH_BUFFER_SIZE = 8192

# Приоритеты операций (меньше — выше приоритет)
_PRIORITY_MAP: Dict[OpType, int] = {
    OpType.DELETE_LOCAL: 1,
    OpType.DELETE_CLOUD: 1,
    OpType.DOWNLOAD: 2,
    OpType.UPLOAD: 3,
    OpType.RENAME: 4,
    OpType.MOVE: 4,
}


# ---------------------------------------------------------------------------
#  SyncQueue — приоритетная очередь с дедупликацией
# ---------------------------------------------------------------------------

class SyncQueue:
    """Потокобезопасная приоритетная очередь синхронизации.

    Каждая операция имеет приоритет, определяемый по типу (DELETE > DOWNLOAD
    > UPLOAD > RENAME/MOVE).  Дедупликация гарантирует, что для одного
    файла не будет двух одновременных операций в очереди.
    """

    def __init__(self) -> None:
        self._queue: PriorityQueue[Tuple[int, float, SyncOperation]] = PriorityQueue()
        self._pending_paths: Set[str] = set()
        self._lock = threading.Lock()
        self._counter = 0  # для устойчивой сортировки при одинаковых приоритетах

    def put(self, operation: SyncOperation) -> bool:
        """Добавить операцию в очередь.

        Returns:
            True  — операция добавлена.
            False — операция для данного файла уже в очереди (дедупликация).
        """
        with self._lock:
            if operation.file_path in self._pending_paths:
                logger.debug(
                    "Дедупликация: операция для '%s' уже в очереди",
                    operation.file_path,
                )
                return False
            priority = _PRIORITY_MAP.get(operation.op_type, 5)
            self._counter += 1
            self._queue.put((priority, self._counter, operation))
            self._pending_paths.add(operation.file_path)
            return True

    def get(self, timeout: float = 0.5) -> Optional[SyncOperation]:
        """Извлечь следующую операцию из очереди (блокирует до timeout)."""
        try:
            _, _, operation = self._queue.get(timeout=timeout)
            with self._lock:
                self._pending_paths.discard(operation.file_path)
            return operation
        except Empty:
            return None

    def pending_count(self) -> int:
        """Количество ожидающих операций."""
        return self._queue.qsize()

    def has_pending(self, file_path: str) -> bool:
        """Есть ли операция для данного файла в очереди."""
        with self._lock:
            return file_path in self._pending_paths

    def clear(self) -> None:
        """Очистить очередь."""
        with self._lock:
            while not self._queue.empty():
                try:
                    self._queue.get_nowait()
                except Empty:
                    break
            self._pending_paths.clear()


# ---------------------------------------------------------------------------
#  LocalWatcher — мониторинг файловой системы через watchdog
# ---------------------------------------------------------------------------

class _DebouncedHandler(FileSystemEventHandler):
    """FileSystemEventHandler с debounce-логикой.

    Быстрые последовательные события для одного и того же пути
    объединяются: обрабатывается только последнее событие, если с момента
    предыдущего прошло меньше ``_DEBOUNCE_INTERVAL`` секунд.
    """

    def __init__(self, engine: "SyncEngine") -> None:
        super().__init__()
        self._engine = engine
        self._last_events: Dict[str, float] = {}
        self._lock = threading.Lock()

    # -- helpers --

    @staticmethod
    def _should_ignore(path: str) -> bool:
        """Проверить, нужно ли игнорировать путь."""
        name = os.path.basename(path).lower()
        if name in _IGNORED_NAMES:
            return True
        if any(name.startswith(p) for p in _IGNORED_PREFIXES):
            return True
        if any(name.endswith(s) for s in _IGNORED_SUFFIXES):
            return True
        # Игнорировать всё внутри .sync/
        parts = Path(path).parts
        if ".sync" in parts:
            return True
        return False

    def _debounce(self, path: str) -> bool:
        """Вернуть True, если событие нужно проигнорировать (debounce)."""
        now = time.monotonic()
        with self._lock:
            last = self._last_events.get(path, 0.0)
            if now - last < _DEBOUNCE_INTERVAL:
                return True
            self._last_events[path] = now
        return False

    # -- события watchdog --

    def on_created(self, event: FileCreatedEvent | DirCreatedEvent) -> None:
        path = event.src_path
        if self._should_ignore(path) or self._debounce(path):
            return
        if event.is_directory:
            logger.info("Создана папка: %s", path)
            return  # Папки создадутся автоматически при загрузке вложенных файлов
        logger.info("Создан файл: %s", path)
        rel = self._engine._relative_path(path)
        self._engine._enqueue(OpType.UPLOAD, rel)

    def on_modified(self, event: FileModifiedEvent) -> None:
        if event.is_directory:
            return
        path = event.src_path
        if self._should_ignore(path) or self._debounce(path):
            return
        logger.info("Изменён файл: %s", path)
        rel = self._engine._relative_path(path)
        self._engine._enqueue(OpType.UPLOAD, rel)

    def on_deleted(self, event: FileDeletedEvent | DirDeletedEvent) -> None:
        path = event.src_path
        if self._should_ignore(path):
            return
        logger.info("Удалён: %s", path)
        rel = self._engine._relative_path(path)
        self._engine._enqueue(OpType.DELETE_CLOUD, rel)

    def on_moved(self, event: FileMovedEvent | DirMovedEvent) -> None:
        src = event.src_path
        dest = event.dest_path
        if self._should_ignore(src) and self._should_ignore(dest):
            return
        logger.info("Перемещён: %s → %s", src, dest)
        rel_src = self._engine._relative_path(src)
        rel_dest = self._engine._relative_path(dest)
        # Если перемещение за пределы папки синхронизации — удаление
        if not dest.startswith(self._engine._sync_folder):
            self._engine._enqueue(OpType.DELETE_CLOUD, rel_src)
        else:
            op = SyncOperation(
                op_type=OpType.RENAME,
                file_path=rel_src,
            )
            # Сохраняем новый путь в поле error (временно, как контейнер)
            op.error = rel_dest
            self._engine._queue.put(op)


class LocalWatcher:
    """Обёртка над watchdog.Observer для мониторинга папки синхронизации."""

    def __init__(self, engine: "SyncEngine") -> None:
        self._engine = engine
        self._observer: Optional[Observer] = None
        self._handler = _DebouncedHandler(engine)

    def start(self) -> None:
        """Запустить мониторинг."""
        if self._observer and self._observer.is_alive():
            return
        self._observer = Observer()
        self._observer.schedule(
            self._handler,
            path=self._engine._sync_folder,
            recursive=True,
        )
        self._observer.daemon = True
        self._observer.start()
        logger.info("LocalWatcher запущен для '%s'", self._engine._sync_folder)

    def stop(self) -> None:
        """Остановить мониторинг."""
        if self._observer and self._observer.is_alive():
            self._observer.stop()
            self._observer.join(timeout=5)
            logger.info("LocalWatcher остановлен")
        self._observer = None

    @property
    def is_alive(self) -> bool:
        return self._observer is not None and self._observer.is_alive()


# ---------------------------------------------------------------------------
#  ConflictResolver
# ---------------------------------------------------------------------------

class ConflictResolver:
    """Разрешение конфликтов при двусторонней синхронизации.

    Стратегия:
        • Побеждает файл с более поздней датой модификации (newer wins).
        • Проигравшая версия сохраняется с суффиксом ``_conflict_YYYYMMDD``.
    """

    @staticmethod
    def resolve(
        local_path: str,
        local_modified: Optional[datetime],
        cloud_modified: Optional[datetime],
    ) -> str:
        """Определить действие при конфликте.

        Returns:
            ``'upload'``   — локальная версия новее → загрузить в облако.
            ``'download'`` — облачная версия новее → скачать из облака.
            ``'equal'``    — даты совпадают, действий не требуется.
        """
        if local_modified is None and cloud_modified is None:
            return "equal"
        if local_modified is None:
            return "download"
        if cloud_modified is None:
            return "upload"
        if local_modified > cloud_modified:
            return "upload"
        if cloud_modified > local_modified:
            return "download"
        return "equal"

    @staticmethod
    def make_conflict_copy(file_path: str) -> str:
        """Создать конфликтную копию файла.

        Добавляет суффикс ``_conflict_YYYYMMDD`` к имени файла (перед
        расширением).  Если такой файл уже существует, добавляет счётчик.

        Returns:
            Путь к созданной копии.
        """
        p = Path(file_path)
        stamp = datetime.now().strftime("%Y%m%d")
        stem = p.stem
        suffix = p.suffix
        parent = p.parent

        conflict_name = f"{stem}_conflict_{stamp}{suffix}"
        conflict_path = parent / conflict_name

        counter = 1
        while conflict_path.exists():
            conflict_name = f"{stem}_conflict_{stamp}_{counter}{suffix}"
            conflict_path = parent / conflict_name
            counter += 1

        if p.exists():
            shutil.copy2(str(p), str(conflict_path))
            logger.info("Конфликтная копия: %s → %s", file_path, conflict_path)

        return str(conflict_path)


# ---------------------------------------------------------------------------
#  SyncEngine — главный класс-оркестратор
# ---------------------------------------------------------------------------

class SyncEngine(QObject):
    """Ядро двусторонней синхронизации Google Drive.

    Связывает воедино LocalWatcher, CloudPoller, SyncQueue и пул
    рабочих потоков (SyncWorker).  Все взаимодействия с UI происходят
    через Qt-сигналы.
    """

    # -- Qt-сигналы --

    status_changed = pyqtSignal(str)                 # 'synced' | 'syncing' | 'error' | 'paused' | 'offline'
    file_status_changed = pyqtSignal(str, object)    # (path, SyncStatus)
    sync_progress = pyqtSignal(int, int)             # (done, total)
    sync_error = pyqtSignal(str)                     # сообщение об ошибке
    file_synced = pyqtSignal(str)                    # путь синхронизированного файла
    operation_started = pyqtSignal(object)           # SyncOperation
    operation_completed = pyqtSignal(object)         # SyncOperation

    def __init__(
        self,
        config: Config,
        drive_api: DriveAPI,
        database: Database,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)

        self._config = config
        self._api = drive_api
        self._db = database

        # Путь к папке синхронизации (нормализованный)
        self._sync_folder: str = os.path.normpath(config.sync_folder)
        os.makedirs(self._sync_folder, exist_ok=True)

        # ID корневой папки в Google Drive (инициализируется при start)
        self._root_folder_id: Optional[str] = None

        # Карта cloud_id → относительный путь (для быстрого поиска)
        self._cloud_id_map: Dict[str, str] = {}

        # Состояние
        self._status: str = "offline"
        self._running = False
        self._paused = False

        # Компоненты
        self._queue = SyncQueue()
        self._watcher = LocalWatcher(self)
        self._conflict_resolver = ConflictResolver()

        # CloudPoller (QTimer)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_cloud)

        # ThreadPoolExecutor'ы для upload и download
        self._upload_pool: Optional[ThreadPoolExecutor] = None
        self._download_pool: Optional[ThreadPoolExecutor] = None

        # Рабочий поток, обслуживающий очередь
        self._worker_thread: Optional[threading.Thread] = None
        self._worker_stop_event = threading.Event()

        # Счётчик завершённых операций (для sync_progress)
        self._ops_done = 0
        self._ops_total = 0
        self._ops_lock = threading.Lock()

        # Подключение сигналов DriveAPI
        self._api.upload_progress.connect(self._on_upload_progress)
        self._api.download_progress.connect(self._on_download_progress)
        self._api.api_error.connect(self._on_api_error)

    # ====================================================================
    #  Публичные свойства
    # ====================================================================

    @property
    def is_running(self) -> bool:
        """Запущен ли движок."""
        return self._running

    @property
    def is_paused(self) -> bool:
        """Приостановлен ли движок."""
        return self._paused

    # ====================================================================
    #  Публичные методы
    # ====================================================================

    def start(self) -> None:
        """Запустить синхронизацию: watcher + poller + workers."""
        if self._running:
            logger.warning("SyncEngine уже запущен")
            return

        logger.info("Запуск SyncEngine…")
        self._running = True
        self._paused = False
        self._set_status("syncing")

        # Убедиться, что локальная папка синхронизации существует
        sync_folder = self._config.sync_folder
        if not os.path.exists(sync_folder):
            try:
                os.makedirs(sync_folder, exist_ok=True)
                logger.info("Создана локальная папка синхронизации: %s", sync_folder)
            except Exception as e:
                logger.error("Не удалось создать локальную папку синхронизации: %s", e)

        # Создаём стандартные папки Яндекс.Диска (кроме Заметок и Телемоста)
        yandex_default_folders = ["Документы", "Загрузки", "Музыка", "Картинки", "Фотокамера", "Скриншоты"]
        for folder_name in yandex_default_folders:
            folder_path = os.path.join(sync_folder, folder_name)
            if not os.path.exists(folder_path):
                try:
                    os.makedirs(folder_path, exist_ok=True)
                    logger.info("Создана стандартная папка: %s", folder_path)
                except Exception as e:
                    logger.error("Не удалось создать стандартную папку %s: %s", folder_name, e)

        try:
            # Убедиться, что корневая папка в Drive существует
            self._root_folder_id = self._api.ensure_sync_folder()
            logger.info("Корневая папка Drive: %s", self._root_folder_id)
        except Exception as exc:
            logger.error("Не удалось получить корневую папку Drive: %s", exc)
            self._set_status("error")
            self.sync_error.emit(f"Ошибка инициализации Drive: {exc}")
            self._running = False
            return

        # Построить карту cloud_id → path из БД
        self._rebuild_cloud_id_map()

        # Первичная синхронизация
        try:
            self._initial_sync()
        except Exception as exc:
            logger.error("Ошибка первичной синхронизации: %s", exc)
            self.sync_error.emit(f"Ошибка первичной синхронизации: {exc}")

        # Запуск файлового наблюдателя
        self._watcher.start()

        # Запуск CloudPoller
        interval = self._config.sync_interval * 1000  # мс
        self._poll_timer.start(interval)

        # Запуск пулов потоков
        self._upload_pool = ThreadPoolExecutor(
            max_workers=_UPLOAD_WORKERS,
            thread_name_prefix="sync-upload",
        )
        self._download_pool = ThreadPoolExecutor(
            max_workers=_DOWNLOAD_WORKERS,
            thread_name_prefix="sync-download",
        )

        # Запуск потока-диспетчера очереди
        self._worker_stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="sync-queue-dispatcher",
            daemon=True,
        )
        self._worker_thread.start()

        self._set_status("synced")
        logger.info("SyncEngine запущен")

    def stop(self) -> None:
        """Полная остановка синхронизации."""
        if not self._running:
            return

        logger.info("Остановка SyncEngine…")
        self._running = False

        # Остановить поллер
        self._poll_timer.stop()

        # Остановить watcher
        self._watcher.stop()

        # Остановить worker-поток
        self._worker_stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=5)

        # Остановить пулы
        if self._upload_pool:
            self._upload_pool.shutdown(wait=False, cancel_futures=True)
            self._upload_pool = None
        if self._download_pool:
            self._download_pool.shutdown(wait=False, cancel_futures=True)
            self._download_pool = None

        self._queue.clear()
        self._set_status("offline")
        logger.info("SyncEngine остановлен")

    def pause(self) -> None:
        """Приостановить выполнение операций.

        Watcher продолжает работать и накапливает события, но операции из
        очереди не выполняются.
        """
        if self._paused or not self._running:
            return
        self._paused = True
        self._set_status("paused")
        logger.info("SyncEngine на паузе")

    def resume(self) -> None:
        """Возобновить выполнение операций после паузы."""
        if not self._paused or not self._running:
            return
        self._paused = False
        self._set_status("syncing")
        logger.info("SyncEngine возобновлён")

    def sync_now(self) -> None:
        """Принудительная полная синхронизация (сканирование + опрос облака)."""
        if not self._running:
            logger.warning("sync_now: движок не запущен")
            return
        logger.info("Принудительная синхронизация…")
        self._set_status("syncing")

        # Запустить в отдельном потоке, чтобы не блокировать GUI
        threading.Thread(
            target=self._full_sync_task,
            name="sync-force",
            daemon=True,
        ).start()

    def get_status(self) -> str:
        """Текущий статус движка."""
        return self._status

    def get_pending_count(self) -> int:
        """Количество ожидающих операций в очереди."""
        return self._queue.pending_count()

    def get_file_status(self, path: str) -> SyncStatus:
        """Получить статус синхронизации конкретного файла."""
        item = self._db.get_file(path)
        if item:
            return item.sync_status
        return SyncStatus.PENDING

    # ====================================================================
    #  Внутренние методы: пути
    # ====================================================================

    def _relative_path(self, abs_path: str) -> str:
        """Преобразовать абсолютный путь в относительный (от sync_folder)."""
        return os.path.relpath(abs_path, self._sync_folder)

    def _absolute_path(self, rel_path: str) -> str:
        """Преобразовать относительный путь в абсолютный."""
        return os.path.normpath(os.path.join(self._sync_folder, rel_path))

    # ====================================================================
    #  Внутренние методы: хэш
    # ====================================================================

    @staticmethod
    def _compute_hash(path: str) -> str:
        """Вычислить MD5-хэш файла."""
        md5 = hashlib.md5()
        try:
            with open(path, "rb") as fh:
                while True:
                    chunk = fh.read(_HASH_BUFFER_SIZE)
                    if not chunk:
                        break
                    md5.update(chunk)
        except OSError as exc:
            logger.warning("Не удалось вычислить хэш '%s': %s", path, exc)
            return ""
        return md5.hexdigest()

    # ====================================================================
    #  Внутренние методы: статус
    # ====================================================================

    def _set_status(self, status: str) -> None:
        if self._status != status:
            self._status = status
            self.status_changed.emit(status)

    def _update_file_status(self, rel_path: str, status: SyncStatus) -> None:
        """Обновить статус файла в БД и эмитировать сигнал."""
        item = self._db.get_file(rel_path)
        if item:
            item.sync_status = status
            self._db.upsert_file(item)
        self.file_status_changed.emit(rel_path, status)

    # ====================================================================
    #  Внутренние методы: очередь
    # ====================================================================

    def _enqueue(self, op_type: OpType, file_path: str) -> None:
        """Создать SyncOperation и добавить в очередь."""
        op = SyncOperation(op_type=op_type, file_path=file_path)
        added = self._queue.put(op)
        if added:
            logger.debug("В очередь: %s — %s", op_type.name, file_path)
            with self._ops_lock:
                self._ops_total += 1

    # ====================================================================
    #  Внутренние методы: облако → карта
    # ====================================================================

    def _rebuild_cloud_id_map(self) -> None:
        """Перестроить карту cloud_id → relative_path из БД."""
        self._cloud_id_map.clear()
        for item in self._db.get_all_files():
            if item.cloud_id:
                self._cloud_id_map[item.cloud_id] = item.path

    # ====================================================================
    #  Первичная синхронизация
    # ====================================================================

    def _initial_sync(self) -> None:
        """Сравнить локальные файлы с облаком и поставить задачи."""
        logger.info("Первичная синхронизация…")
        self._set_status("syncing")

        # 1. Собрать локальные файлы
        local_files: Dict[str, str] = {}  # rel_path → abs_path
        selective = self._config.selective_sync_folders
        for root, dirs, files in os.walk(self._sync_folder):
            # Пропустить .sync
            dirs[:] = [d for d in dirs if d != ".sync"]
            for fname in files:
                if _DebouncedHandler._should_ignore(fname):
                    continue
                abs_p = os.path.join(root, fname)
                rel_p = self._relative_path(abs_p)
                # Проверка выборочной синхронизации
                if selective and not self._matches_selective(rel_p, selective):
                    continue
                local_files[rel_p] = abs_p

        # 2. Собрать облачные файлы (рекурсивно)
        cloud_files: Dict[str, dict] = {}  # rel_path → metadata
        self._list_cloud_recursive(self._root_folder_id, "", cloud_files)

        # 3. Сравнить
        all_paths = set(local_files.keys()) | set(cloud_files.keys())

        for rel_path in all_paths:
            in_local = rel_path in local_files
            in_cloud = rel_path in cloud_files

            db_item = self._db.get_file(rel_path)

            if in_local and in_cloud:
                # Файл есть и там, и там — проверить хэш / дату
                abs_p = local_files[rel_path]
                cloud_meta = cloud_files[rel_path]
                local_hash = self._compute_hash(abs_p)

                cloud_md5 = cloud_meta.get("md5Checksum", "")
                cloud_id = cloud_meta.get("id", "")
                cloud_modified = self._parse_cloud_time(
                    cloud_meta.get("modifiedTime", "")
                )
                local_modified = datetime.fromtimestamp(os.path.getmtime(abs_p))

                if local_hash and cloud_md5 and local_hash == cloud_md5:
                    # Файлы идентичны
                    self._upsert_db_item(
                        rel_path, abs_p, cloud_id, local_hash, cloud_md5,
                        local_modified, SyncStatus.SYNCED, cloud_meta,
                    )
                    continue

                # Конфликт — решаем по дате
                action = ConflictResolver.resolve(
                    abs_p, local_modified, cloud_modified
                )
                if action == "upload":
                    # Сохранить облачную версию как конфликтную
                    if cloud_md5:
                        conflict_path = ConflictResolver.make_conflict_copy(abs_p)
                        if conflict_path != abs_p:
                            self._api.download_file(cloud_id, conflict_path)
                    self._enqueue(OpType.UPLOAD, rel_path)
                elif action == "download":
                    ConflictResolver.make_conflict_copy(abs_p)
                    self._enqueue(OpType.DOWNLOAD, rel_path)
                    # Запомнить cloud_id для download
                    self._upsert_db_item(
                        rel_path, abs_p, cloud_id, local_hash, cloud_md5,
                        local_modified, SyncStatus.PENDING, cloud_meta,
                    )
                else:
                    self._upsert_db_item(
                        rel_path, abs_p, cloud_id, local_hash, cloud_md5,
                        local_modified, SyncStatus.SYNCED, cloud_meta,
                    )

            elif in_local and not in_cloud:
                # Только локально — загрузить в облако
                abs_p = local_files[rel_path]
                if db_item and db_item.cloud_id:
                    # Файл был в облаке, но удалён оттуда → удалить локально
                    self._enqueue(OpType.DELETE_LOCAL, rel_path)
                else:
                    self._enqueue(OpType.UPLOAD, rel_path)

            elif in_cloud and not in_local:
                # Только в облаке — скачать
                cloud_meta = cloud_files[rel_path]
                cloud_id = cloud_meta.get("id", "")
                cloud_modified = self._parse_cloud_time(
                    cloud_meta.get("modifiedTime", "")
                )
                if db_item and db_item.local_hash:
                    # Файл был локально, но удалён → удалить в облаке
                    self._enqueue(OpType.DELETE_CLOUD, rel_path)
                else:
                    self._upsert_db_item(
                        rel_path, self._absolute_path(rel_path),
                        cloud_id, "", "",
                        cloud_modified, SyncStatus.CLOUD_ONLY, cloud_meta,
                    )
                    self._enqueue(OpType.DOWNLOAD, rel_path)

        logger.info(
            "Первичная синхронизация завершена: %d операций в очереди",
            self._queue.pending_count(),
        )

    def _list_cloud_recursive(
        self,
        folder_id: str,
        prefix: str,
        result: Dict[str, dict],
    ) -> None:
        """Рекурсивно обойти папку Google Drive и заполнить словарь."""
        try:
            items = self._api.list_folder(folder_id)
        except Exception as exc:
            logger.error("Ошибка при листинге папки '%s': %s", folder_id, exc)
            return

        for item in items:
            name = item.get("name", "")
            item_id = item.get("id", "")
            mime = item.get("mimeType", "")
            rel = os.path.join(prefix, name) if prefix else name

            if mime == "application/vnd.google-apps.folder":
                self._list_cloud_recursive(item_id, rel, result)
            else:
                result[rel] = item
                self._cloud_id_map[item_id] = rel

    def _matches_selective(self, rel_path: str, folders: list[str]) -> bool:
        """Проверить, попадает ли файл в выборочную синхронизацию."""
        if not folders:
            return True
        for folder in folders:
            folder_norm = folder.replace("/", os.sep).replace("\\", os.sep)
            if rel_path.startswith(folder_norm) or rel_path == folder_norm:
                return True
        return False

    # ====================================================================
    #  CloudPoller
    # ====================================================================

    @pyqtSlot()
    def _poll_cloud(self) -> None:
        """Обработчик таймера: опросить Google Drive Changes API."""
        if self._paused or not self._running:
            return

        threading.Thread(
            target=self._poll_cloud_task,
            name="cloud-poller",
            daemon=True,
        ).start()

    def _poll_cloud_task(self) -> None:
        """Фоновый поток для опроса изменений в облаке."""
        try:
            start_token = self._db.get_change_token()
            if not start_token:
                start_token = self._api.get_start_page_token()
                self._db.set_change_token(start_token)
                return

            changes, new_token = self._api.get_changes(start_token)
            self._db.set_change_token(new_token)

            if not changes:
                return

            logger.info("CloudPoller: получено %d изменений", len(changes))

            for change in changes:
                file_id = change.get("fileId", "")
                removed = change.get("removed", False)
                file_meta = change.get("file", {})

                if removed or file_meta.get("trashed", False):
                    # Файл удалён из облака
                    rel_path = self._cloud_id_map.get(file_id)
                    if rel_path:
                        logger.info("Облачное удаление: %s", rel_path)
                        self._enqueue(OpType.DELETE_LOCAL, rel_path)
                    continue

                name = file_meta.get("name", "")
                mime = file_meta.get("mimeType", "")
                if mime == "application/vnd.google-apps.folder":
                    continue

                # Определить путь
                rel_path = self._cloud_id_map.get(file_id)
                if not rel_path:
                    # Новый файл в облаке — определить путь через parents
                    parents = file_meta.get("parents", [])
                    parent_path = ""
                    if parents:
                        parent_id = parents[0]
                        parent_path = self._cloud_id_map.get(parent_id, "")
                    rel_path = os.path.join(parent_path, name) if parent_path else name
                    self._cloud_id_map[file_id] = rel_path

                # Проверить, нужно ли скачивать
                db_item = self._db.get_file(rel_path)
                cloud_md5 = file_meta.get("md5Checksum", "")

                if db_item and db_item.cloud_hash == cloud_md5 and cloud_md5:
                    continue  # Не изменился

                # Обновить запись в БД
                cloud_modified = self._parse_cloud_time(
                    file_meta.get("modifiedTime", "")
                )
                self._upsert_db_item(
                    rel_path,
                    self._absolute_path(rel_path),
                    file_id,
                    db_item.local_hash if db_item else "",
                    cloud_md5,
                    cloud_modified,
                    SyncStatus.PENDING,
                    file_meta,
                )

                # Проверить конфликт
                abs_p = self._absolute_path(rel_path)
                if os.path.exists(abs_p) and db_item:
                    local_modified = datetime.fromtimestamp(os.path.getmtime(abs_p))
                    local_hash = self._compute_hash(abs_p)
                    if local_hash != (db_item.local_hash or ""):
                        # Файл изменён и локально → конфликт
                        action = ConflictResolver.resolve(
                            abs_p, local_modified, cloud_modified
                        )
                        if action == "upload":
                            self._enqueue(OpType.UPLOAD, rel_path)
                            continue
                        elif action == "download":
                            ConflictResolver.make_conflict_copy(abs_p)

                self._enqueue(OpType.DOWNLOAD, rel_path)

        except Exception as exc:
            logger.error("CloudPoller ошибка: %s", exc)
            self.sync_error.emit(f"Ошибка опроса облака: {exc}")

    # ====================================================================
    #  Полная синхронизация (sync_now)
    # ====================================================================

    def _full_sync_task(self) -> None:
        """Задача полной синхронизации (запускается в отдельном потоке)."""
        try:
            self._initial_sync()
            self._poll_cloud_task()
        except Exception as exc:
            logger.error("Ошибка полной синхронизации: %s", exc)
            self.sync_error.emit(f"Ошибка синхронизации: {exc}")
        finally:
            if self._queue.pending_count() == 0:
                self._set_status("synced")

    # ====================================================================
    #  Worker — диспетчер очереди
    # ====================================================================

    def _worker_loop(self) -> None:
        """Основной цикл потока-диспетчера.

        Извлекает операции из очереди и отправляет в соответствующий пул
        потоков.
        """
        logger.info("Worker-диспетчер запущен")

        while not self._worker_stop_event.is_set():
            if self._paused:
                time.sleep(0.5)
                continue

            op = self._queue.get(timeout=0.5)
            if op is None:
                # Очередь пуста
                if self._running and self._status == "syncing":
                    self._set_status("synced")
                continue

            self._set_status("syncing")
            self.operation_started.emit(op)

            try:
                if op.op_type in (OpType.UPLOAD, OpType.DELETE_CLOUD, OpType.RENAME, OpType.MOVE):
                    pool = self._upload_pool
                else:
                    pool = self._download_pool

                if pool is None:
                    logger.warning("Пул потоков не инициализирован")
                    continue

                future: Future = pool.submit(self._execute_operation, op)
                future.add_done_callback(
                    lambda f, _op=op: self._on_operation_done(f, _op)
                )
            except Exception as exc:
                logger.error("Ошибка отправки операции в пул: %s", exc)
                op.error = str(exc)
                self._on_operation_failed(op)

        logger.info("Worker-диспетчер остановлен")

    # ====================================================================
    #  Выполнение операций
    # ====================================================================

    def _execute_operation(self, op: SyncOperation) -> None:
        """Выполнить одну операцию синхронизации (вызывается в потоке пула)."""
        rel_path = op.file_path
        abs_path = self._absolute_path(rel_path)

        logger.info("Выполняю: %s — %s", op.op_type.name, rel_path)

        if op.op_type == OpType.UPLOAD:
            self._do_upload(op, rel_path, abs_path)

        elif op.op_type == OpType.DOWNLOAD:
            self._do_download(op, rel_path, abs_path)

        elif op.op_type == OpType.DELETE_LOCAL:
            self._do_delete_local(op, rel_path, abs_path)

        elif op.op_type == OpType.DELETE_CLOUD:
            self._do_delete_cloud(op, rel_path, abs_path)

        elif op.op_type == OpType.RENAME:
            self._do_rename(op, rel_path)

        elif op.op_type == OpType.MOVE:
            self._do_rename(op, rel_path)  # MOVE обрабатывается аналогично

    # -- Upload --

    def _do_upload(self, op: SyncOperation, rel_path: str, abs_path: str) -> None:
        if not os.path.exists(abs_path):
            logger.warning("Файл не найден для upload: %s", abs_path)
            op.error = "Файл не найден"
            return

        self._update_file_status(rel_path, SyncStatus.UPLOADING)

        # Определить parent_id
        parent_id = self._resolve_cloud_parent(rel_path)

        cloud_id = self._api.upload_file(abs_path, parent_id)
        local_hash = self._compute_hash(abs_path)
        modified = datetime.fromtimestamp(os.path.getmtime(abs_path))

        # Получить метаданные загруженного файла
        try:
            meta = self._api.get_file_metadata(cloud_id)
            cloud_md5 = meta.get("md5Checksum", "")
        except Exception:
            cloud_md5 = ""

        self._upsert_db_item(
            rel_path, abs_path, cloud_id, local_hash, cloud_md5,
            modified, SyncStatus.SYNCED, {},
        )
        self._cloud_id_map[cloud_id] = rel_path

        op.progress = 100.0
        self.file_synced.emit(rel_path)
        logger.info("Загружен: %s → %s", rel_path, cloud_id)

    # -- Download --

    def _do_download(self, op: SyncOperation, rel_path: str, abs_path: str) -> None:
        db_item = self._db.get_file(rel_path)
        cloud_id = db_item.cloud_id if db_item else None

        if not cloud_id:
            # Попробовать найти в карте по пути
            cloud_id = self._find_cloud_id_by_path(rel_path)
            if not cloud_id:
                logger.warning("Нет cloud_id для download: %s", rel_path)
                op.error = "Не найден cloud_id"
                return

        self._update_file_status(rel_path, SyncStatus.DOWNLOADING)

        # Создать родительские папки
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)

        success = self._api.download_file(cloud_id, abs_path)
        if not success:
            op.error = "Ошибка скачивания"
            self._update_file_status(rel_path, SyncStatus.ERROR)
            return

        local_hash = self._compute_hash(abs_path)
        modified = datetime.fromtimestamp(os.path.getmtime(abs_path))

        try:
            meta = self._api.get_file_metadata(cloud_id)
            cloud_md5 = meta.get("md5Checksum", "")
        except Exception:
            cloud_md5 = local_hash

        self._upsert_db_item(
            rel_path, abs_path, cloud_id, local_hash, cloud_md5,
            modified, SyncStatus.SYNCED, {},
        )

        op.progress = 100.0
        self.file_synced.emit(rel_path)
        logger.info("Скачан: %s", rel_path)

    # -- Delete local --

    def _do_delete_local(self, op: SyncOperation, rel_path: str, abs_path: str) -> None:
        if os.path.exists(abs_path):
            try:
                if os.path.isdir(abs_path):
                    shutil.rmtree(abs_path, ignore_errors=True)
                else:
                    os.remove(abs_path)
                logger.info("Удалён локально: %s", abs_path)
            except OSError as exc:
                logger.error("Не удалось удалить '%s': %s", abs_path, exc)
                op.error = str(exc)
                self._update_file_status(rel_path, SyncStatus.ERROR)
                return

        self._db.delete_file(rel_path)
        op.progress = 100.0

    # -- Delete cloud --

    def _do_delete_cloud(self, op: SyncOperation, rel_path: str, abs_path: str) -> None:
        db_item = self._db.get_file(rel_path)
        cloud_id = db_item.cloud_id if db_item else None

        if not cloud_id:
            cloud_id = self._find_cloud_id_by_path(rel_path)

        if cloud_id:
            try:
                self._api.delete_file(cloud_id)
                logger.info("Удалён из облака: %s (id=%s)", rel_path, cloud_id)
                self._cloud_id_map.pop(cloud_id, None)
            except Exception as exc:
                logger.error("Ошибка удаления из облака: %s", exc)
                op.error = str(exc)
                self._update_file_status(rel_path, SyncStatus.ERROR)
                return
        else:
            logger.warning("Нет cloud_id для удаления: %s", rel_path)

        self._db.delete_file(rel_path)
        op.progress = 100.0

    # -- Rename / Move --

    def _do_rename(self, op: SyncOperation, old_rel_path: str) -> None:
        new_rel_path = op.error  # Новый путь хранился в поле error
        op.error = None

        if not new_rel_path:
            logger.warning("Rename: не указан новый путь для '%s'", old_rel_path)
            return

        # Удалить старый файл из облака
        db_item = self._db.get_file(old_rel_path)
        if db_item and db_item.cloud_id:
            try:
                self._api.delete_file(db_item.cloud_id)
                self._cloud_id_map.pop(db_item.cloud_id, None)
            except Exception as exc:
                logger.error("Rename: ошибка удаления старого: %s", exc)

        self._db.delete_file(old_rel_path)

        # Загрузить как новый
        new_abs_path = self._absolute_path(new_rel_path)
        if os.path.exists(new_abs_path) and not os.path.isdir(new_abs_path):
            upload_op = SyncOperation(op_type=OpType.UPLOAD, file_path=new_rel_path)
            self._do_upload(upload_op, new_rel_path, new_abs_path)

        op.progress = 100.0
        logger.info("Переименование: %s → %s", old_rel_path, new_rel_path)

    # ====================================================================
    #  Обработка завершения операций
    # ====================================================================

    def _on_operation_done(self, future: Future, op: SyncOperation) -> None:
        """Callback после завершения операции в пуле."""
        exc = future.exception()
        if exc:
            logger.error("Операция %s для '%s' завершилась с ошибкой: %s",
                         op.op_type.name, op.file_path, exc)
            op.error = str(exc)
            self._on_operation_failed(op)
        else:
            if op.error:
                self._on_operation_failed(op)
            else:
                self._on_operation_succeeded(op)

    def _on_operation_succeeded(self, op: SyncOperation) -> None:
        """Операция завершена успешно."""
        with self._ops_lock:
            self._ops_done += 1
            done, total = self._ops_done, self._ops_total

        self.sync_progress.emit(done, total)
        self.operation_completed.emit(op)

        self._db.log_operation(op, status="success")

        if done >= total:
            with self._ops_lock:
                self._ops_done = 0
                self._ops_total = 0
            if self._running and not self._paused:
                self._set_status("synced")

    def _on_operation_failed(self, op: SyncOperation) -> None:
        """Операция завершена с ошибкой."""
        with self._ops_lock:
            self._ops_done += 1
            done, total = self._ops_done, self._ops_total

        self.sync_progress.emit(done, total)
        self.operation_completed.emit(op)

        error_msg = op.error or "Неизвестная ошибка"
        self._db.log_operation(op, status="error", error=error_msg)
        self._update_file_status(op.file_path, SyncStatus.ERROR)
        self.sync_error.emit(f"{op.op_type.name} {op.file_path}: {error_msg}")

        logger.error("Ошибка: %s '%s': %s", op.op_type.name, op.file_path, error_msg)

    # ====================================================================
    #  Вспомогательные методы
    # ====================================================================

    def _resolve_cloud_parent(self, rel_path: str) -> str:
        """Определить cloud parent_id для файла по его относительному пути.

        При необходимости создаёт промежуточные папки в Google Drive.
        """
        parts = Path(rel_path).parts
        if len(parts) <= 1:
            return self._root_folder_id or ""

        current_id = self._root_folder_id or ""
        current_rel = ""

        for folder_name in parts[:-1]:
            current_rel = os.path.join(current_rel, folder_name) if current_rel else folder_name

            # Проверить, есть ли папка в карте
            existing_id = self._find_cloud_id_by_path(current_rel)
            if existing_id:
                current_id = existing_id
                continue

            # Создать папку в Drive
            try:
                new_id = self._api.create_folder(folder_name, current_id)
                self._cloud_id_map[new_id] = current_rel
                current_id = new_id
                logger.info("Создана папка в облаке: %s (id=%s)", current_rel, new_id)
            except Exception as exc:
                logger.error("Ошибка создания папки '%s': %s", current_rel, exc)
                return current_id

        return current_id

    def _find_cloud_id_by_path(self, rel_path: str) -> Optional[str]:
        """Найти cloud_id по относительному пути (обратный поиск по карте)."""
        for cid, path in self._cloud_id_map.items():
            if path == rel_path:
                return cid
        # Попробовать БД
        db_item = self._db.get_file(rel_path)
        if db_item and db_item.cloud_id:
            return db_item.cloud_id
        return None

    def _upsert_db_item(
        self,
        rel_path: str,
        abs_path: str,
        cloud_id: str,
        local_hash: str,
        cloud_hash: str,
        modified: Optional[datetime],
        status: SyncStatus,
        cloud_meta: dict,
    ) -> None:
        """Создать или обновить запись в БД."""
        is_dir = os.path.isdir(abs_path) if os.path.exists(abs_path) else False
        size = 0
        if os.path.isfile(abs_path):
            try:
                size = os.path.getsize(abs_path)
            except OSError:
                size = int(cloud_meta.get("size", 0))

        item = FileItem(
            path=rel_path,
            name=os.path.basename(rel_path),
            size=size,
            modified=modified,
            is_dir=is_dir,
            cloud_id=cloud_id,
            local_hash=local_hash,
            cloud_hash=cloud_hash,
            sync_status=status,
            mime_type=cloud_meta.get("mimeType"),
        )
        self._db.upsert_file(item)

    @staticmethod
    def _parse_cloud_time(time_str: str) -> Optional[datetime]:
        """Парсинг строки времени из Google Drive API (RFC 3339)."""
        if not time_str:
            return None
        try:
            # Формат: 2026-06-13T10:30:00.000Z
            clean = time_str.replace("Z", "+00:00")
            return datetime.fromisoformat(clean)
        except (ValueError, TypeError):
            logger.warning("Не удалось распарсить время: %s", time_str)
            return None

    # ====================================================================
    #  Обработчики сигналов DriveAPI
    # ====================================================================

    @pyqtSlot(str, float)
    def _on_upload_progress(self, path: str, percent: float) -> None:
        """Прогресс загрузки файла."""
        rel = self._relative_path(path) if os.path.isabs(path) else path
        self.file_status_changed.emit(rel, SyncStatus.UPLOADING)

    @pyqtSlot(str, float)
    def _on_download_progress(self, path: str, percent: float) -> None:
        """Прогресс скачивания файла."""
        rel = self._relative_path(path) if os.path.isabs(path) else path
        self.file_status_changed.emit(rel, SyncStatus.DOWNLOADING)

    @pyqtSlot(str)
    def _on_api_error(self, error: str) -> None:
        """Ошибка API."""
        logger.error("DriveAPI ошибка: %s", error)
        self.sync_error.emit(error)
