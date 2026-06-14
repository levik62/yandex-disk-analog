# -*- coding: utf-8 -*-
"""
SQLite база данных для хранения состояния синхронизации Google Drive Sync.

Хранит информацию о файлах, логе операций и токене изменений Google Drive.
Путь к БД: %LOCALAPPDATA%\\GoogleDriveSync\\sync_state.db
"""

import os
import sqlite3
import logging
from datetime import datetime
from typing import Dict, List, Optional
from contextlib import contextmanager

from models import FileItem, SyncStatus, SyncOperation, OpType

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.path.join(
    os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
    'GoogleDriveSync',
    'sync_state.db',
)


class Database:
    """
    Менеджер SQLite базы данных для состояния синхронизации.

    Потокобезопасный (check_same_thread=False).
    Поддерживает контекстный менеджер.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        """
        Инициализация базы данных.

        Args:
            db_path: Путь к файлу БД. Если None, используется путь по умолчанию.
        """
        self._db_path = db_path or DEFAULT_DB_PATH
        self._ensure_directory()
        self._conn = sqlite3.connect(
            self._db_path,
            check_same_thread=False,
            detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()
        logger.info("База данных инициализирована: %s", self._db_path)

    def _ensure_directory(self) -> None:
        """Создаёт директорию для файла БД, если она не существует."""
        dir_path = os.path.dirname(self._db_path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
            logger.info("Создана директория для БД: %s", dir_path)

    def _create_tables(self) -> None:
        """Создаёт таблицы, если они ещё не существуют."""
        with self._transaction() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    size INTEGER DEFAULT 0,
                    modified TEXT,
                    is_dir INTEGER DEFAULT 0,
                    cloud_id TEXT,
                    local_hash TEXT,
                    cloud_hash TEXT,
                    sync_status TEXT DEFAULT 'PENDING',
                    mime_type TEXT,
                    last_sync TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sync_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    op_type TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'success',
                    error_message TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS change_token (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    token TEXT NOT NULL
                )
            """)
            # Индексы для ускорения запросов
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_files_path ON files(path)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_files_sync_status ON files(sync_status)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_files_cloud_id ON files(cloud_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_sync_log_timestamp ON sync_log(timestamp)"
            )

    @contextmanager
    def _transaction(self):
        """
        Контекстный менеджер для транзакций.

        Yields:
            sqlite3.Cursor: Курсор для выполнения запросов.
        """
        cursor = self._conn.cursor()
        try:
            yield cursor
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ========== Операции с файлами ==========

    def get_file(self, path: str) -> Optional[FileItem]:
        """
        Получить запись файла по относительному пути.

        Args:
            path: Относительный путь к файлу от корня папки синхронизации.

        Returns:
            FileItem или None, если файл не найден.
        """
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM files WHERE path = ?", (path,))
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_file_item(row)

    def upsert_file(self, item: FileItem) -> None:
        """
        Вставить или обновить запись файла.

        Args:
            item: Объект FileItem для сохранения.
        """
        modified_str = item.modified.isoformat() if item.modified else None
        now_str = datetime.now().isoformat()

        with self._transaction() as cursor:
            cursor.execute("""
                INSERT INTO files (path, name, size, modified, is_dir, cloud_id,
                                   local_hash, cloud_hash, sync_status, mime_type, last_sync)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    name = excluded.name,
                    size = excluded.size,
                    modified = excluded.modified,
                    is_dir = excluded.is_dir,
                    cloud_id = excluded.cloud_id,
                    local_hash = excluded.local_hash,
                    cloud_hash = excluded.cloud_hash,
                    sync_status = excluded.sync_status,
                    mime_type = excluded.mime_type,
                    last_sync = excluded.last_sync
            """, (
                item.path,
                item.name,
                item.size,
                modified_str,
                1 if item.is_dir else 0,
                item.cloud_id,
                item.local_hash,
                item.cloud_hash,
                item.sync_status.name,
                item.mime_type,
                now_str,
            ))
        logger.debug("Upsert файл: %s (статус: %s)", item.path, item.sync_status.name)

    def delete_file(self, path: str) -> None:
        """
        Удалить запись файла по пути.

        Args:
            path: Относительный путь к файлу.
        """
        with self._transaction() as cursor:
            cursor.execute("DELETE FROM files WHERE path = ?", (path,))
        logger.debug("Удалена запись файла: %s", path)

    def get_all_files(self) -> List[FileItem]:
        """
        Получить список всех файлов в базе данных.

        Returns:
            Список объектов FileItem.
        """
        cursor = self._conn.cursor()
        cursor.execute("SELECT * FROM files ORDER BY path")
        return [self._row_to_file_item(row) for row in cursor.fetchall()]

    def get_files_by_status(self, status: SyncStatus) -> List[FileItem]:
        """
        Получить файлы с определённым статусом синхронизации.

        Args:
            status: Статус синхронизации для фильтрации.

        Returns:
            Список объектов FileItem с указанным статусом.
        """
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT * FROM files WHERE sync_status = ? ORDER BY path",
            (status.name,),
        )
        return [self._row_to_file_item(row) for row in cursor.fetchall()]

    def get_files_in_folder(self, folder_path: str) -> List[FileItem]:
        """
        Получить файлы в указанной папке (один уровень вложенности).

        Args:
            folder_path: Относительный путь к папке. Пустая строка — корень.

        Returns:
            Список объектов FileItem в папке.
        """
        cursor = self._conn.cursor()
        if not folder_path or folder_path == '':
            # Корневой уровень: файлы без разделителей в пути
            # или с одним сегментом
            cursor.execute("SELECT * FROM files ORDER BY is_dir DESC, path")
            all_rows = cursor.fetchall()
            result = []
            for row in all_rows:
                path = row['path']
                # Файл в корне = не содержит разделителей в пути
                normalized = path.replace('\\', '/')
                if '/' not in normalized:
                    result.append(self._row_to_file_item(row))
            return result
        else:
            # Файлы, путь которых начинается с folder_path/ и не содержит
            # дополнительных разделителей после folder_path/
            prefix = folder_path.replace('\\', '/').rstrip('/') + '/'
            cursor.execute(
                "SELECT * FROM files WHERE path LIKE ? ORDER BY is_dir DESC, path",
                (prefix + '%',),
            )
            result = []
            for row in cursor.fetchall():
                path = row['path'].replace('\\', '/')
                remainder = path[len(prefix):]
                if '/' not in remainder:
                    result.append(self._row_to_file_item(row))
            return result

    # ========== Лог операций ==========

    def log_operation(
        self,
        operation: SyncOperation,
        status: str = 'success',
        error: Optional[str] = None,
    ) -> None:
        """
        Записать операцию синхронизации в лог.

        Args:
            operation: Объект SyncOperation.
            status: Статус выполнения ('success' или 'error').
            error: Сообщение об ошибке (если есть).
        """
        with self._transaction() as cursor:
            cursor.execute("""
                INSERT INTO sync_log (timestamp, op_type, file_path, status, error_message)
                VALUES (?, ?, ?, ?, ?)
            """, (
                datetime.now().isoformat(),
                operation.op_type.name,
                operation.file_path,
                status,
                error or operation.error,
            ))
        logger.debug(
            "Лог операции: %s %s -> %s",
            operation.op_type.name, operation.file_path, status,
        )

    def get_sync_log(self, limit: int = 100) -> List[Dict]:
        """
        Получить последние записи из лога синхронизации.

        Args:
            limit: Максимальное количество записей.

        Returns:
            Список словарей с данными операций.
        """
        cursor = self._conn.cursor()
        cursor.execute(
            "SELECT * FROM sync_log ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = cursor.fetchall()
        result = []
        for row in rows:
            result.append({
                'id': row['id'],
                'timestamp': row['timestamp'],
                'op_type': row['op_type'],
                'file_path': row['file_path'],
                'status': row['status'],
                'error_message': row['error_message'],
            })
        return result

    # ========== Токен изменений ==========

    def get_change_token(self) -> Optional[str]:
        """
        Получить сохранённый токен изменений Google Drive.

        Returns:
            Строка токена или None, если токен не сохранён.
        """
        cursor = self._conn.cursor()
        cursor.execute("SELECT token FROM change_token WHERE id = 1")
        row = cursor.fetchone()
        return row['token'] if row else None

    def set_change_token(self, token: str) -> None:
        """
        Сохранить токен изменений Google Drive.

        Args:
            token: Строка токена (startPageToken).
        """
        with self._transaction() as cursor:
            cursor.execute("""
                INSERT INTO change_token (id, token) VALUES (1, ?)
                ON CONFLICT(id) DO UPDATE SET token = excluded.token
            """, (token,))
        logger.debug("Сохранён change token: %s", token)

    # ========== Вспомогательные методы ==========

    @staticmethod
    def _row_to_file_item(row: sqlite3.Row) -> FileItem:
        """
        Преобразовать строку SQLite в объект FileItem.

        Args:
            row: Строка из таблицы files.

        Returns:
            Объект FileItem.
        """
        modified = None
        if row['modified']:
            try:
                modified = datetime.fromisoformat(row['modified'])
            except (ValueError, TypeError):
                pass

        try:
            sync_status = SyncStatus[row['sync_status']]
        except (KeyError, TypeError):
            sync_status = SyncStatus.PENDING

        return FileItem(
            path=row['path'],
            name=row['name'],
            size=row['size'] or 0,
            modified=modified,
            is_dir=bool(row['is_dir']),
            cloud_id=row['cloud_id'],
            local_hash=row['local_hash'],
            cloud_hash=row['cloud_hash'],
            sync_status=sync_status,
            mime_type=row['mime_type'],
        )

    def close(self) -> None:
        """Закрыть соединение с базой данных."""
        if self._conn:
            self._conn.close()
            logger.info("Соединение с БД закрыто: %s", self._db_path)

    def __enter__(self) -> 'Database':
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"Database(path='{self._db_path}')"
