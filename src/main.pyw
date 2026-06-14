# -*- coding: utf-8 -*-
"""
Точка входа приложения Google Drive Sync.

Порядок инициализации:
    1.  Логирование (файл + консоль)
    2.  Single-instance через QSharedMemory
    3.  QApplication
    4.  Глобальный обработчик исключений
    5.  Загрузка конфигурации
    6.  Применение темы оформления
    7.  База данных
    8.  Авторизация Google
    9.  Drive API
    10. Движок синхронизации
    11. Горячие клавиши
    12. Главное окно
    13. Системный трей
    14. Подключение сигналов
    15. Запуск всех подсистем
    16. Цикл событий Qt
"""

import sys
import os
import logging
import traceback
import ctypes
from typing import Optional

from PyQt6.QtWidgets import QApplication, QMessageBox, QDialogButtonBox
from PyQt6.QtCore import QSharedMemory, Qt
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QPainterPath, QFont

from config import Config
from database import Database
from auth_manager import AuthManager
from drive_api import DriveAPI
from sync_engine import SyncEngine
from hotkeys import HotkeyManager
from tray_manager import TrayManager, draw_yellow_saucer
from main_window import MainWindow
from settings_dialog import SettingsDialog

# ── Константы ────────────────────────────────────────────────────────────────

APP_NAME = 'Google Drive Sync'
APP_ID = 'com.googledrivesync.desktop'
SHARED_MEMORY_KEY = 'GoogleDriveSync'

LOG_DIR = os.path.join(
    os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
    'GoogleDriveSync',
)
LOG_FILE = os.path.join(LOG_DIR, 'app.log')

# ── Палитры тем ──────────────────────────────────────────────────────────────

_DARK_STYLESHEET = """
QWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
    font-family: "Segoe UI", "Noto Sans", sans-serif;
    font-size: 13px;
}
QMainWindow {
    background-color: #1e1e2e;
}
QMenuBar {
    background-color: #181825;
    color: #cdd6f4;
    border-bottom: 1px solid #313244;
}
QMenuBar::item:selected {
    background-color: #313244;
}
QMenu {
    background-color: #1e1e2e;
    color: #cdd6f4;
    border: 1px solid #313244;
}
QMenu::item:selected {
    background-color: #89b4fa;
    color: #1e1e2e;
}
QToolBar {
    background-color: #181825;
    border: none;
    spacing: 4px;
}
QPushButton {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 6px 16px;
}
QPushButton:hover {
    background-color: #45475a;
    border-color: #89b4fa;
}
QPushButton:pressed {
    background-color: #585b70;
}
QLineEdit, QTextEdit, QPlainTextEdit {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 6px;
    selection-background-color: #89b4fa;
    selection-color: #1e1e2e;
}
QLineEdit:focus, QTextEdit:focus {
    border-color: #89b4fa;
}
QComboBox {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 6px;
}
QComboBox:hover {
    border-color: #89b4fa;
}
QComboBox QAbstractItemView {
    background-color: #313244;
    color: #cdd6f4;
    selection-background-color: #89b4fa;
    selection-color: #1e1e2e;
}
QTabWidget::pane {
    border: 1px solid #45475a;
    background-color: #1e1e2e;
}
QTabBar::tab {
    background-color: #313244;
    color: #a6adc8;
    border: 1px solid #45475a;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 6px 14px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background-color: #1e1e2e;
    color: #89b4fa;
}
QScrollBar:vertical {
    background: #313244;
    width: 10px;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background: #585b70;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar:horizontal {
    background: #313244;
    height: 10px;
    border-radius: 5px;
}
QScrollBar::handle:horizontal {
    background: #585b70;
    border-radius: 5px;
    min-width: 30px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}
QProgressBar {
    background-color: #313244;
    border: 1px solid #45475a;
    border-radius: 6px;
    text-align: center;
    color: #cdd6f4;
}
QProgressBar::chunk {
    background-color: #89b4fa;
    border-radius: 5px;
}
QTreeView, QListView, QTableView {
    background-color: #313244;
    alternate-background-color: #181825;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {
    background-color: #89b4fa;
    color: #1e1e2e;
}
QHeaderView::section {
    background-color: #45475a;
    color: #cdd6f4;
    padding: 6px;
    border: none;
}
QStatusBar {
    background-color: #181825;
    color: #a6adc8;
    border-top: 1px solid #313244;
}
QToolTip {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 4px;
    padding: 4px;
}
QGroupBox {
    border: 1px solid #45475a;
    border-radius: 8px;
    margin-top: 14px;
    padding-top: 16px;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 16px;
    padding: 0 6px;
}
QSplitter::handle {
    background-color: #45475a;
}
QCheckBox::indicator, QRadioButton::indicator {
    width: 16px;
    height: 16px;
}
QCheckBox::indicator {
    border-radius: 4px;
    border: 2px solid #585b70;
    background-color: #313244;
}
QCheckBox::indicator:checked {
    background-color: #89b4fa;
    border-color: #89b4fa;
}
QRadioButton::indicator {
    border-radius: 8px;
    border: 2px solid #585b70;
    background-color: #313244;
}
QRadioButton::indicator:checked {
    background-color: #89b4fa;
    border-color: #89b4fa;
}
FileCard {
    background-color: #313244;
    border-radius: 8px;
    border: 1px solid transparent;
}
FileCard:hover {
    border: 1px solid #585b70;
    background-color: #45475a;
}
FileCard[selected="true"] {
    background-color: #45475a;
    border: 1px solid #89b4fa;
}
FileCard QLabel {
    color: #cdd6f4;
}
"""

_LIGHT_STYLESHEET = """
QWidget {
    background-color: #f4f5f6;
    color: #2e303f;
    font-family: "Segoe UI", "Noto Sans", sans-serif;
    font-size: 13px;
}
QMainWindow {
    background-color: #f4f5f6;
}
QMenuBar {
    background-color: #e6e8eb;
    color: #2e303f;
    border-bottom: 1px solid #dcdde2;
}
QMenuBar::item:selected {
    background-color: #ccd0da;
}
QMenu {
    background-color: #f4f5f6;
    color: #2e303f;
    border: 1px solid #dcdde2;
}
QMenu::item:selected {
    background-color: #1e66f5;
    color: #f4f5f6;
}
QToolBar {
    background-color: #e6e8eb;
    border: none;
    spacing: 4px;
}
QPushButton {
    background-color: #ffffff;
    color: #2e303f;
    border: 1px solid #dcdde2;
    border-radius: 6px;
    padding: 6px 16px;
}
QPushButton:hover {
    background-color: #e6e8eb;
    border-color: #1e66f5;
}
QPushButton:pressed {
    background-color: #ccd0da;
}
QLineEdit, QTextEdit, QPlainTextEdit {
    background-color: #ffffff;
    color: #2e303f;
    border: 1px solid #dcdde2;
    border-radius: 6px;
    padding: 6px;
    selection-background-color: #1e66f5;
    selection-color: #f4f5f6;
    placeholder-text-color: #8c8e9f;
}
QLineEdit:focus, QTextEdit:focus {
    border-color: #1e66f5;
}
QComboBox {
    background-color: #ffffff;
    color: #2e303f;
    border: 1px solid #dcdde2;
    border-radius: 6px;
    padding: 6px;
}
QComboBox:hover {
    border-color: #1e66f5;
}
QComboBox QAbstractItemView {
    background-color: #ffffff;
    color: #2e303f;
    selection-background-color: #1e66f5;
    selection-color: #f4f5f6;
}
QTabWidget::pane {
    border: 1px solid #dcdde2;
    background-color: #f4f5f6;
}
QTabBar::tab {
    background-color: #e6e8eb;
    color: #5c5e70;
    border: 1px solid #dcdde2;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 6px 14px;
    margin-right: 2px;
}
QTabBar::tab:selected {
    background-color: #f4f5f6;
    color: #1e66f5;
}
QScrollBar:vertical {
    background: #e6e8eb;
    width: 10px;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background: #ccd0da;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar:horizontal {
    background: #e6e8eb;
    height: 10px;
    border-radius: 5px;
}
QScrollBar::handle:horizontal {
    background: #ccd0da;
    border-radius: 5px;
    min-width: 30px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0px;
}
QProgressBar {
    background-color: #e6e8eb;
    border: 1px solid #dcdde2;
    border-radius: 6px;
    text-align: center;
    color: #2e303f;
}
QProgressBar::chunk {
    background-color: #1e66f5;
    border-radius: 5px;
}
QTreeView, QListView, QTableView {
    background-color: #ffffff;
    alternate-background-color: #f4f5f6;
    color: #2e303f;
    border: 1px solid #dcdde2;
    border-radius: 6px;
}
QTreeView::item:selected, QListView::item:selected, QTableView::item:selected {
    background-color: #1e66f5;
    color: #f4f5f6;
}
QHeaderView::section {
    background-color: #e6e8eb;
    color: #2e303f;
    padding: 6px;
    border: none;
}
QStatusBar {
    background-color: #e6e8eb;
    color: #5c5e70;
    border-top: 1px solid #dcdde2;
}
QToolTip {
    background-color: #e6e8eb;
    color: #2e303f;
    border: 1px solid #dcdde2;
    border-radius: 4px;
    padding: 4px;
}
QGroupBox {
    border: 1px solid #dcdde2;
    border-radius: 8px;
    margin-top: 14px;
    padding-top: 16px;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 16px;
    padding: 0 6px;
}
QSplitter::handle {
    background-color: #dcdde2;
}
QCheckBox::indicator, QRadioButton::indicator {
    width: 16px;
    height: 16px;
}
QCheckBox::indicator {
    border-radius: 4px;
    border: 2px solid #ccd0da;
    background-color: #ffffff;
}
QCheckBox::indicator:checked {
    background-color: #1e66f5;
    border-color: #1e66f5;
}
QRadioButton::indicator {
    border-radius: 8px;
    border: 2px solid #ccd0da;
    background-color: #ffffff;
}
QRadioButton::indicator:checked {
    background-color: #1e66f5;
    border-color: #1e66f5;
}
FileCard {
    background-color: #ffffff;
    border-radius: 8px;
    border: 1px solid #dcdde2;
}
FileCard:hover {
    border: 1px solid #ccd0da;
    background-color: #f4f5f6;
}
FileCard[selected="true"] {
    background-color: #e6e8eb;
    border: 1px solid #1e66f5;
}
FileCard QLabel {
    color: #2e303f;
}
"""


# ═════════════════════════════════════════════════════════════════════════════
#  Логирование
# ═════════════════════════════════════════════════════════════════════════════

def setup_logging() -> None:
    """
    Настроить логирование в файл и консоль.

    Файл лога: %LOCALAPPDATA%\\GoogleDriveSync\\app.log
    Формат: дата — уровень — модуль — сообщение
    """
    os.makedirs(LOG_DIR, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Формат
    fmt = logging.Formatter(
        '%(asctime)s  [%(levelname)-7s]  %(name)-20s  %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    # Файловый обработчик (ротация не нужна для MVP)
    file_handler = logging.FileHandler(LOG_FILE, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    root_logger.addHandler(file_handler)

    # Консольный обработчик
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(fmt)
    root_logger.addHandler(console_handler)


# ═════════════════════════════════════════════════════════════════════════════
#  Определение темы ОС
# ═════════════════════════════════════════════════════════════════════════════

def _is_windows_dark_theme() -> bool:
    """
    Определить, используется ли тёмная тема Windows.

    Проверяет ключ реестра AppsUseLightTheme.

    Returns:
        True, если Windows использует тёмную тему.
    """
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize',
            0,
            winreg.KEY_READ,
        )
        try:
            value, _ = winreg.QueryValueEx(key, 'AppsUseLightTheme')
            return value == 0
        finally:
            winreg.CloseKey(key)
    except Exception:
        return True  # По умолчанию считаем тёмную


def apply_theme(theme_name: str) -> None:
    """
    Применить тему оформления к QApplication.

    Определяет тему (system/dark/light) и устанавливает
    соответствующий QSS stylesheet.

    Args:
        theme_name: Имя темы ('system', 'dark', 'light').
    """
    app = QApplication.instance()
    if app is None:
        return

    if theme_name == 'system':
        use_dark = _is_windows_dark_theme()
    elif theme_name == 'dark':
        use_dark = True
    else:
        use_dark = False

    stylesheet = _DARK_STYLESHEET if use_dark else _LIGHT_STYLESHEET
    app.setStyleSheet(stylesheet)

    logger.info("Тема применена: %s (dark=%s)", theme_name, use_dark)


# ═════════════════════════════════════════════════════════════════════════════
#  Иконка приложения (генерируется программно)
# ═════════════════════════════════════════════════════════════════════════════

def _create_app_icon() -> QIcon:
    """
    Создать иконку приложения — жёлтая летающая тарелка (бананового цвета).

    Returns:
        QIcon с логотипом приложения.
    """
    size = 128
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(0, 0, 0, 0))

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    draw_yellow_saucer(painter, size)

    painter.end()
    return QIcon(pixmap)


# ═════════════════════════════════════════════════════════════════════════════
#  Глобальный обработчик исключений
# ═════════════════════════════════════════════════════════════════════════════

logger = logging.getLogger(__name__)


def _global_exception_handler(
    exc_type: type,
    exc_value: BaseException,
    exc_tb: object,
) -> None:
    """
    Глобальный обработчик необработанных исключений.

    Логирует трейсбек и показывает QMessageBox пользователю.

    Args:
        exc_type: Тип исключения.
        exc_value: Экземпляр исключения.
        exc_tb: Traceback объект.
    """
    # Не перехватываем KeyboardInterrupt
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return

    tb_text = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
    logger.critical("Необработанное исключение:\n%s", tb_text)

    try:
        app = QApplication.instance()
        if app:
            msg = QMessageBox()
            msg.setWindowTitle('Критическая ошибка — Google Drive Sync')
            msg.setIcon(QMessageBox.Icon.Critical)
            msg.setText('Произошла непредвиденная ошибка.')
            msg.setInformativeText(str(exc_value))
            msg.setDetailedText(tb_text)
            msg.exec()
    except Exception:
        pass  # Если GUI недоступен, только логируем


# ═════════════════════════════════════════════════════════════════════════════
#  Открытие диалога настроек (фабрика)
# ═════════════════════════════════════════════════════════════════════════════

def open_settings_dialog(
    config: Config,
    auth_manager: AuthManager,
    main_window: Optional[MainWindow] = None,
) -> None:
    """
    Создать и показать диалог настроек.

    Если пользователь вышел из аккаунта (код 2), завершает приложение.

    Args:
        config: Конфигурация приложения.
        auth_manager: Менеджер авторизации.
        main_window: Главное окно (для привязки модальности).
    """
    dialog = SettingsDialog(config, auth_manager, main_window)
    result = dialog.exec()

    if result == 2:
        # Пользователь вышел из аккаунта → завершение
        app = QApplication.instance()
        if app:
            app.quit()
    elif result == QDialogButtonBox.StandardButton.Ok.value:
        # Настройки применены — обновляем тему
        apply_theme(config.theme)
        if main_window:
            main_window.apply_theme_styles(config.theme)


# ═════════════════════════════════════════════════════════════════════════════
#  Точка входа
# ═════════════════════════════════════════════════════════════════════════════

def main() -> int:
    """
    Главная функция приложения Google Drive Sync.

    Инициализирует все компоненты в правильном порядке,
    подключает сигналы и запускает цикл событий Qt.

    Returns:
        Код завершения приложения.
    """
    # 1. Логирование
    setup_logging()
    logger.info("=" * 60)
    logger.info("Запуск Google Drive Sync")
    logger.info("=" * 60)

    # 2. Single-instance через QSharedMemory
    #    Создаём QApplication перед QSharedMemory, т.к. QSharedMemory — QObject
    app = QApplication(sys.argv)

    shared_memory = QSharedMemory(SHARED_MEMORY_KEY)
    if not shared_memory.create(1):
        # Приложение уже запущено
        logger.warning("Обнаружен другой экземпляр приложения. Завершение.")
        QMessageBox.warning(
            None,
            APP_NAME,
            'Google Drive Sync уже запущен.\n\n'
            'Приложение работает в системном трее.',
        )
        return 1

    # 3. Установить AppUserModelID для корректного отображения в панели задач Windows
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass

    # 4. Глобальный обработчик исключений
    sys.excepthook = _global_exception_handler

    # 5. Загрузка конфигурации
    config = Config()
    # config.load() вызывается в __init__ Config

    # 6. Применение темы
    app.setWindowIcon(_create_app_icon())
    apply_theme(config.theme)

    # 7. База данных
    database = Database()

    # 8. Авторизация
    auth_manager = AuthManager(config)
    if not auth_manager.is_authenticated:
        logger.info("Пользователь не авторизован. Запускаем OAuth flow...")
        if not auth_manager.authenticate():
            logger.error("Авторизация не выполнена. Завершение.")
            QMessageBox.critical(
                None,
                APP_NAME,
                'Не удалось авторизоваться в Google.\n\n'
                'Проверьте подключение к интернету и наличие\n'
                'файла credentials.json в папке приложения.',
            )
            return 1

    # 9. Drive API
    drive_api = DriveAPI(auth_manager)

    # 10. Движок синхронизации
    sync_engine = SyncEngine(config, drive_api, database)

    # 11. Горячие клавиши
    hotkey_manager = HotkeyManager(config)

    # 12. Главное окно
    main_window = MainWindow(sync_engine, drive_api, config, database, auth_manager)

    # 13. Системный трей
    tray = TrayManager(sync_engine, config, auth_manager)

    # 14. Подключение сигналов

    # Трей → Главное окно
    tray.show_main_window.connect(lambda: _show_main_window(main_window, tray))
    tray.hide_main_window.connect(lambda: _hide_main_window(main_window, tray))

    # Трей → Настройки
    tray.open_settings.connect(
        lambda: open_settings_dialog(config, auth_manager, main_window)
    )

    # Главное окно → Настройки
    main_window.open_settings_requested.connect(
        lambda: open_settings_dialog(config, auth_manager, main_window)
    )

    # Главное окно → Выход (Logout)
    main_window.logout_requested.connect(lambda: _cleanup_and_quit(
        sync_engine, hotkey_manager, tray, database,
    ))

    # Трей → Выход
    tray.quit_app.connect(lambda: _cleanup_and_quit(
        sync_engine, hotkey_manager, tray, database,
    ))

    def _resume_or_start():
        if not sync_engine.is_running:
            sync_engine.start()
        else:
            sync_engine.resume()

    # Трей → Пауза / Возобновление
    tray.pause_sync.connect(sync_engine.pause)
    tray.resume_sync.connect(_resume_or_start)

    # SyncEngine → Трей (статус)
    sync_engine.status_changed.connect(tray.update_status)

    # 15. Запуск подсистем
    sync_engine.start()
    logger.info("Движок синхронизации запущен автоматически на старте")

    if config.hotkeys_enabled:
        hotkey_manager.start()
        logger.info("Горячие клавиши активированы")

    # 16. Показать трей
    tray.show()
    logger.info("Иконка трея показана")

    # Обновить информацию о хранилище в трее
    user_info = auth_manager.get_user_info()
    if user_info:
        tray.update_storage(user_info.storage_used_gb, user_info.storage_total_gb)

    # Не выходить при закрытии последнего окна
    app.setQuitOnLastWindowClosed(False)

    logger.info("Приложение готово к работе")

    # 17. Запуск цикла событий
    exit_code = app.exec()

    logger.info("Приложение завершается с кодом %d", exit_code)
    return exit_code


# ═════════════════════════════════════════════════════════════════════════════
#  Вспомогательные функции для сигналов
# ═════════════════════════════════════════════════════════════════════════════

def _show_main_window(window: MainWindow, tray: TrayManager) -> None:
    """
    Показать и поднять главное окно приложения.

    Args:
        window: Экземпляр главного окна.
        tray: Менеджер трея для обновления состояния видимости.
    """
    window.show()
    window.raise_()
    window.activateWindow()
    tray.set_main_window_visible(True)


def _hide_main_window(window: MainWindow, tray: TrayManager) -> None:
    """
    Скрыть главное окно приложения.

    Args:
        window: Экземпляр главного окна.
        tray: Менеджер трея для обновления состояния видимости.
    """
    window.hide()
    tray.set_main_window_visible(False)


def _cleanup_and_quit(
    sync_engine: SyncEngine,
    hotkey_manager: HotkeyManager,
    tray: TrayManager,
    database: Database,
) -> None:
    """
    Корректно завершить все подсистемы и выйти.

    Args:
        sync_engine: Движок синхронизации.
        hotkey_manager: Менеджер горячих клавиш.
        tray: Менеджер трея.
        database: База данных.
    """
    logger.info("Начинаем завершение приложения...")

    # Остановка синхронизации
    try:
        sync_engine.stop()
        logger.info("Движок синхронизации остановлен")
    except Exception as e:
        logger.error("Ошибка остановки sync_engine: %s", e)

    # Остановка горячих клавиш
    try:
        hotkey_manager.stop()
        logger.info("Горячие клавиши отключены")
    except Exception as e:
        logger.error("Ошибка остановки hotkey_manager: %s", e)

    # Скрыть трей
    try:
        tray.hide()
        logger.info("Иконка трея скрыта")
    except Exception as e:
        logger.error("Ошибка скрытия трея: %s", e)

    # Закрыть базу данных
    try:
        database.close()
        logger.info("База данных закрыта")
    except Exception as e:
        logger.error("Ошибка закрытия БД: %s", e)

    # Завершение Qt
    app = QApplication.instance()
    if app:
        app.quit()

    logger.info("Приложение завершено")


# ═════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    sys.exit(main())
