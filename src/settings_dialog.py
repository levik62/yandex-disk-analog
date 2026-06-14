# -*- coding: utf-8 -*-
"""
Диалог настроек Google Drive Sync.

Шесть вкладок:
    1. Общие      — автозапуск, папка синхронизации, тема оформления
    2. Синхронизация — интервал проверки, избирательная синхронизация
    3. Уведомления — чекбоксы для типов уведомлений
    4. Горячие клавиши — включение и отображение текущих привязок
    5. Прокси      — выбор режима и ручная настройка прокси
    6. Аккаунт     — информация о пользователе, хранилище, выход

Стилизация: тёмная тема Catppuccin Mocha (#1e1e2e / #cdd6f4 / #89b4fa).
"""

import os
import sys
import logging
import winreg
from typing import Optional, List

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap, QIcon, QFont, QPainter, QColor, QPainterPath
from PyQt6.QtWidgets import (
    QDialog, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QGridLayout, QGroupBox,
    QCheckBox, QRadioButton, QButtonGroup,
    QLabel, QLineEdit, QPushButton, QComboBox,
    QFileDialog, QTreeWidget, QTreeWidgetItem,
    QProgressBar, QSpacerItem, QSizePolicy,
    QDialogButtonBox, QMessageBox, QApplication,
)

from config import Config
from auth_manager import AuthManager

logger = logging.getLogger(__name__)

# ── Палитра Catppuccin Mocha ─────────────────────────────────────────────────
_BG_BASE = '#1e1e2e'
_BG_SURFACE0 = '#313244'
_BG_SURFACE1 = '#45475a'
_BG_SURFACE2 = '#585b70'
_BG_MANTLE = '#181825'
_TEXT = '#cdd6f4'
_TEXT_DIM = '#a6adc8'
_TEXT_MUTED = '#6c7086'
_ACCENT = '#89b4fa'
_ACCENT_HOVER = '#b4d0fb'
_GREEN = '#a6e3a1'
_RED = '#f38ba8'
_YELLOW = '#f9e2af'
_BORDER = '#45475a'
_SCROLLBAR_BG = '#313244'
_SCROLLBAR_HANDLE = '#585b70'

# Реестр Windows — автозапуск
_AUTOSTART_KEY_PATH = r'Software\Microsoft\Windows\CurrentVersion\Run'
_AUTOSTART_VALUE_NAME = 'GoogleDriveSync'


def _is_windows_dark_theme() -> bool:
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
        return True

def _build_stylesheet(theme: str) -> str:
    """Собрать CSS-стили для диалога настроек."""
    if theme == 'system':
        use_dark = _is_windows_dark_theme()
    elif theme == 'dark':
        use_dark = True
    else:
        use_dark = False

    if use_dark:
        _BG_BASE = '#1e1e2e'
        _BG_SURFACE0 = '#313244'
        _BG_SURFACE1 = '#45475a'
        _BG_SURFACE2 = '#585b70'
        _BG_MANTLE = '#181825'
        _TEXT = '#cdd6f4'
        _TEXT_DIM = '#a6adc8'
        _TEXT_MUTED = '#6c7086'
        _ACCENT = '#89b4fa'
        _ACCENT_HOVER = '#b4d0fb'
        _GREEN = '#a6e3a1'
        _RED = '#f38ba8'
        _YELLOW = '#f9e2af'
        _BORDER = '#45475a'
    else:
        _BG_BASE = '#eff1f5'
        _BG_SURFACE0 = '#ccd0da'
        _BG_SURFACE1 = '#bcc0cc'
        _BG_SURFACE2 = '#acb0be'
        _BG_MANTLE = '#e6e9ef'
        _TEXT = '#4c4f69'
        _TEXT_DIM = '#6c6f85'
        _TEXT_MUTED = '#9ca0b0'
        _ACCENT = '#1e66f5'
        _ACCENT_HOVER = '#3b79f7'
        _GREEN = '#40a02b'
        _RED = '#d20f39'
        _YELLOW = '#df8e1d'
        _BORDER = '#ccd0da'

    return f"""
    QDialog {{
        background-color: {_BG_BASE};
        color: {_TEXT};
    }}
    QTabWidget::pane {{
        border: 1px solid {_BORDER};
        border-radius: 6px;
        background-color: {_BG_BASE};
        top: -1px;
    }}
    QTabBar::tab {{
        background-color: {_BG_SURFACE0};
        color: {_TEXT_DIM};
        border: 1px solid {_BORDER};
        border-bottom: none;
        border-top-left-radius: 6px;
        border-top-right-radius: 6px;
        padding: 8px 18px;
        margin-right: 2px;
        font-size: 13px;
    }}
    QTabBar::tab:selected {{
        background-color: {_BG_BASE};
        color: {_ACCENT};
        border-bottom: 2px solid {_ACCENT};
    }}
    QTabBar::tab:hover:!selected {{
        background-color: {_BG_SURFACE1};
        color: {_TEXT};
    }}
    QGroupBox {{
        font-weight: bold;
        font-size: 13px;
        color: {_TEXT};
        border: 1px solid {_BORDER};
        border-radius: 8px;
        margin-top: 14px;
        padding: 16px 12px 12px 12px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 16px;
        padding: 0 6px;
    }}
    QLabel {{
        color: {_TEXT};
        font-size: 13px;
    }}
    QCheckBox {{
        color: {_TEXT};
        spacing: 8px;
        font-size: 13px;
    }}
    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border-radius: 4px;
        border: 2px solid {_BG_SURFACE2};
        background-color: {_BG_SURFACE0};
    }}
    QCheckBox::indicator:checked {{
        background-color: {_ACCENT};
        border-color: {_ACCENT};
    }}
    QCheckBox::indicator:hover {{
        border-color: {_ACCENT};
    }}
    QRadioButton {{
        color: {_TEXT};
        spacing: 8px;
        font-size: 13px;
    }}
    QRadioButton::indicator {{
        width: 16px;
        height: 16px;
        border-radius: 8px;
        border: 2px solid {_BG_SURFACE2};
        background-color: {_BG_SURFACE0};
    }}
    QRadioButton::indicator:checked {{
        background-color: {_ACCENT};
        border-color: {_ACCENT};
    }}
    QRadioButton::indicator:hover {{
        border-color: {_ACCENT};
    }}
    QLineEdit {{
        background-color: {_BG_SURFACE0};
        color: {_TEXT};
        border: 1px solid {_BORDER};
        border-radius: 6px;
        padding: 6px 10px;
        font-size: 13px;
        selection-background-color: {_ACCENT};
    }}
    QLineEdit:focus {{
        border-color: {_ACCENT};
    }}
    QLineEdit:disabled {{
        background-color: {_BG_MANTLE};
        color: {_TEXT_MUTED};
    }}
    QComboBox {{
        background-color: {_BG_SURFACE0};
        color: {_TEXT};
        border: 1px solid {_BORDER};
        border-radius: 6px;
        padding: 6px 10px;
        font-size: 13px;
        min-width: 120px;
    }}
    QComboBox:hover {{
        border-color: {_ACCENT};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 24px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {_BG_SURFACE0};
        color: {_TEXT};
        border: 1px solid {_BORDER};
        selection-background-color: {_ACCENT};
        selection-color: {_BG_BASE};
    }}
    QPushButton {{
        background-color: {_BG_SURFACE0};
        color: {_TEXT};
        border: 1px solid {_BORDER};
        border-radius: 6px;
        padding: 7px 18px;
        font-size: 13px;
    }}
    QPushButton:hover {{
        background-color: {_BG_SURFACE1};
        border-color: {_ACCENT};
    }}
    QPushButton:pressed {{
        background-color: {_BG_SURFACE2};
    }}
    QPushButton#btn_accent {{
        background-color: {_ACCENT};
        color: {_BG_BASE};
        border: none;
        font-weight: bold;
    }}
    QPushButton#btn_accent:hover {{
        background-color: {_ACCENT_HOVER};
    }}
    QPushButton#btn_danger {{
        background-color: transparent;
        color: {_RED};
        border: 1px solid {_RED};
    }}
    QPushButton#btn_danger:hover {{
        background-color: {_RED};
        color: {_BG_BASE};
    }}
    QTreeWidget {{
        background-color: {_BG_SURFACE0};
        color: {_TEXT};
        border: 1px solid {_BORDER};
        border-radius: 6px;
        font-size: 13px;
        alternate-background-color: {_BG_MANTLE};
    }}
    QTreeWidget::item {{
        padding: 4px;
    }}
    QTreeWidget::item:selected {{
        background-color: {_ACCENT};
        color: {_BG_BASE};
    }}
    QTreeWidget::indicator {{
        width: 18px;
        height: 18px;
        border-radius: 4px;
        border: 2px solid {_BG_SURFACE2};
        background-color: {_BG_SURFACE0};
    }}
    QTreeWidget::indicator:checked {{
        background-color: {_ACCENT};
        border-color: {_ACCENT};
    }}
    QHeaderView::section {{
        background-color: {_BG_SURFACE1};
        color: {_TEXT};
        padding: 6px;
        border: none;
        font-weight: bold;
        font-size: 12px;
    }}
    QProgressBar {{
        background-color: {_BG_SURFACE0};
        border: 1px solid {_BORDER};
        border-radius: 6px;
        text-align: center;
        color: {_TEXT};
        font-size: 12px;
        min-height: 20px;
    }}
    QProgressBar::chunk {{
        background-color: {_ACCENT};
        border-radius: 5px;
    }}
    QScrollBar:vertical {{
        background: {_SCROLLBAR_BG};
        width: 10px;
        border-radius: 5px;
    }}
    QScrollBar::handle:vertical {{
        background: {_SCROLLBAR_HANDLE};
        border-radius: 5px;
        min-height: 30px;
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    QDialogButtonBox QPushButton {{
        min-width: 90px;
    }}
    """


class SettingsDialog(QDialog):
    """
    Диалог настроек приложения Google Drive Sync.

    Содержит шесть вкладок с настройками, кнопки OK / Отмена / Применить.
    Загружает текущие значения из Config, применяет при сохранении.
    """

    def __init__(
        self,
        config: Config,
        auth_manager: AuthManager,
        parent: Optional[QWidget] = None,
    ) -> None:
        """
        Инициализация диалога настроек.

        Args:
            config: Экземпляр конфигурации приложения.
            auth_manager: Менеджер авторизации для вкладки «Аккаунт».
            parent: Родительский виджет.
        """
        super().__init__(parent)
        self._config = config
        self._auth_manager = auth_manager

        self.setWindowTitle('Настройки — Google Drive Sync')
        self.setMinimumSize(620, 520)
        self.resize(680, 560)
        self.setStyleSheet(_build_stylesheet(config.theme))
        self.setWindowFlags(
            self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint
        )

        self._build_ui()
        self._load_values()

    # =========================================================================
    #  Построение UI
    # =========================================================================

    def _build_ui(self) -> None:
        """Создать виджеты и разместить их в layout-ах."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(10)

        # --- Вкладки ---
        self._tabs = QTabWidget()
        self._tabs.addTab(self._create_general_tab(), '  Общие  ')
        self._tabs.addTab(self._create_sync_tab(), '  Синхронизация  ')
        self._tabs.addTab(self._create_notifications_tab(), '  Уведомления  ')
        self._tabs.addTab(self._create_hotkeys_tab(), '  Горячие клавиши  ')
        self._tabs.addTab(self._create_proxy_tab(), '  Прокси  ')
        self._tabs.addTab(self._create_account_tab(), '  Аккаунт  ')
        main_layout.addWidget(self._tabs)

        # --- Кнопки OK / Отмена / Применить ---
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply
        )
        btn_box.button(QDialogButtonBox.StandardButton.Ok).setText('OK')
        btn_box.button(QDialogButtonBox.StandardButton.Cancel).setText('Отмена')
        btn_box.button(QDialogButtonBox.StandardButton.Apply).setText('Применить')

        btn_ok = btn_box.button(QDialogButtonBox.StandardButton.Ok)
        btn_ok.setObjectName('btn_accent')

        btn_box.accepted.connect(self._on_ok)
        btn_box.rejected.connect(self.reject)
        btn_box.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(
            self._on_apply
        )
        main_layout.addWidget(btn_box)

    # ── Вкладка «Общие» ──────────────────────────────────────────────────────

    def _create_general_tab(self) -> QWidget:
        """Создать вкладку «Общие»."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(14)

        # Автозапуск
        self._chk_autostart = QCheckBox('Запускать при включении компьютера')
        layout.addWidget(self._chk_autostart)

        # Папка синхронизации
        grp_folder = QGroupBox('Папка синхронизации')
        folder_layout = QHBoxLayout(grp_folder)
        self._lbl_sync_folder = QLabel()
        self._lbl_sync_folder.setWordWrap(True)
        self._lbl_sync_folder.setMinimumWidth(300)
        folder_layout.addWidget(self._lbl_sync_folder, stretch=1)

        btn_change_folder = QPushButton('Изменить…')
        btn_change_folder.setFixedWidth(100)
        btn_change_folder.clicked.connect(self._choose_sync_folder)
        folder_layout.addWidget(btn_change_folder)
        layout.addWidget(grp_folder)

        # Тема оформления
        grp_theme = QGroupBox('Тема оформления')
        theme_layout = QHBoxLayout(grp_theme)

        self._theme_group = QButtonGroup(self)
        self._rb_theme_system = QRadioButton('Системная')
        self._rb_theme_light = QRadioButton('Светлая')
        self._rb_theme_dark = QRadioButton('Тёмная')
        self._theme_group.addButton(self._rb_theme_system, 0)
        self._theme_group.addButton(self._rb_theme_light, 1)
        self._theme_group.addButton(self._rb_theme_dark, 2)

        theme_layout.addWidget(self._rb_theme_system)
        theme_layout.addWidget(self._rb_theme_light)
        theme_layout.addWidget(self._rb_theme_dark)
        theme_layout.addStretch()
        layout.addWidget(grp_theme)

        layout.addStretch()
        return tab

    # ── Вкладка «Синхронизация» ───────────────────────────────────────────────

    def _create_sync_tab(self) -> QWidget:
        """Создать вкладку «Синхронизация»."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(14)

        # Интервал проверки
        interval_layout = QHBoxLayout()
        interval_layout.addWidget(QLabel('Интервал проверки:'))
        self._cmb_interval = QComboBox()
        self._cmb_interval.addItem('30 секунд', 30)
        self._cmb_interval.addItem('1 минута', 60)
        self._cmb_interval.addItem('2 минуты', 120)
        self._cmb_interval.addItem('5 минут', 300)
        interval_layout.addWidget(self._cmb_interval)
        interval_layout.addStretch()
        layout.addLayout(interval_layout)

        # Избирательная синхронизация
        grp_selective = QGroupBox('Избирательная синхронизация')
        sel_layout = QVBoxLayout(grp_selective)

        hint = QLabel(
            'Отмеченные папки будут синхронизироваться. '
            'Снимите галочку, чтобы исключить папку.'
        )
        hint.setStyleSheet(f'color: {_TEXT_DIM}; font-size: 12px;')
        hint.setWordWrap(True)
        sel_layout.addWidget(hint)

        self._tree_selective = QTreeWidget()
        self._tree_selective.setHeaderLabels(['Имя', 'Размер'])
        self._tree_selective.setColumnWidth(0, 350)
        self._tree_selective.setAlternatingRowColors(True)
        self._tree_selective.setMinimumHeight(200)
        sel_layout.addWidget(self._tree_selective)

        btn_refresh = QPushButton('Обновить список')
        btn_refresh.setFixedWidth(140)
        btn_refresh.clicked.connect(self._populate_selective_tree)
        sel_layout.addWidget(btn_refresh, alignment=Qt.AlignmentFlag.AlignRight)

        layout.addWidget(grp_selective, stretch=1)
        return tab

    # ── Вкладка «Уведомления» ─────────────────────────────────────────────────

    def _create_notifications_tab(self) -> QWidget:
        """Создать вкладку «Уведомления»."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)

        grp = QGroupBox('Показывать уведомления при:')
        grp_layout = QVBoxLayout(grp)
        grp_layout.setSpacing(10)

        self._chk_notif_upload = QCheckBox('Загрузке файлов в облако')
        self._chk_notif_download = QCheckBox('Скачивании файлов из облака')
        self._chk_notif_errors = QCheckBox('Ошибках синхронизации')
        self._chk_notif_low_space = QCheckBox('Нехватке свободного места')

        for chk in (
            self._chk_notif_upload,
            self._chk_notif_download,
            self._chk_notif_errors,
            self._chk_notif_low_space,
        ):
            grp_layout.addWidget(chk)

        layout.addWidget(grp)
        layout.addStretch()
        return tab

    # ── Вкладка «Горячие клавиши» ─────────────────────────────────────────────

    def _create_hotkeys_tab(self) -> QWidget:
        """Создать вкладку «Горячие клавиши»."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(14)

        self._chk_hotkeys_enabled = QCheckBox('Включить горячие клавиши')
        layout.addWidget(self._chk_hotkeys_enabled)

        grp = QGroupBox('Текущие привязки')
        form = QFormLayout(grp)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._lbl_hk_region = QLabel()
        self._lbl_hk_fullscreen = QLabel()
        self._lbl_hk_window = QLabel()

        # Стиль для значений клавиш
        key_style = (
            f'background-color: {_BG_SURFACE0}; '
            f'border: 1px solid {_BORDER}; '
            f'border-radius: 4px; '
            f'padding: 4px 10px; '
            f'font-family: "Consolas", "Cascadia Code", monospace; '
            f'font-size: 13px;'
        )
        for lbl in (self._lbl_hk_region, self._lbl_hk_fullscreen, self._lbl_hk_window):
            lbl.setStyleSheet(key_style)

        form.addRow('Снимок области:', self._lbl_hk_region)
        form.addRow('Снимок экрана:', self._lbl_hk_fullscreen)
        form.addRow('Снимок окна:', self._lbl_hk_window)

        layout.addWidget(grp)
        layout.addStretch()
        return tab

    # ── Вкладка «Прокси» ─────────────────────────────────────────────────────

    def _create_proxy_tab(self) -> QWidget:
        """Создать вкладку «Прокси»."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(14)

        # Режим прокси
        grp_mode = QGroupBox('Режим прокси')
        mode_layout = QVBoxLayout(grp_mode)

        self._proxy_group = QButtonGroup(self)
        self._rb_proxy_none = QRadioButton('Без прокси')
        self._rb_proxy_system = QRadioButton('Использовать системный прокси')
        self._rb_proxy_manual = QRadioButton('Ручная настройка')
        self._proxy_group.addButton(self._rb_proxy_none, 0)
        self._proxy_group.addButton(self._rb_proxy_system, 1)
        self._proxy_group.addButton(self._rb_proxy_manual, 2)

        mode_layout.addWidget(self._rb_proxy_none)
        mode_layout.addWidget(self._rb_proxy_system)
        mode_layout.addWidget(self._rb_proxy_manual)

        self._proxy_group.idToggled.connect(self._on_proxy_mode_changed)
        layout.addWidget(grp_mode)

        # Ручные настройки
        grp_manual = QGroupBox('Параметры прокси')
        manual_layout = QFormLayout(grp_manual)
        manual_layout.setSpacing(10)

        self._edt_proxy_server = QLineEdit()
        self._edt_proxy_server.setPlaceholderText('Например: proxy.example.com')
        self._edt_proxy_port = QLineEdit()
        self._edt_proxy_port.setPlaceholderText('8080')
        self._edt_proxy_port.setFixedWidth(100)

        manual_layout.addRow('Сервер:', self._edt_proxy_server)
        manual_layout.addRow('Порт:', self._edt_proxy_port)

        self._grp_proxy_manual = grp_manual
        layout.addWidget(grp_manual)

        layout.addStretch()
        return tab

    # ── Вкладка «Аккаунт» ────────────────────────────────────────────────────

    def _create_account_tab(self) -> QWidget:
        """Создать вкладку «Аккаунт»."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(16)

        # Информация о пользователе
        grp_user = QGroupBox('Пользователь')
        user_layout = QHBoxLayout(grp_user)
        user_layout.setSpacing(16)

        # Аватар
        self._lbl_avatar = QLabel()
        self._lbl_avatar.setFixedSize(64, 64)
        self._lbl_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_avatar.setStyleSheet(
            f'border: 2px solid {_BORDER}; border-radius: 32px; '
            f'background-color: {_BG_SURFACE0};'
        )
        user_layout.addWidget(self._lbl_avatar)

        # Имя + email
        info_layout = QVBoxLayout()
        info_layout.setSpacing(4)
        self._lbl_user_name = QLabel()
        self._lbl_user_name.setStyleSheet('font-size: 16px; font-weight: bold;')
        self._lbl_user_email = QLabel()
        self._lbl_user_email.setStyleSheet(f'color: {_TEXT_DIM}; font-size: 13px;')
        info_layout.addWidget(self._lbl_user_name)
        info_layout.addWidget(self._lbl_user_email)
        info_layout.addStretch()
        user_layout.addLayout(info_layout, stretch=1)

        layout.addWidget(grp_user)

        # Хранилище
        grp_storage = QGroupBox('Хранилище Google Drive')
        storage_layout = QVBoxLayout(grp_storage)
        storage_layout.setSpacing(8)

        self._pb_storage = QProgressBar()
        self._pb_storage.setMinimum(0)
        self._pb_storage.setMaximum(100)
        self._pb_storage.setTextVisible(True)
        storage_layout.addWidget(self._pb_storage)

        self._lbl_storage_text = QLabel()
        self._lbl_storage_text.setStyleSheet(f'color: {_TEXT_DIM}; font-size: 12px;')
        self._lbl_storage_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        storage_layout.addWidget(self._lbl_storage_text)

        layout.addWidget(grp_storage)

        # Кнопка выхода
        btn_logout = QPushButton('  Выйти из аккаунта  ')
        btn_logout.setObjectName('btn_danger')
        btn_logout.setFixedWidth(200)
        btn_logout.clicked.connect(self._on_logout)
        layout.addWidget(btn_logout, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()
        return tab

    # =========================================================================
    #  Загрузка текущих значений из Config
    # =========================================================================

    def _load_values(self) -> None:
        """Заполнить все элементы управления текущими значениями конфигурации."""
        cfg = self._config

        # ── Общие ──
        self._chk_autostart.setChecked(cfg.auto_start)
        self._lbl_sync_folder.setText(cfg.sync_folder)

        theme_map = {'system': 0, 'light': 1, 'dark': 2}
        btn = self._theme_group.button(theme_map.get(cfg.theme, 0))
        if btn:
            btn.setChecked(True)

        # ── Синхронизация ──
        interval_idx = self._cmb_interval.findData(cfg.sync_interval)
        if interval_idx >= 0:
            self._cmb_interval.setCurrentIndex(interval_idx)
        else:
            self._cmb_interval.setCurrentIndex(1)  # 60 сек по умолчанию
        self._populate_selective_tree()

        # ── Уведомления ──
        notif = cfg.notifications
        self._chk_notif_upload.setChecked(notif.get('file_upload', True))
        self._chk_notif_download.setChecked(notif.get('file_download', True))
        self._chk_notif_errors.setChecked(notif.get('errors', True))
        self._chk_notif_low_space.setChecked(notif.get('low_space', True))

        # ── Горячие клавиши ──
        self._chk_hotkeys_enabled.setChecked(cfg.hotkeys_enabled)
        hk = cfg.hotkeys
        self._lbl_hk_region.setText(hk.get('capture_region', 'PrtScr'))
        self._lbl_hk_fullscreen.setText(hk.get('capture_fullscreen', 'Ctrl+Shift+3'))
        self._lbl_hk_window.setText(hk.get('capture_window', 'Ctrl+Shift+4'))

        # ── Прокси ──
        proxy_btn = self._proxy_group.button(cfg.proxy_mode)
        if proxy_btn:
            proxy_btn.setChecked(True)
        self._edt_proxy_server.setText(cfg.proxy_server)
        self._edt_proxy_port.setText(cfg.proxy_port)
        self._on_proxy_mode_changed(cfg.proxy_mode, True)

        # ── Аккаунт ──
        self._load_account_info()

    def _load_account_info(self) -> None:
        """Загрузить информацию об аккаунте в UI."""
        user_info = self._auth_manager.get_user_info()

        if user_info:
            self._lbl_user_name.setText(user_info.name or 'Нет имени')
            self._lbl_user_email.setText(user_info.email or 'Нет email')

            # Аватар
            if user_info.avatar_data:
                pixmap = QPixmap()
                pixmap.loadFromData(user_info.avatar_data)
                if not pixmap.isNull():
                    pixmap = pixmap.scaled(
                        60, 60,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    # Округлить аватар
                    rounded = self._round_pixmap(pixmap, 30)
                    self._lbl_avatar.setPixmap(rounded)
                else:
                    self._set_default_avatar()
            else:
                self._set_default_avatar()

            # Хранилище
            used_gb = user_info.storage_used_gb
            total_gb = user_info.storage_total_gb
            percent = user_info.storage_percent

            self._pb_storage.setValue(int(percent))
            self._pb_storage.setFormat(f'{percent:.1f}%')
            self._lbl_storage_text.setText(
                f'Занято {used_gb:.2f} ГБ из {total_gb:.2f} ГБ'
            )

            # Цвет прогресс-бара в зависимости от заполненности
            if percent >= 90:
                chunk_color = _RED
            elif percent >= 70:
                chunk_color = _YELLOW
            else:
                chunk_color = _ACCENT
            self._pb_storage.setStyleSheet(
                self._pb_storage.styleSheet()
                + f'\nQProgressBar::chunk {{ background-color: {chunk_color}; border-radius: 5px; }}'
            )
        else:
            # Используем закешированные данные из конфига
            self._lbl_user_name.setText(self._config.last_user_name or 'Не удалось загрузить')
            self._lbl_user_email.setText(self._config.last_user_email or '')
            self._set_default_avatar()
            self._pb_storage.setValue(0)
            self._lbl_storage_text.setText('Нет данных о хранилище')

    @staticmethod
    def _round_pixmap(pixmap: QPixmap, radius: int) -> QPixmap:
        """
        Создать округлённый QPixmap.

        Args:
            pixmap: Исходное изображение.
            radius: Радиус округления.

        Returns:
            Округлённый QPixmap.
        """
        size = min(pixmap.width(), pixmap.height())
        target = QPixmap(size, size)
        target.fill(QColor(0, 0, 0, 0))

        painter = QPainter(target)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        path = QPainterPath()
        path.addEllipse(0, 0, size, size)
        painter.setClipPath(path)

        # Центрирование исходного изображения
        x = (pixmap.width() - size) // 2
        y = (pixmap.height() - size) // 2
        painter.drawPixmap(0, 0, pixmap, x, y, size, size)
        painter.end()

        return target

    def _set_default_avatar(self) -> None:
        """Установить аватар-заглушку (инициалы или иконку)."""
        pixmap = QPixmap(60, 60)
        pixmap.fill(QColor(0, 0, 0, 0))

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QColor(_ACCENT))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(0, 0, 60, 60)

        # Инициалы пользователя
        name = self._config.last_user_name
        if name:
            parts = name.split()
            initials = ''.join(p[0].upper() for p in parts[:2])
        else:
            initials = '?'

        painter.setPen(QColor(_BG_BASE))
        font = QFont('Segoe UI', 20, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, initials)
        painter.end()

        self._lbl_avatar.setPixmap(pixmap)

    # =========================================================================
    #  Избирательная синхронизация — сканирование папки
    # =========================================================================

    def _populate_selective_tree(self) -> None:
        """Сканировать папку синхронизации и заполнить дерево с чекбоксами."""
        self._tree_selective.clear()
        sync_folder = self._lbl_sync_folder.text()

        if not sync_folder or not os.path.isdir(sync_folder):
            item = QTreeWidgetItem(self._tree_selective)
            item.setText(0, '⚠ Папка синхронизации не найдена')
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            return

        selected_folders = set(self._config.selective_sync_folders)
        no_selection = len(selected_folders) == 0  # Если пусто — всё выбрано

        self._scan_directory(
            sync_folder,
            self._tree_selective.invisibleRootItem(),
            sync_folder,
            selected_folders,
            no_selection,
            max_depth=3,
        )
        self._tree_selective.expandToDepth(0)

    def _scan_directory(
        self,
        path: str,
        parent_item: QTreeWidgetItem,
        root_path: str,
        selected: set,
        select_all: bool,
        max_depth: int,
        current_depth: int = 0,
    ) -> None:
        """
        Рекурсивно сканировать директорию и добавить узлы в дерево.

        Args:
            path: Текущий путь для сканирования.
            parent_item: Родительский элемент дерева.
            root_path: Корневой путь синхронизации.
            selected: Множество выбранных относительных путей.
            select_all: Если True, все элементы отмечены.
            max_depth: Максимальная глубина сканирования.
            current_depth: Текущая глубина.
        """
        if current_depth >= max_depth:
            return

        try:
            entries = sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError:
            return
        except OSError as e:
            logger.warning("Ошибка сканирования %s: %s", path, e)
            return

        for entry in entries:
            # Пропускаем скрытые и системные файлы
            if entry.name.startswith('.') or entry.name.startswith('~'):
                continue

            if not entry.is_dir():
                continue

            rel_path = os.path.relpath(entry.path, root_path)
            item = QTreeWidgetItem(parent_item)
            item.setText(0, entry.name)
            item.setData(0, Qt.ItemDataRole.UserRole, rel_path)
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsAutoTristate
            )

            # Размер папки (быстрый подсчёт первого уровня)
            dir_size = self._get_dir_size_shallow(entry.path)
            item.setText(1, self._format_size(dir_size))

            # Устанавливаем состояние чекбокса
            if select_all or rel_path in selected:
                item.setCheckState(0, Qt.CheckState.Checked)
            else:
                item.setCheckState(0, Qt.CheckState.Unchecked)

            # Рекурсия
            self._scan_directory(
                entry.path, item, root_path, selected, select_all,
                max_depth, current_depth + 1,
            )

    @staticmethod
    def _get_dir_size_shallow(path: str) -> int:
        """
        Быстрый подсчёт размера содержимого папки (только первый уровень).

        Args:
            path: Путь к директории.

        Returns:
            Суммарный размер файлов первого уровня в байтах.
        """
        total = 0
        try:
            for entry in os.scandir(path):
                if entry.is_file(follow_symlinks=False):
                    try:
                        total += entry.stat().st_size
                    except OSError:
                        pass
        except (PermissionError, OSError):
            pass
        return total

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """
        Отформатировать размер файла для отображения.

        Args:
            size_bytes: Размер в байтах.

        Returns:
            Строка вида «1.5 МБ», «300 КБ» и т.д.
        """
        if size_bytes == 0:
            return '—'
        if size_bytes < 1024:
            return f'{size_bytes} Б'
        if size_bytes < 1024 * 1024:
            return f'{size_bytes / 1024:.1f} КБ'
        if size_bytes < 1024 * 1024 * 1024:
            return f'{size_bytes / (1024 * 1024):.1f} МБ'
        return f'{size_bytes / (1024 ** 3):.2f} ГБ'

    def _collect_selected_folders(self) -> List[str]:
        """
        Собрать список выбранных папок из дерева избирательной синхронизации.

        Returns:
            Список относительных путей выбранных папок.
        """
        selected: List[str] = []
        self._collect_checked_items(
            self._tree_selective.invisibleRootItem(), selected
        )
        return selected

    def _collect_checked_items(
        self, parent: QTreeWidgetItem, result: List[str]
    ) -> None:
        """
        Рекурсивно собрать отмеченные элементы дерева.

        Args:
            parent: Родительский элемент дерева.
            result: Список для накопления результатов.
        """
        for i in range(parent.childCount()):
            child = parent.child(i)
            if child.checkState(0) == Qt.CheckState.Checked:
                rel_path = child.data(0, Qt.ItemDataRole.UserRole)
                if rel_path:
                    result.append(rel_path)
            # Проверяем дочерние, даже если родитель частично отмечен
            self._collect_checked_items(child, result)

    # =========================================================================
    #  Обработчики действий
    # =========================================================================

    def _choose_sync_folder(self) -> None:
        """Открыть диалог выбора папки синхронизации."""
        current = self._lbl_sync_folder.text()
        folder = QFileDialog.getExistingDirectory(
            self,
            'Выберите папку синхронизации',
            current if os.path.isdir(current) else os.path.expanduser('~'),
        )
        if folder:
            self._lbl_sync_folder.setText(folder)

    def _on_proxy_mode_changed(self, button_id: int, checked: bool) -> None:
        """
        Обработка смены режима прокси.

        Args:
            button_id: ID выбранной радиокнопки (0, 1, 2).
            checked: Состояние кнопки.
        """
        is_manual = button_id == 2 and checked
        self._grp_proxy_manual.setEnabled(is_manual)
        self._edt_proxy_server.setEnabled(is_manual)
        self._edt_proxy_port.setEnabled(is_manual)

    def _on_logout(self) -> None:
        """Обработка нажатия кнопки «Выйти из аккаунта»."""
        reply = QMessageBox.question(
            self,
            'Выход из аккаунта',
            'Вы действительно хотите выйти из аккаунта Google?\n\n'
            'Синхронизация будет остановлена.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._auth_manager.logout()
            logger.info("Пользователь вышел из аккаунта через настройки")
            QMessageBox.information(
                self,
                'Выход выполнен',
                'Вы вышли из аккаунта Google.\n'
                'Приложение будет закрыто.',
            )
            # Закрываем диалог и передаём код для выхода
            self.done(2)  # Код 2 = сигнал для выхода из приложения

    # =========================================================================
    #  Сохранение настроек
    # =========================================================================

    def _apply_settings(self) -> None:
        """Применить текущие значения из UI в конфигурацию и сохранить."""
        cfg = self._config

        # ── Общие ──
        auto_start = self._chk_autostart.isChecked()
        cfg.auto_start = auto_start
        self._set_autostart(auto_start)

        cfg.sync_folder = self._lbl_sync_folder.text()

        theme_id = self._theme_group.checkedId()
        theme_map_reverse = {0: 'system', 1: 'light', 2: 'dark'}
        cfg.theme = theme_map_reverse.get(theme_id, 'system')

        # ── Синхронизация ──
        interval = self._cmb_interval.currentData()
        if interval:
            cfg.sync_interval = interval
        cfg.selective_sync_folders = self._collect_selected_folders()

        # ── Уведомления ──
        cfg.notifications = {
            'file_upload': self._chk_notif_upload.isChecked(),
            'file_download': self._chk_notif_download.isChecked(),
            'errors': self._chk_notif_errors.isChecked(),
            'low_space': self._chk_notif_low_space.isChecked(),
        }

        # ── Горячие клавиши ──
        cfg.hotkeys_enabled = self._chk_hotkeys_enabled.isChecked()

        # ── Прокси ──
        cfg.proxy_mode = self._proxy_group.checkedId()
        cfg.proxy_server = self._edt_proxy_server.text().strip()
        cfg.proxy_port = self._edt_proxy_port.text().strip()

        # Сохранение в файл
        cfg.save()
        logger.info("Настройки сохранены из диалога")

    def _on_ok(self) -> None:
        """Обработка нажатия кнопки OK."""
        self._apply_settings()
        self.accept()

    def _on_apply(self) -> None:
        """Обработка нажатия кнопки «Применить»."""
        self._apply_settings()

    # =========================================================================
    #  Автозапуск Windows (реестр)
    # =========================================================================

    @staticmethod
    def _set_autostart(enabled: bool) -> None:
        """
        Установить или убрать автозапуск приложения через реестр Windows.

        Записывает/удаляет ключ в HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run.

        Args:
            enabled: True — добавить в автозапуск, False — убрать.
        """
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                _AUTOSTART_KEY_PATH,
                0,
                winreg.KEY_SET_VALUE,
            )
        except OSError as e:
            logger.error("Не удалось открыть ключ реестра автозапуска: %s", e)
            return

        try:
            if enabled:
                # Путь к текущему исполняемому файлу
                exe_path = sys.executable
                # Если запускаем через pythonw.exe, используем путь к main.pyw
                if 'python' in os.path.basename(exe_path).lower():
                    # Находим main.pyw рядом с текущим скриптом
                    script_dir = os.path.dirname(os.path.abspath(__file__))
                    main_pyw = os.path.join(script_dir, 'main.pyw')
                    if os.path.exists(main_pyw):
                        exe_path = f'"{sys.executable}" "{main_pyw}"'
                    else:
                        exe_path = f'"{sys.executable}" "{os.path.abspath(__file__)}"'
                else:
                    exe_path = f'"{exe_path}"'

                winreg.SetValueEx(
                    key,
                    _AUTOSTART_VALUE_NAME,
                    0,
                    winreg.REG_SZ,
                    exe_path,
                )
                logger.info("Автозапуск добавлен в реестр: %s", exe_path)
            else:
                try:
                    winreg.DeleteValue(key, _AUTOSTART_VALUE_NAME)
                    logger.info("Автозапуск удалён из реестра")
                except FileNotFoundError:
                    pass  # Ключа уже нет — ничего не делаем
        except OSError as e:
            logger.error("Ошибка работы с реестром автозапуска: %s", e)
        finally:
            winreg.CloseKey(key)

    @staticmethod
    def _get_autostart_status() -> bool:
        """
        Проверить, установлен ли автозапуск в реестре Windows.

        Returns:
            True, если ключ автозапуска существует.
        """
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                _AUTOSTART_KEY_PATH,
                0,
                winreg.KEY_READ,
            )
            try:
                winreg.QueryValueEx(key, _AUTOSTART_VALUE_NAME)
                return True
            except FileNotFoundError:
                return False
            finally:
                winreg.CloseKey(key)
        except OSError:
            return False
