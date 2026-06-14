# -*- coding: utf-8 -*-
"""
Глобальные горячие клавиши Windows.

Регистрирует системные горячие клавиши через WinAPI (RegisterHotKey/UnregisterHotKey),
слушает WM_HOTKEY сообщения в отдельном QThread и запускает
YandexDiskScreenshotEditor.exe для захвата экрана.
"""

import ctypes
import ctypes.wintypes
import subprocess
import os
import logging
from typing import Dict, Optional, Tuple

from PyQt6.QtCore import QObject, pyqtSignal, QThread
from PyQt6.QtWidgets import QMessageBox

from config import Config

logger = logging.getLogger(__name__)

# ── Путь к редактору скриншотов Яндекс.Диска ──────────────────────────────────
_appdata = os.environ.get("APPDATA", "")
YANDEX_SCREENSHOT_EDITOR: str = os.path.join(
    _appdata, "Yandex", "YandexDisk2", "3.2.47.5133", "YandexDiskScreenshotEditor.exe"
)

# ── WinAPI-константы ───────────────────────────────────────────────────────────
WM_HOTKEY: int = 0x0312

# Модификаторы RegisterHotKey
MOD_ALT: int = 0x0001
MOD_CTRL: int = 0x0002
MOD_SHIFT: int = 0x0004
MOD_WIN: int = 0x0008
MOD_NOREPEAT: int = 0x4000

# Идентификаторы горячих клавиш (произвольные, уникальные в пределах потока)
HOTKEY_ID_CAPTURE_REGION: int = 1
HOTKEY_ID_CAPTURE_FULLSCREEN: int = 2
HOTKEY_ID_CAPTURE_WINDOW: int = 3

# Маппинг имён модификаторов → значения
_MODIFIER_MAP: Dict[str, int] = {
    "ctrl": MOD_CTRL,
    "control": MOD_CTRL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "super": MOD_WIN,
}

# Маппинг имён клавиш → виртуальные коды (VK)
_VK_MAP: Dict[str, int] = {
    # Функциональные клавиши
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    # Специальные клавиши
    "printscreen": 0x2C, "prtsc": 0x2C, "print": 0x2C, "snapshot": 0x2C,
    "pause": 0x13, "break": 0x13,
    "insert": 0x2D, "ins": 0x2D,
    "delete": 0x2E, "del": 0x2E,
    "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pgup": 0x21,
    "pagedown": 0x22, "pgdn": 0x22,
    "space": 0x20,
    "tab": 0x09,
    "escape": 0x1B, "esc": 0x1B,
    "enter": 0x0D, "return": 0x0D,
    "backspace": 0x08,
    # Стрелки
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    # Цифры 0-9 (ASCII)
    "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
    "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
    # Буквы A-Z (ASCII)
    "a": 0x41, "b": 0x42, "c": 0x43, "d": 0x44, "e": 0x45,
    "f": 0x46, "g": 0x47, "h": 0x48, "i": 0x49, "j": 0x4A,
    "k": 0x4B, "l": 0x4C, "m": 0x4D, "n": 0x4E, "o": 0x4F,
    "p": 0x50, "q": 0x51, "r": 0x52, "s": 0x53, "t": 0x54,
    "u": 0x55, "v": 0x56, "w": 0x57, "x": 0x58, "y": 0x59,
    "z": 0x5A,
    # Numpad
    "num0": 0x60, "num1": 0x61, "num2": 0x62, "num3": 0x63,
    "num4": 0x64, "num5": 0x65, "num6": 0x66, "num7": 0x67,
    "num8": 0x68, "num9": 0x69,
    "numpad0": 0x60, "numpad1": 0x61, "numpad2": 0x62, "numpad3": 0x63,
    "numpad4": 0x64, "numpad5": 0x65, "numpad6": 0x66, "numpad7": 0x67,
    "numpad8": 0x68, "numpad9": 0x69,
    "multiply": 0x6A, "add": 0x6B, "subtract": 0x6D,
    "decimal": 0x6E, "divide": 0x6F,
}

# Маппинг действие → аргументы командной строки для YandexDiskScreenshotEditor
_EDITOR_ARGS: Dict[str, list[str]] = {
    "capture_region": ["--region"],
    "capture_fullscreen": ["--fullscreen"],
    "capture_window": ["--window"],
}

# Маппинг действие → ID горячей клавиши
_ACTION_TO_ID: Dict[str, int] = {
    "capture_region": HOTKEY_ID_CAPTURE_REGION,
    "capture_fullscreen": HOTKEY_ID_CAPTURE_FULLSCREEN,
    "capture_window": HOTKEY_ID_CAPTURE_WINDOW,
}


def parse_hotkey_string(hotkey_str: str) -> Tuple[int, int]:
    """
    Парсит строку горячей клавиши в пару (модификаторы, виртуальный код клавиши).

    Поддерживаемые форматы:
        - ``'PrintScreen'``
        - ``'Ctrl+Shift+3'``
        - ``'Alt+F4'``
        - ``'Ctrl+Shift+A'``

    Args:
        hotkey_str: Строка горячей клавиши, части разделены ``'+'``.

    Returns:
        Кортеж ``(modifiers, vk_code)`` для передачи в ``RegisterHotKey``.

    Raises:
        ValueError: Если строка пустая, содержит неизвестные токены
                    или не содержит основной клавиши.
    """
    if not hotkey_str or not hotkey_str.strip():
        raise ValueError("Строка горячей клавиши не может быть пустой")

    parts: list[str] = [p.strip().lower() for p in hotkey_str.split("+")]
    modifiers: int = 0
    vk_code: int = 0
    key_found: bool = False

    for part in parts:
        if part in _MODIFIER_MAP:
            modifiers |= _MODIFIER_MAP[part]
        elif part in _VK_MAP:
            if key_found:
                raise ValueError(
                    f"Несколько основных клавиш в строке: '{hotkey_str}'"
                )
            vk_code = _VK_MAP[part]
            key_found = True
        else:
            raise ValueError(
                f"Неизвестный токен '{part}' в строке горячей клавиши: '{hotkey_str}'"
            )

    if not key_found:
        raise ValueError(
            f"Не найдена основная клавиша в строке: '{hotkey_str}'"
        )

    return modifiers, vk_code


class _HotkeyListenerThread(QThread):
    """
    Фоновый поток для прослушивания WM_HOTKEY-сообщений Windows.

    Регистрирует горячие клавиши через ``RegisterHotKey`` в контексте
    собственного потока (требование WinAPI) и отправляет сигнал
    ``hotkey_triggered`` при нажатии зарегистрированной комбинации.

    Attributes:
        hotkey_triggered: Сигнал, испускаемый при срабатывании горячей клавиши.
                          Передаёт целочисленный ID горячей клавиши.
    """

    hotkey_triggered = pyqtSignal(int)

    def __init__(
        self,
        hotkeys_config: Dict[str, str],
        parent: Optional[QObject] = None,
    ) -> None:
        """
        Инициализация потока-слушателя.

        Args:
            hotkeys_config: Словарь ``{действие: строка_горячей_клавиши}``,
                            например ``{'capture_region': 'PrintScreen'}``.
            parent: Родительский QObject (опционально).
        """
        super().__init__(parent)
        self._hotkeys_config: Dict[str, str] = hotkeys_config
        self._registered_ids: list[int] = []
        self._running: bool = False

    def run(self) -> None:
        """
        Основной цикл потока: регистрация хоткеев и обработка WM_HOTKEY.

        RegisterHotKey привязывается к потоку, в котором был вызван,
        поэтому регистрация и GetMessage выполняются здесь.
        """
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        self._running = True
        self._registered_ids.clear()

        # ── Регистрация горячих клавиш ────────────────────────────────────
        for action, hotkey_str in self._hotkeys_config.items():
            hotkey_id: Optional[int] = _ACTION_TO_ID.get(action)
            if hotkey_id is None:
                logger.warning("Неизвестное действие горячей клавиши: %s", action)
                continue

            try:
                modifiers, vk_code = parse_hotkey_string(hotkey_str)
            except ValueError as exc:
                logger.error(
                    "Ошибка парсинга горячей клавиши '%s' для '%s': %s",
                    hotkey_str, action, exc,
                )
                continue

            # MOD_NOREPEAT предотвращает повторные срабатывания при удержании
            result: bool = user32.RegisterHotKey(
                None, hotkey_id, modifiers | MOD_NOREPEAT, vk_code
            )
            if result:
                self._registered_ids.append(hotkey_id)
                logger.info(
                    "Горячая клавиша зарегистрирована: %s → %s (id=%d, mod=0x%X, vk=0x%X)",
                    action, hotkey_str, hotkey_id, modifiers, vk_code,
                )
            else:
                error_code: int = ctypes.GetLastError()
                logger.error(
                    "Не удалось зарегистрировать горячую клавишу '%s' (%s): "
                    "ошибка Windows %d",
                    action, hotkey_str, error_code,
                )

        # ── Цикл обработки сообщений ─────────────────────────────────────
        msg = ctypes.wintypes.MSG()
        while self._running:
            # GetMessage блокирует поток до получения сообщения.
            # Возвращает 0 при WM_QUIT, -1 при ошибке.
            ret: int = user32.GetMessageW(
                ctypes.byref(msg), None, 0, 0
            )
            if ret == 0 or ret == -1:
                break

            if msg.message == WM_HOTKEY:
                hotkey_id_received: int = msg.wParam
                logger.debug(
                    "Получено WM_HOTKEY, id=%d", hotkey_id_received
                )
                self.hotkey_triggered.emit(hotkey_id_received)

        # ── Снятие регистрации ────────────────────────────────────────────
        self._unregister_all(user32)

    def stop(self) -> None:
        """
        Останавливает цикл обработки сообщений.

        Отправляет WM_QUIT в очередь сообщений потока, чтобы
        ``GetMessageW`` вернул 0 и цикл завершился.
        """
        self._running = False
        if self.isRunning():
            # PostThreadMessage отправляет WM_QUIT в очередь нашего потока
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            thread_id: int = int(self.currentThreadId())
            if thread_id:
                user32.PostThreadMessageW(thread_id, 0x0012, 0, 0)  # WM_QUIT

    def _unregister_all(self, user32: ctypes.WinDLL) -> None:
        """
        Снимает регистрацию всех ранее зарегистрированных горячих клавиш.

        Args:
            user32: Загруженная библиотека user32.dll.
        """
        for hk_id in self._registered_ids:
            result: bool = user32.UnregisterHotKey(None, hk_id)
            if result:
                logger.info("Горячая клавиша снята: id=%d", hk_id)
            else:
                logger.warning(
                    "Не удалось снять горячую клавишу: id=%d", hk_id
                )
        self._registered_ids.clear()


class HotkeyManager(QObject):
    """
    Менеджер глобальных горячих клавиш Windows.

    Управляет регистрацией и обработкой системных горячих клавиш.
    При срабатывании запускает ``YandexDiskScreenshotEditor.exe``
    с соответствующим режимом захвата.

    Signals:
        capture_region: Испускается при захвате области экрана.
        capture_fullscreen: Испускается при захвате всего экрана.
        capture_window: Испускается при захвате активного окна.

    Example:
        >>> config = Config()
        >>> config.load()
        >>> manager = HotkeyManager(config)
        >>> manager.start()
        >>> # ... приложение работает ...
        >>> manager.stop()
    """

    capture_region = pyqtSignal()
    capture_fullscreen = pyqtSignal()
    capture_window = pyqtSignal()

    def __init__(self, config: Config, parent: Optional[QObject] = None) -> None:
        """
        Инициализация менеджера горячих клавиш.

        Args:
            config: Объект конфигурации с настройками горячих клавиш.
            parent: Родительский QObject (опционально).
        """
        super().__init__(parent)
        self._config: Config = config
        self._listener_thread: Optional[_HotkeyListenerThread] = None

        # Маппинг ID → (сигнал, действие)
        self._id_to_action: Dict[int, Tuple[pyqtSignal, str]] = {
            HOTKEY_ID_CAPTURE_REGION: (self.capture_region, "capture_region"),
            HOTKEY_ID_CAPTURE_FULLSCREEN: (self.capture_fullscreen, "capture_fullscreen"),
            HOTKEY_ID_CAPTURE_WINDOW: (self.capture_window, "capture_window"),
        }

    def start(self) -> None:
        """
        Запускает прослушивание глобальных горячих клавиш.

        Создаёт фоновый поток ``_HotkeyListenerThread``, который
        регистрирует горячие клавиши через WinAPI ``RegisterHotKey``
        и слушает ``WM_HOTKEY`` сообщения.

        Если горячие клавиши отключены в конфигурации
        (``config.hotkeys_enabled == False``), метод ничего не делает.

        При повторном вызове без предварительного ``stop()``
        сначала останавливает предыдущий поток.
        """
        if not self._config.hotkeys_enabled:
            logger.info("Горячие клавиши отключены в конфигурации")
            return

        # Остановить предыдущий поток, если он работает
        if self._listener_thread is not None and self._listener_thread.isRunning():
            logger.info("Перезапуск потока горячих клавиш")
            self.stop()

        hotkeys_config: dict = self._config.hotkeys
        if not hotkeys_config:
            logger.warning("Конфигурация горячих клавиш пуста")
            return

        self._listener_thread = _HotkeyListenerThread(hotkeys_config, self)
        self._listener_thread.hotkey_triggered.connect(self._on_hotkey_triggered)
        self._listener_thread.finished.connect(self._on_thread_finished)
        self._listener_thread.start()

        logger.info("Менеджер горячих клавиш запущен")

    def stop(self) -> None:
        """
        Останавливает прослушивание горячих клавиш и освобождает ресурсы.

        Отправляет WM_QUIT в поток-слушатель, ожидает его завершения
        (до 5 секунд) и корректно отключает сигналы.
        """
        if self._listener_thread is None:
            return

        if self._listener_thread.isRunning():
            self._listener_thread.stop()

            # Ожидание завершения потока (макс. 5 секунд)
            if not self._listener_thread.wait(5000):
                logger.warning(
                    "Поток горячих клавиш не завершился за 5 секунд, "
                    "принудительное завершение"
                )
                self._listener_thread.terminate()
                self._listener_thread.wait(2000)

        try:
            self._listener_thread.hotkey_triggered.disconnect(self._on_hotkey_triggered)
        except (TypeError, RuntimeError):
            pass  # Сигнал уже отключён или объект удалён

        try:
            self._listener_thread.finished.disconnect(self._on_thread_finished)
        except (TypeError, RuntimeError):
            pass

        self._listener_thread = None
        logger.info("Менеджер горячих клавиш остановлен")

    def _on_hotkey_triggered(self, hotkey_id: int) -> None:
        """
        Обработчик срабатывания горячей клавиши.

        Вызывается из основного потока Qt (через механизм signal/slot
        с автоматическим маршалингом между потоками).

        Args:
            hotkey_id: Идентификатор сработавшей горячей клавиши.
        """
        action_info: Optional[Tuple[pyqtSignal, str]] = self._id_to_action.get(hotkey_id)
        if action_info is None:
            logger.warning("Получен неизвестный hotkey_id: %d", hotkey_id)
            return

        signal, action_name = action_info
        logger.info("Горячая клавиша сработала: %s (id=%d)", action_name, hotkey_id)

        # Испускаем сигнал для внешних подписчиков
        signal.emit()

        # Запускаем редактор скриншотов
        self._launch_screenshot_editor(action_name)

    def _on_thread_finished(self) -> None:
        """Обработчик завершения фонового потока."""
        logger.debug("Поток горячих клавиш завершил работу")

    def _launch_screenshot_editor(self, mode: str) -> None:
        """
        Запускает YandexDiskScreenshotEditor.exe с указанным режимом захвата.

        Args:
            mode: Режим захвата экрана. Допустимые значения:
                  ``'capture_region'``, ``'capture_fullscreen'``, ``'capture_window'``.

        Если исполняемый файл не найден, показывает предупреждение
        пользователю через ``QMessageBox``.
        """
        if not os.path.isfile(YANDEX_SCREENSHOT_EDITOR):
            logger.error(
                "Редактор скриншотов не найден: %s", YANDEX_SCREENSHOT_EDITOR
            )
            QMessageBox.warning(
                None,
                "Редактор скриншотов не найден",
                f"Файл не найден:\n{YANDEX_SCREENSHOT_EDITOR}\n\n"
                "Убедитесь, что Яндекс.Диск установлен и путь к редактору "
                "скриншотов указан верно.",
            )
            return

        args: list[str] = _EDITOR_ARGS.get(mode, [])
        cmd: list[str] = [YANDEX_SCREENSHOT_EDITOR] + args

        logger.info("Запуск редактора скриншотов: %s", " ".join(cmd))

        try:
            subprocess.Popen(
                cmd,
                creationflags=subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NO_WINDOW,
            )
        except OSError as exc:
            logger.error("Ошибка запуска редактора скриншотов: %s", exc)
            QMessageBox.critical(
                None,
                "Ошибка запуска",
                f"Не удалось запустить редактор скриншотов:\n{exc}",
            )
        except Exception as exc:
            logger.error(
                "Непредвиденная ошибка при запуске редактора: %s", exc
            )
            QMessageBox.critical(
                None,
                "Ошибка",
                f"Непредвиденная ошибка:\n{exc}",
            )

    @property
    def is_running(self) -> bool:
        """Возвращает ``True``, если поток-слушатель активен."""
        return (
            self._listener_thread is not None
            and self._listener_thread.isRunning()
        )
