# -*- coding: utf-8 -*-
"""
Главное окно приложения Google Drive Sync — аналог окна Яндекс.Диска.

Содержит боковую панель с деревом папок, блоком пользователя и индикатором
хранилища, а также основную область с breadcrumbs, панелью инструментов
и файловым браузером (режимы «плитка» и «список»).
"""

import os
import shutil
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, List

from PyQt6.QtCore import (
    Qt, QSize, QRect, QPoint, QMimeData, QUrl, QTimer, pyqtSignal
)
from PyQt6.QtGui import (
    QIcon, QPixmap, QPainter, QColor, QPen, QBrush, QFont,
    QPainterPath, QAction, QDrag, QCursor, QPalette
)
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QSplitter, QFrame, QLabel, QTreeWidget, QTreeWidgetItem,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QScrollArea, QToolButton, QLineEdit, QComboBox, QMenu,
    QProgressBar, QStatusBar, QSizePolicy, QLayout, QStyle,
    QApplication, QMessageBox, QFileDialog, QAbstractItemView,
    QLayoutItem, QWidgetItem
)

from models import FileItem, SyncStatus, UserInfo
from config import Config
from tray_manager import draw_yellow_saucer

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
#  FlowLayout — раскладка для плиточного режима
# ═══════════════════════════════════════════════════════════════════════════════

class FlowLayout(QLayout):
    """Раскладка, автоматически переносящая элементы на новую строку."""

    def __init__(self, parent=None, margin: int = 8, h_spacing: int = 8,
                 v_spacing: int = 8):
        super().__init__(parent)
        self._h_space = h_spacing
        self._v_space = v_spacing
        self._items: List[QLayoutItem] = []
        if margin >= 0:
            self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> Optional[QLayoutItem]:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> Optional[QLayoutItem]:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        m = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        effective = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x = effective.x()
        y = effective.y()
        line_height = 0

        for item in self._items:
            sz = item.sizeHint()
            next_x = x + sz.width() + self._h_space
            if next_x - self._h_space > effective.right() and line_height > 0:
                x = effective.x()
                y = y + line_height + self._v_space
                next_x = x + sz.width() + self._h_space
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), sz))
            x = next_x
            line_height = max(line_height, sz.height())

        return y + line_height - rect.y() + m.bottom()


# ═══════════════════════════════════════════════════════════════════════════════
#  FileCard — карточка файла для плиточного режима
# ═══════════════════════════════════════════════════════════════════════════════

class FileCard(QFrame):
    """Карточка файла/папки в плиточном режиме."""

    double_clicked = pyqtSignal(object)  # FileItem
    context_menu_requested = pyqtSignal(object, object)  # FileItem, QPoint

    def __init__(self, file_item: FileItem, sync_folder: str,
                 size_mode: str = 'large', parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.file_item = file_item
        self.sync_folder = sync_folder
        self.size_mode = size_mode

        if size_mode == 'normal':
            self.CARD_W = 110
            self.CARD_H = 120
            self.thumb_h = 64
            self.font_size = 8
        elif size_mode == 'huge':
            self.CARD_W = 200
            self.CARD_H = 210
            self.thumb_h = 140
            self.font_size = 10
        else:  # large
            self.CARD_W = 150
            self.CARD_H = 160
            self.thumb_h = 100
            self.font_size = 9

        self.setFixedSize(self.CARD_W, self.CARD_H)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setProperty('class', 'file-card')
        self._selected = False
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(2)

        # Миниатюра / иконка
        thumb_label = QLabel()
        thumb_label.setFixedSize(self.CARD_W - 12, self.thumb_h)
        thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        pixmap = self._get_thumbnail()
        if pixmap and not pixmap.isNull():
            scaled = pixmap.scaled(
                self.CARD_W - 12, self.thumb_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            thumb_label.setPixmap(scaled)
        else:
            icon_pixmap = self._generate_icon(self.CARD_W - 12, self.thumb_h)
            thumb_label.setPixmap(icon_pixmap)

        layout.addWidget(thumb_label)

        # Имя файла
        name_label = QLabel(self.file_item.name)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setWordWrap(False)
        name_label.setToolTip(self.file_item.name)
        name_label.setMaximumWidth(self.CARD_W - 12)
        font = QFont('Segoe UI', self.font_size)
        name_label.setFont(font)
        layout.addWidget(name_label)

        # Иконка статуса
        status_label = QLabel(self._status_text())
        status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_label.setFont(QFont('Segoe UI', max(6, self.font_size - 2)))
        status_label.setStyleSheet(f'color: {self._status_color()};')
        layout.addWidget(status_label)

    def _get_thumbnail(self) -> Optional[QPixmap]:
        """Попытка загрузить миниатюру для изображений."""
        abs_path = os.path.join(self.sync_folder, self.file_item.path)
        if not os.path.isfile(abs_path):
            return None
        ext = os.path.splitext(abs_path)[1].lower()
        if ext in ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp'):
            pix = QPixmap(abs_path)
            if not pix.isNull():
                return pix
        return None

    def _generate_icon(self, w: int, h: int) -> QPixmap:
        """Генерация иконки типа файла через QPainter."""
        pix = QPixmap(w, h)
        pix.fill(QColor(0, 0, 0, 0))
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx, cy = w // 2, h // 2

        if self.file_item.is_dir:
            p.setBrush(QBrush(QColor('#f9e2af')))
            p.setPen(Qt.PenStyle.NoPen)
            # Масштабируемые размеры папки относительно ширины w
            scale = w / 134.0
            folder_w = int(48 * scale)
            folder_h = int(32 * scale)
            folder_tab_w = int(24 * scale)
            folder_tab_h = int(10 * scale)
            p.drawRoundedRect(cx - folder_w // 2, cy - folder_h // 2, folder_w, folder_h, 4, 4)
            p.drawRoundedRect(cx - folder_w // 2, cy - folder_h // 2 - folder_tab_h + 2, folder_tab_w, folder_tab_h, 3, 3)
        else:
            ext = os.path.splitext(self.file_item.name)[1].lower()
            color = '#89b4fa'
            if ext in ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'):
                color = '#a6e3a1'
            elif ext in ('.doc', '.docx', '.txt', '.pdf'):
                color = '#74c7ec'
            elif ext in ('.mp3', '.wav', '.flac', '.ogg'):
                color = '#cba6f7'
            elif ext in ('.mp4', '.avi', '.mkv', '.mov'):
                color = '#f38ba8'
            elif ext in ('.zip', '.rar', '.7z', '.tar'):
                color = '#fab387'

            scale = w / 134.0
            file_w = int(36 * scale)
            file_h = int(44 * scale)
            corner_size = int(10 * scale)

            p.setBrush(QBrush(QColor(color)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(cx - file_w // 2, cy - file_h // 2, file_w, file_h, 4, 4)

            p.setBrush(QBrush(QColor('#1e1e2e')))
            p.drawRect(cx + file_w // 2 - corner_size, cy - file_h // 2, corner_size, corner_size)
            p.setBrush(QBrush(QColor(color).darker(120)))
            pts = [QPoint(cx + file_w // 2 - corner_size, cy - file_h // 2),
                   QPoint(cx + file_w // 2, cy - file_h // 2 + corner_size),
                   QPoint(cx + file_w // 2 - corner_size, cy - file_h // 2 + corner_size)]
            p.drawPolygon(pts)

            if ext:
                p.setPen(QPen(QColor('#1e1e2e')))
                font_sz = max(6, int(8 * scale))
                p.setFont(QFont('Segoe UI', font_sz, QFont.Weight.Bold))
                p.drawText(QRect(cx - file_w // 2, cy - file_h // 6, file_w, file_h // 3),
                           Qt.AlignmentFlag.AlignCenter, ext[1:].upper())

        p.end()
        return pix

    def _status_text(self) -> str:
        mapping = {
            SyncStatus.SYNCED: '✓', SyncStatus.UPLOADING: '↑',
            SyncStatus.DOWNLOADING: '↓', SyncStatus.ERROR: '✗',
            SyncStatus.PENDING: '…', SyncStatus.CONFLICT: '⚡',
            SyncStatus.CLOUD_ONLY: '☁', SyncStatus.LOCAL_ONLY: '💻',
        }
        return mapping.get(self.file_item.sync_status, '')

    def _status_color(self) -> str:
        mapping = {
            SyncStatus.SYNCED: '#a6e3a1', SyncStatus.UPLOADING: '#89b4fa',
            SyncStatus.DOWNLOADING: '#89b4fa', SyncStatus.ERROR: '#f38ba8',
            SyncStatus.PENDING: '#a6adc8', SyncStatus.CONFLICT: '#fab387',
            SyncStatus.CLOUD_ONLY: '#74c7ec', SyncStatus.LOCAL_ONLY: '#cdd6f4',
        }
        return mapping.get(self.file_item.sync_status, '#a6adc8')

    def set_selected(self, sel: bool) -> None:
        self._selected = sel
        self.setProperty('selected', sel)
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseDoubleClickEvent(self, event) -> None:
        self.double_clicked.emit(self.file_item)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.set_selected(True)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:
        self.context_menu_requested.emit(self.file_item, event.globalPos())


# ═══════════════════════════════════════════════════════════════════════════════
#  MainWindow
# ═══════════════════════════════════════════════════════════════════════════════

class MainWindow(QMainWindow):
    """Главное окно приложения Google Drive Sync."""

    open_settings_requested = pyqtSignal()
    logout_requested = pyqtSignal()

    def __init__(self, sync_engine, drive_api, config: Config,
                 database, auth_manager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._sync_engine = sync_engine
        self._drive_api = drive_api
        self._config = config
        self._database = database
        self._auth_manager = auth_manager
        self._current_path = ''
        self._file_cards: List[FileCard] = []
        self._user_info: Optional[UserInfo] = None
        self._history_back: List[str] = []
        self._history_forward: List[str] = []

        self._setup_window()
        self._build_ui()
        self.apply_theme_styles(self._config.theme)
        self._connect_signals()
        self._load_user_info()
        self._restore_geometry()
        self._populate_folder_tree()
        QTimer.singleShot(100, lambda: self.navigate_to(''))

    def _setup_window(self) -> None:
        self.setWindowTitle('Google Drive Sync')
        self.setMinimumSize(900, 600)
        self.resize(1100, 700)
        self.setAcceptDrops(True)
        icon_pix = QPixmap(32, 32)
        icon_pix.fill(QColor(0, 0, 0, 0))
        p = QPainter(icon_pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        draw_yellow_saucer(p, 32)
        p.end()
        self.setWindowIcon(QIcon(icon_pix))

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # ── Боковая панель ────────────────────────────────────────────
        self._sidebar = QFrame()
        self._sidebar.setFixedWidth(260)
        sidebar_layout = QVBoxLayout(self._sidebar)
        sidebar_layout.setContentsMargins(12, 12, 12, 12)
        sidebar_layout.setSpacing(8)

        # Блок пользователя
        user_frame = QFrame()
        user_layout = QHBoxLayout(user_frame)
        user_layout.setContentsMargins(4, 4, 4, 4)
        user_layout.setSpacing(10)

        self._avatar_label = QLabel()
        self._avatar_label.setFixedSize(40, 40)
        self._avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._avatar_label.setText('?')
        user_layout.addWidget(self._avatar_label)

        user_info_layout = QVBoxLayout()
        user_info_layout.setSpacing(0)
        self._name_label = QLabel('Загрузка...')
        self._name_label.setFont(QFont('Segoe UI', 10, QFont.Weight.Bold))
        user_info_layout.addWidget(self._name_label)
        self._email_label = QLabel('')
        self._email_label.setFont(QFont('Segoe UI', 8))
        user_info_layout.addWidget(self._email_label)
        self._logout_lbl = QLabel('<a href="#logout" style="color: #0055ff; text-decoration: none;">Выйти</a>')
        self._logout_lbl.setFont(QFont('Segoe UI', 8))
        self._logout_lbl.linkActivated.connect(self._on_logout)
        user_info_layout.addWidget(self._logout_lbl)
        user_layout.addLayout(user_info_layout)
        user_layout.addStretch()
        sidebar_layout.addWidget(user_frame)

        self._sep1 = QFrame()
        self._sep1.setFrameShape(QFrame.Shape.HLine)
        sidebar_layout.addWidget(self._sep1)

        self._tree_label = QLabel('Папки')
        self._tree_label.setFont(QFont('Segoe UI', 9, QFont.Weight.Bold))
        sidebar_layout.addWidget(self._tree_label)

        self._folder_tree = QTreeWidget()
        self._folder_tree.setHeaderHidden(True)
        self._folder_tree.setIconSize(QSize(18, 18))
        self._folder_tree.setIndentation(16)
        self._folder_tree.itemClicked.connect(self._on_tree_item_clicked)
        sidebar_layout.addWidget(self._folder_tree, 1)

        self._sep2 = QFrame()
        self._sep2.setFrameShape(QFrame.Shape.HLine)
        sidebar_layout.addWidget(self._sep2)

        storage_frame = QFrame()
        storage_layout = QVBoxLayout(storage_frame)
        storage_layout.setContentsMargins(0, 4, 0, 4)
        storage_layout.setSpacing(6)
        self._storage_bar = QProgressBar()
        self._storage_bar.setFixedHeight(6)
        self._storage_bar.setRange(0, 100)
        self._storage_bar.setValue(0)
        self._storage_bar.setTextVisible(False)
        storage_layout.addWidget(self._storage_bar)
        self._storage_text = QLabel('Загрузка...')
        self._storage_text.setFont(QFont('Segoe UI', 8))
        storage_layout.addWidget(self._storage_text)

        # Желтая промо-кнопка "Купить место"
        self._promo_btn = QToolButton()
        self._promo_btn.setText('Купить место')
        self._promo_btn.setFixedHeight(30)
        self._promo_btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._promo_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._promo_btn.setStyleSheet('''
            QToolButton { background: #ffcc00; color: #000000; border: none;
                border-radius: 6px; font-family: 'Segoe UI'; font-size: 9pt; font-weight: bold; }
            QToolButton:hover { background: #ffdb4d; }
        ''')
        self._promo_btn.clicked.connect(self._on_promo_clicked)
        storage_layout.addWidget(self._promo_btn)

        sidebar_layout.addWidget(storage_frame)
        splitter.addWidget(self._sidebar)

        # ── Основная область ──────────────────────────────────────────
        self._content = QFrame()
        content_layout = QVBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Toolbar
        self._toolbar_frame = QFrame()
        self._toolbar_frame.setFixedHeight(48)
        toolbar_layout = QHBoxLayout(self._toolbar_frame)
        toolbar_layout.setContentsMargins(16, 0, 16, 0)
        toolbar_layout.setSpacing(8)

        # Кнопки навигации по истории
        self._back_btn = QToolButton()
        self._back_btn.setText('←')
        self._back_btn.setFixedSize(28, 28)
        self._back_btn.setToolTip('Назад')
        self._back_btn.clicked.connect(self._go_back)
        self._back_btn.setEnabled(False)
        toolbar_layout.addWidget(self._back_btn)

        self._forward_btn = QToolButton()
        self._forward_btn.setText('→')
        self._forward_btn.setFixedSize(28, 28)
        self._forward_btn.setToolTip('Вперёд')
        self._forward_btn.clicked.connect(self._go_forward)
        self._forward_btn.setEnabled(False)
        toolbar_layout.addWidget(self._forward_btn)

        self._breadcrumb_container = QHBoxLayout()
        self._breadcrumb_container.setSpacing(4)
        toolbar_layout.addLayout(self._breadcrumb_container)
        toolbar_layout.addStretch()

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText('Поиск...')
        self._search_input.setFixedWidth(180)
        self._search_input.setFixedHeight(28)
        self._search_input.setStyleSheet('''
            QLineEdit { background: #313244; color: #cdd6f4; border: 1px solid #45475a;
                border-radius: 14px; padding: 0 12px; font-family: 'Segoe UI'; font-size: 9pt; }
            QLineEdit:focus { border: 1px solid #89b4fa; }
        ''')
        self._search_input.textChanged.connect(self._on_search)
        toolbar_layout.addWidget(self._search_input)

        self._sort_combo = QComboBox()
        self._sort_combo.addItems(['По имени', 'По дате', 'По размеру'])
        self._sort_combo.setFixedHeight(28)
        self._sort_combo.setStyleSheet('''
            QComboBox { background: #313244; color: #cdd6f4; border: 1px solid #45475a;
                border-radius: 6px; padding: 0 8px; font-family: 'Segoe UI'; font-size: 9pt; min-width: 100px; }
            QComboBox::drop-down { border: none; width: 20px; }
            QComboBox QAbstractItemView { background: #313244; color: #cdd6f4;
                selection-background-color: #45475a; border: 1px solid #585b70; }
        ''')
        sort_map = {'name': 0, 'date': 1, 'size': 2}
        self._sort_combo.setCurrentIndex(sort_map.get(self._config.sort_by, 0))
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        toolbar_layout.addWidget(self._sort_combo)

        btn_style = '''
            QToolButton { background: #313244; color: #cdd6f4; border: 1px solid #45475a;
                border-radius: 6px; font-size: 14px; }
            QToolButton:hover { background: #45475a; }
            QToolButton:checked { background: #89b4fa; color: #1e1e2e; border: 1px solid #89b4fa; }
        '''
        self._view_mode_btn = QToolButton()
        self._view_mode_btn.setFixedSize(38, 28)
        self._view_mode_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._view_mode_btn.setToolTip('Вид')
        self._view_mode_btn.setStyleSheet(btn_style)

        self._view_menu = QMenu(self._view_mode_btn)
        self._view_menu.setStyleSheet('''
            QMenu { background: #313244; color: #cdd6f4; border: 1px solid #45475a;
                border-radius: 6px; padding: 4px; }
            QMenu::item { padding: 6px 28px 6px 28px; border-radius: 4px; font-family: 'Segoe UI'; font-size: 9pt; }
            QMenu::item:selected { background: #45475a; color: #cdd6f4; }
            QMenu::indicator { width: 16px; height: 16px; left: 6px; }
            QMenu::separator { height: 1px; background: #45475a; margin: 4px 8px; }
        ''')
        self._view_mode_btn.setMenu(self._view_menu)

        current_mode = self._config.view_mode
        self._view_mode_btn.setText(self._get_view_mode_icon(current_mode))
        self._update_view_menu()

        toolbar_layout.addWidget(self._view_mode_btn)

        self._toggle_sync_btn = QToolButton()
        self._toggle_sync_btn.setText('▶')
        self._toggle_sync_btn.setFixedSize(28, 28)
        self._toggle_sync_btn.setToolTip('Запустить автосинхронизацию')
        self._toggle_sync_btn.setStyleSheet('''
            QToolButton { background: #313244; color: #a6e3a1; border: 1px solid #45475a;
                border-radius: 6px; font-size: 14px; }
            QToolButton:hover { background: #45475a; }
        ''')
        self._toggle_sync_btn.clicked.connect(self._on_toggle_sync)
        toolbar_layout.addWidget(self._toggle_sync_btn)

        self._sync_btn = QToolButton()
        self._sync_btn.setText('⟳')
        self._sync_btn.setFixedSize(28, 28)
        self._sync_btn.setToolTip('Синхронизировать сейчас')
        self._sync_btn.setStyleSheet('''
            QToolButton { background: #313244; color: #89b4fa; border: 1px solid #45475a;
                border-radius: 6px; font-size: 16px; }
            QToolButton:hover { background: #45475a; }
        ''')
        self._sync_btn.clicked.connect(self._on_sync_now)
        toolbar_layout.addWidget(self._sync_btn)

        self._settings_btn = QToolButton()
        self._settings_btn.setText('⚙')
        self._settings_btn.setFixedSize(28, 28)
        self._settings_btn.setToolTip('Настройки')
        self._settings_btn.setStyleSheet('''
            QToolButton { background: #313244; color: #89b4fa; border: 1px solid #45475a;
                border-radius: 6px; font-size: 16px; }
            QToolButton:hover { background: #45475a; }
        ''')
        self._settings_btn.clicked.connect(self.open_settings_requested.emit)
        toolbar_layout.addWidget(self._settings_btn)

        content_layout.addWidget(self._toolbar_frame)

        # Область файлов
        self._files_stack = QWidget()
        files_layout = QVBoxLayout(self._files_stack)
        files_layout.setContentsMargins(0, 0, 0, 0)
        files_layout.setSpacing(0)

        scroll_style = '''
            QScrollArea { background: #1e1e2e; border: none; }
            QScrollBar:vertical { background: #181825; width: 8px; border: none; }
            QScrollBar::handle:vertical { background: #45475a; border-radius: 4px; min-height: 30px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        '''
        self._tiles_scroll = QScrollArea()
        self._tiles_scroll.setWidgetResizable(True)
        self._tiles_scroll.setStyleSheet(scroll_style)
        self._tiles_container = QWidget()
        self._tiles_layout = FlowLayout(self._tiles_container, 12, 10, 10)
        self._tiles_scroll.setWidget(self._tiles_container)

        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(['Имя', 'Размер', 'Изменён', 'Статус'])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            self._table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(1, 100)
        self._table.setColumnWidth(2, 150)
        self._table.setColumnWidth(3, 80)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(42)  # Увеличиваем высоту строки
        self._table.setShowGrid(False)  # Скрываем сетку таблицы
        self._table.setIconSize(QSize(18, 18))  # Увеличиваем размер иконок в таблице
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)
        self._table.doubleClicked.connect(self._on_table_double_click)
        self._table.setStyleSheet('''
            QTableWidget { background: #1e1e2e; color: #cdd6f4; border: none;
                gridline-color: #313244; font-family: 'Segoe UI'; font-size: 10pt; }
            QTableWidget::item { padding: 6px 8px; }
            QTableWidget::item:hover { background: #313244; }
            QTableWidget::item:selected { background: #45475a; color: #cdd6f4; }
            QHeaderView::section { background: #181825; color: #a6adc8; border: none;
                border-bottom: 1px solid #313244; padding: 6px 8px;
                font-family: 'Segoe UI'; font-size: 9pt; font-weight: bold; }
        ''')

        files_layout.addWidget(self._tiles_scroll)
        files_layout.addWidget(self._table)

        if self._config.view_mode == 'list':
            self._tiles_scroll.hide()
            self._table.show()
        else:
            self._tiles_scroll.show()
            self._table.hide()

        content_layout.addWidget(self._files_stack, 1)

        # Статус-бар
        self._status_label = QLabel('Синхронизация отключена')
        self._status_label.setFont(QFont('Segoe UI', 9))
        self._status_label.setStyleSheet('color: #a6adc8; padding: 4px 16px;')
        self._status_frame = QFrame()
        self._status_frame.setFixedHeight(28)
        sl = QHBoxLayout(self._status_frame)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(self._status_label)
        content_layout.addWidget(self._status_frame)

        splitter.addWidget(self._content)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

    def _connect_signals(self) -> None:
        try:
            self._sync_engine.file_status_changed.connect(self._update_file_status)
            self._sync_engine.file_synced.connect(
                lambda path: self._update_file_status(path, SyncStatus.SYNCED))
            self._sync_engine.sync_progress.connect(self._on_sync_progress)
            self._sync_engine.status_changed.connect(self._on_status_changed)
        except Exception as e:
            logger.warning('Не удалось подключить сигналы: %s', e)

    def apply_theme_styles(self, theme: str) -> None:
        if theme == 'system':
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
                    use_dark = (value == 0)
                finally:
                    winreg.CloseKey(key)
            except Exception:
                use_dark = True
        else:
            use_dark = (theme == 'dark')

        if use_dark:
            bg_base = '#1e1e2e'
            bg_mantle = '#181825'
            bg_surface0 = '#313244'
            bg_surface1 = '#45475a'
            bg_surface2 = '#585b70'
            text = '#cdd6f4'
            text_dim = '#a6adc8'
            text_muted = '#6c7086'
            accent = '#89b4fa'
            border = '#45475a'
            self._theme_text_color = '#cdd6f4'
            self._theme_accent_color = '#89b4fa'
            self._theme_muted_color = '#6c7086'
            logout_color = '#89b4fa'
        else:
            bg_base = '#ffffff'      # Чисто белый фон контента (как в Яндекс.Диске)
            bg_mantle = '#f5f6f8'    # Серо-голубой фон левой панели (как в Яндекс.Диске)
            bg_surface0 = '#eef2f7'  # hover
            bg_surface1 = '#e5f1ff'  # selected
            bg_surface2 = '#e8e9eb'
            text = '#2c2d30'         # Высококонтрастный цвет текста
            text_dim = '#7a7b7d'
            text_muted = '#9ca0b0'
            accent = '#0055ff'       # Синий акцент для ссылок и кнопок
            border = '#e8e9eb'       # Тонкие границы
            self._theme_text_color = '#2c2d30'
            self._theme_accent_color = '#0055ff'
            self._theme_muted_color = '#9ca0b0'
            logout_color = '#0055ff'

        self._sidebar.setStyleSheet(f'QFrame {{ background: {bg_mantle}; }}')
        self._avatar_label.setStyleSheet(
            f'background: {bg_surface0}; border-radius: 20px; color: {text}; '
            'font-size: 16px; font-weight: bold;'
        )
        self._name_label.setStyleSheet(f'color: {text};')
        self._email_label.setStyleSheet(f'color: {text_dim};')
        self._logout_lbl.setText(f'<a href="#logout" style="color: {logout_color}; text-decoration: none;">Выйти</a>')
        self._sep1.setStyleSheet(f'background: {bg_surface0}; max-height: 1px;')
        self._sep2.setStyleSheet(f'background: {bg_surface0}; max-height: 1px;')
        self._tree_label.setStyleSheet(f'color: {text_dim}; padding: 4px 0;')

        self._folder_tree.setStyleSheet(f'''
            QTreeWidget {{ background: {bg_mantle}; color: {text}; border: none;
                font-family: 'Segoe UI'; font-size: 10pt; }}
            QTreeWidget::item {{ padding: 4px 2px; border-radius: 4px; }}
            QTreeWidget::item:hover {{ background: {bg_surface0}; }}
            QTreeWidget::item:selected {{ background: {bg_surface1}; color: {text}; }}
            QTreeWidget::branch {{ background: {bg_mantle}; }}
        ''')

        self._storage_bar.setStyleSheet(f'''
            QProgressBar {{ background: {bg_surface0}; border-radius: 3px; border: none; }}
            QProgressBar::chunk {{ background: {accent}; border-radius: 3px; }}
        ''')
        self._storage_text.setStyleSheet(f'color: {text_dim};')

        self._content.setStyleSheet(f'QFrame {{ background: {bg_base}; }}')
        self._toolbar_frame.setStyleSheet(
            f'QFrame {{ background: {bg_mantle}; border-bottom: 1px solid {bg_surface0}; }}'
        )

        self._search_input.setStyleSheet(f'''
            QLineEdit {{ background: {bg_surface0}; color: {text}; border: 1px solid {border};
                border-radius: 14px; padding: 0 12px; font-family: 'Segoe UI'; font-size: 9pt; }}
            QLineEdit:focus {{ border: 1px solid {accent}; }}
        ''')
        self._sort_combo.setStyleSheet(f'''
            QComboBox {{ background: {bg_surface0}; color: {text}; border: 1px solid {border};
                border-radius: 6px; padding: 0 8px; font-family: 'Segoe UI'; font-size: 9pt; min-width: 100px; }}
            QComboBox::drop-down {{ border: none; width: 20px; }}
            QComboBox QAbstractItemView {{ background: {bg_surface0}; color: {text};
                selection-background-color: {bg_surface1}; border: 1px solid {bg_surface2}; }}
        ''')

        btn_style = f'''
            QToolButton {{ background: {bg_surface0}; color: {text}; border: 1px solid {border};
                border-radius: 6px; font-size: 14px; }}
            QToolButton:hover {{ background: {bg_surface1}; }}
            QToolButton:disabled {{ color: {text_muted}; background: {bg_mantle}; border: 1px solid {border}; }}
            QToolButton:checked {{ background: {accent}; color: {bg_base}; border: 1px solid {accent}; }}
        '''
        self._view_mode_btn.setStyleSheet(btn_style)
        self._view_menu.setStyleSheet(f'''
            QMenu {{ background: {bg_surface0}; color: {text}; border: 1px solid {border};
                border-radius: 6px; padding: 4px; }}
            QMenu::item {{ padding: 6px 28px 6px 28px; border-radius: 4px; font-family: 'Segoe UI'; font-size: 9pt; }}
            QMenu::item:selected {{ background: {bg_surface1}; color: {text}; }}
            QMenu::indicator {{ width: 16px; height: 16px; left: 6px; }}
            QMenu::separator {{ height: 1px; background: {border}; margin: 4px 8px; }}
        ''')
        self._back_btn.setStyleSheet(btn_style)
        self._forward_btn.setStyleSheet(btn_style)

        sync_btn_style = f'''
            QToolButton {{ background: {bg_surface0}; color: {accent}; border: 1px solid {border};
                border-radius: 6px; font-size: 16px; }}
            QToolButton:hover {{ background: {bg_surface1}; }}
        '''
        self._sync_btn.setStyleSheet(sync_btn_style)
        self._settings_btn.setStyleSheet(sync_btn_style)

        self._on_status_changed(self._sync_engine.get_status())

        self._tiles_scroll.setStyleSheet(f'''
            QScrollArea {{ background: {bg_base}; border: none; }}
            QScrollBar:vertical {{ background: {bg_mantle}; width: 8px; border: none; }}
            QScrollBar::handle:vertical {{ background: {bg_surface1}; border-radius: 4px; min-height: 30px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        ''')
        self._table.setStyleSheet(f'''
            QTableWidget {{ background: {bg_base}; color: {text}; border: none;
                gridline-color: {bg_surface0}; font-family: 'Segoe UI'; font-size: 10pt; }}
            QTableWidget::item {{ padding: 6px 8px; }}
            QTableWidget::item:hover {{ background: {bg_surface0}; }}
            QTableWidget::item:selected {{ background: {bg_surface1}; color: {text}; }}
            QHeaderView::section {{ background: {bg_mantle}; color: {text_dim}; border: none;
                border-bottom: 1px solid {bg_surface0}; padding: 6px 8px;
                font-family: 'Segoe UI'; font-size: 9pt; font-weight: bold; }}
        ''')

        self._status_label.setStyleSheet(f'color: {text_dim}; padding: 4px 16px;')
        self._status_frame.setStyleSheet(f'background: {bg_mantle}; border-top: 1px solid {bg_surface0};')
        
        self.refresh()

    def _load_user_info(self) -> None:
        try:
            self._user_info = self._auth_manager.get_user_info()
            if self._user_info:
                self._name_label.setText(self._user_info.name or 'Пользователь')
                self._email_label.setText(self._user_info.email or '')
                initials = self._user_info.name[0].upper() if self._user_info.name else '?'
                self._avatar_label.setText(initials)
                used_gb = self._user_info.storage_used_gb
                total_gb = self._user_info.storage_total_gb
                pct = self._user_info.storage_percent
                self._storage_bar.setValue(int(pct))
                self._storage_text.setText(f'{used_gb:.1f} ГБ из {total_gb:.1f} ГБ')
                chunk_color = '#f38ba8' if pct > 90 else '#fab387' if pct > 70 else '#89b4fa'
                self._storage_bar.setStyleSheet(f'''
                    QProgressBar {{ background: #313244; border-radius: 3px; border: none; }}
                    QProgressBar::chunk {{ background: {chunk_color}; border-radius: 3px; }}
                ''')
        except Exception as e:
            logger.error('Ошибка загрузки информации о пользователе: %s', e)
            self._name_label.setText('Не подключено')

    # ── Навигация ─────────────────────────────────────────────────────

    def navigate_to(self, folder_path: str, add_to_history: bool = True) -> None:
        # Нормализуем путь к прямому слэшу для кроссплатформенности и совместимости с БД
        folder_path = folder_path.replace('\\', '/')

        if add_to_history:
            if hasattr(self, '_history_back') and self._current_path != folder_path:
                self._history_back.append(self._current_path)
                self._history_forward.clear()

        self._current_path = folder_path
        self._update_breadcrumbs()
        self._select_current_tree_item()
        self._load_files()
        self._update_history_buttons()

    def refresh(self) -> None:
        self._populate_folder_tree()
        self._load_files()

    def _update_breadcrumbs(self) -> None:
        while self._breadcrumb_container.count():
            item = self._breadcrumb_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if self._current_path == '__quick_access__':
            parts = ['Быстрый доступ']
        elif self._current_path == '__trash__':
            parts = ['Корзина']
        else:
            parts = ['Google Drive']
            if self._current_path:
                parts += self._current_path.replace('\\', '/').split('/')

        theme_accent = getattr(self, '_theme_accent_color', '#0055ff')
        theme_text = getattr(self, '_theme_text_color', '#2c2d30')
        theme_muted = getattr(self, '_theme_muted_color', '#9ca0b0')

        for i, part in enumerate(parts):
            if i > 0:
                sep = QLabel('›')
                sep.setStyleSheet(f'color: {theme_muted}; font-size: 14px;')
                self._breadcrumb_container.addWidget(sep)

            btn = QLabel(part)
            btn.setFont(QFont('Segoe UI', 10))
            btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

            if i < len(parts) - 1:
                btn.setStyleSheet(f'color: {theme_accent}; padding: 2px 4px;')
                if part == 'Google Drive':
                    path = ''
                else:
                    path = '/'.join(parts[1:i + 1])
                btn.mousePressEvent = lambda event, p=path: self.navigate_to(p)
            else:
                btn.setStyleSheet(f'color: {theme_text}; font-weight: bold; padding: 2px 4px;')

            self._breadcrumb_container.addWidget(btn)

    def _populate_folder_tree(self) -> None:
        self._folder_tree.clear()

        # 1. Быстрый доступ
        quick_item = QTreeWidgetItem(self._folder_tree, ['Быстрый доступ'])
        quick_item.setData(0, Qt.ItemDataRole.UserRole, '__quick_access__')
        quick_color = '#ffcc00' if self._config.theme != 'dark' else '#f9e2af'
        quick_item.setIcon(0, self._make_folder_icon(quick_color, 'star'))

        # 2. Google Drive (корень)
        sync_folder = self._config.sync_folder
        if not os.path.isdir(sync_folder):
            return
        root_item = QTreeWidgetItem(self._folder_tree, ['Google Drive'])
        root_item.setData(0, Qt.ItemDataRole.UserRole, '')
        root_item.setExpanded(True)

        icon_pix = QPixmap(18, 18)
        icon_pix.fill(QColor(0, 0, 0, 0))
        p = QPainter(icon_pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        draw_yellow_saucer(p, 18)
        p.end()
        root_item.setIcon(0, QIcon(icon_pix))

        self._scan_folders(sync_folder, root_item, 0, 4)

        # 3. Корзина
        trash_item = QTreeWidgetItem(self._folder_tree, ['Корзина'])
        trash_item.setData(0, Qt.ItemDataRole.UserRole, '__trash__')
        trash_item.setIcon(0, self._make_folder_icon('#7a7b7d', 'trash'))

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, col: int) -> None:
        path = item.data(0, Qt.ItemDataRole.UserRole) or ''
        self.navigate_to(path)

    def _scan_folders(self, abs_path: str, parent_item: QTreeWidgetItem,
                      depth: int, max_depth: int) -> None:
        if depth >= max_depth:
            return
        try:
            entries = sorted(os.listdir(abs_path))
        except PermissionError:
            return
        for name in entries:
            if name.startswith('.') or name.startswith('~'):
                continue
            full = os.path.join(abs_path, name)
            if not os.path.isdir(full):
                continue
            rel = os.path.relpath(full, self._config.sync_folder).replace('\\', '/')
            item = QTreeWidgetItem(parent_item, [name])
            item.setData(0, Qt.ItemDataRole.UserRole, rel)
            
            folder_type = self._get_folder_type(name)
            color = '#ffcc00' if self._config.theme != 'dark' else '#f9e2af'
            item.setIcon(0, self._make_folder_icon(color, folder_type))
            
            if self._current_path and (
                self._current_path == rel
                or self._current_path.startswith(rel + '/')
            ):
                item.setExpanded(True)
            self._scan_folders(full, item, depth + 1, max_depth)

    @staticmethod
    def _get_folder_type(folder_name: str) -> str:
        name = folder_name.lower().strip()
        if name in ('быстрый доступ', 'quick access', 'избранное', 'favorites'):
            return 'star'
        elif name in ('скриншоты', 'screenshots'):
            return 'screenshots'
        elif name in ('загрузки', 'downloads'):
            return 'downloads'
        elif name in ('музыка', 'music'):
            return 'music'
        elif name in ('картинки', 'pictures', 'фото', 'photos'):
            return 'pictures'
        elif name in ('фотокамера', 'camera', 'camera uploads'):
            return 'camera'
        elif name in ('корзина', 'trash', 'bin'):
            return 'trash'
        return 'normal'

    def _make_folder_icon(self, color_hex: str, icon_type: str = 'normal') -> QIcon:
        pix = QPixmap(18, 18)
        pix.fill(QColor(0, 0, 0, 0))
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        folder_color = QColor(color_hex)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(folder_color))
        p.drawRoundedRect(1, 5, 16, 11, 2, 2)
        p.drawRoundedRect(1, 3, 8, 4, 1, 1)
        
        p.setPen(QPen(QColor(255, 255, 255), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        
        if icon_type == 'star':
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor('#ffb300')))
            pts = [
                QPoint(9, 6), QPoint(11, 9), QPoint(14, 9),
                QPoint(12, 11), QPoint(13, 14), QPoint(9, 12),
                QPoint(5, 14), QPoint(6, 11), QPoint(4, 9), QPoint(7, 9)
            ]
            p.drawPolygon(pts)
        elif icon_type == 'screenshots':
            p.setPen(QPen(QColor('#ffffff'), 1.2))
            p.drawRect(4, 7, 10, 7)
            p.drawLine(7, 14, 11, 14)
            p.drawLine(9, 14, 9, 15)
        elif icon_type == 'downloads':
            p.setPen(QPen(QColor('#ffffff'), 1.5))
            p.drawLine(9, 6, 9, 13)
            p.drawLine(6, 10, 9, 13)
            p.drawLine(12, 10, 9, 13)
        elif icon_type == 'music':
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(QColor('#ffffff')))
            p.drawEllipse(5, 11, 4, 3)
            p.setPen(QPen(QColor('#ffffff'), 1.2))
            p.drawLine(8, 12, 8, 7)
            p.drawLine(8, 7, 12, 6)
            p.drawLine(12, 6, 12, 9)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(9, 8, 4, 3)
        elif icon_type == 'pictures':
            p.setPen(QPen(QColor('#ffffff'), 1.2))
            p.drawRect(4, 7, 10, 7)
            p.setBrush(QBrush(QColor('#ffffff')))
            p.setPen(Qt.PenStyle.NoPen)
            pts1 = [QPoint(5, 13), QPoint(9, 9), QPoint(13, 13)]
            p.drawPolygon(pts1)
        elif icon_type == 'camera':
            p.setPen(QPen(QColor('#ffffff'), 1.2))
            p.drawRoundedRect(4, 8, 10, 6, 1, 1)
            p.drawRect(7, 6, 4, 2)
            p.drawEllipse(7, 9, 4, 4)
        elif icon_type == 'trash':
            p.setPen(QPen(QColor('#ffffff'), 1.2))
            p.drawRect(5, 7, 8, 8)
            p.drawLine(4, 7, 14, 7)
            p.drawLine(7, 5, 11, 5)
            p.drawLine(7, 9, 7, 13)
            p.drawLine(9, 9, 9, 13)
            p.drawLine(11, 9, 11, 13)
            
        p.end()
        return QIcon(pix)

    def _make_file_icon(self, filename: str) -> QIcon:
        pix = QPixmap(18, 18)
        pix.fill(QColor(0, 0, 0, 0))
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        ext = os.path.splitext(filename)[1].lower()
        color = '#89b4fa'
        if ext in ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp'):
            color = '#a6e3a1'
        elif ext in ('.doc', '.docx', '.txt', '.pdf'):
            color = '#74c7ec'
        elif ext in ('.mp3', '.wav', '.flac', '.ogg'):
            color = '#cba6f7'
        elif ext in ('.mp4', '.avi', '.mkv', '.mov'):
            color = '#f38ba8'
        elif ext in ('.zip', '.rar', '.7z', '.tar'):
            color = '#fab387'
            
        p.setBrush(QBrush(QColor(color)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(2, 1, 14, 16, 2, 2)
        
        p.setBrush(QBrush(QColor('#ffffff')))
        p.drawPolygon([QPoint(12, 1), QPoint(16, 5), QPoint(12, 5)])
        
        p.end()
        return QIcon(pix)

    def _load_files(self) -> None:
        sync_folder = self._config.sync_folder
        items: List[FileItem] = []

        if self._current_path == '__quick_access__':
            items = self._load_recent_files()
        elif self._current_path == '__trash__':
            items = []
        else:
            current_abs = os.path.join(sync_folder, self._current_path)
            if os.path.isdir(current_abs):
                try:
                    entries = os.listdir(current_abs)
                except PermissionError:
                    entries = []
                for name in entries:
                    if name.startswith('.') or name.startswith('~') or name == 'desktop.ini':
                        continue
                    full = os.path.join(current_abs, name)
                    rel = os.path.relpath(full, sync_folder).replace('\\', '/')
                    is_dir = os.path.isdir(full)
                    try:
                        db_item = self._database.get_file(rel)
                    except Exception:
                        db_item = None
                    try:
                        stat = os.stat(full)
                    except OSError:
                        stat = None
                    fi = FileItem(
                        path=rel, name=name,
                        size=stat.st_size if stat and not is_dir else 0,
                        modified=datetime.fromtimestamp(stat.st_mtime) if stat else None,
                        is_dir=is_dir,
                        cloud_id=db_item.cloud_id if db_item else None,
                        sync_status=db_item.sync_status if db_item else SyncStatus.PENDING,
                    )
                    items.append(fi)

        if self._current_path != '__quick_access__':
            items = self._sort_items(items)

        if self._config.view_mode == 'tiles':
            self._populate_tiles(items)
        else:
            self._populate_table(items)

        if self._current_path == '__trash__':
            self._status_label.setText('В Корзине пусто')
        else:
            dirs_count = sum(1 for i in items if i.is_dir)
            files_count = sum(1 for i in items if not i.is_dir)
            total_size = sum(i.size for i in items if not i.is_dir)
            parts = []
            if dirs_count:
                parts.append(f'{dirs_count} папок')
            if files_count:
                parts.append(f'{files_count} файлов')
            if total_size:
                parts.append(self._format_size(total_size))
            self._status_label.setText(', '.join(parts) if parts else 'Пусто')

    def _sort_items(self, items: List[FileItem]) -> List[FileItem]:
        dirs = [i for i in items if i.is_dir]
        files = [i for i in items if not i.is_dir]
        key_map = {
            'name': lambda x: x.name.lower(),
            'date': lambda x: x.modified or datetime.min,
            'size': lambda x: x.size,
        }
        key_fn = key_map.get(self._config.sort_by, key_map['name'])
        rev = self._config.sort_order == 'desc'
        dirs.sort(key=lambda x: x.name.lower(), reverse=rev)
        files.sort(key=key_fn, reverse=rev)
        return dirs + files

    def _populate_tiles(self, items: List[FileItem]) -> None:
        self._file_cards.clear()
        old = self._tiles_scroll.takeWidget()
        if old:
            old.deleteLater()
        container = QWidget()
        layout = FlowLayout(container, 12, 10, 10)

        tile_size = 'large'
        if self._config.view_mode == 'tiles_normal':
            tile_size = 'normal'
        elif self._config.view_mode == 'tiles_huge':
            tile_size = 'huge'

        for fi in items:
            card = FileCard(fi, self._config.sync_folder, size_mode=tile_size)
            card.double_clicked.connect(self._on_file_activated)
            card.context_menu_requested.connect(self._on_card_context_menu)
            layout.addWidget(card)
            self._file_cards.append(card)
        self._tiles_scroll.setWidget(container)

    def _populate_table(self, items: List[FileItem]) -> None:
        self._table.setRowCount(len(items))
        for row, fi in enumerate(items):
            name_item = QTableWidgetItem(fi.name)
            name_item.setData(Qt.ItemDataRole.UserRole, fi)
            if fi.is_dir:
                folder_type = self._get_folder_type(fi.name)
                color = '#ffcc00' if self._config.theme != 'dark' else '#f9e2af'
                name_item.setIcon(self._make_folder_icon(color, folder_type))
            else:
                name_item.setIcon(self._make_file_icon(fi.name))
            self._table.setItem(row, 0, name_item)
            self._table.setItem(row, 1, QTableWidgetItem(
                '' if fi.is_dir else self._format_size(fi.size)))
            self._table.setItem(row, 2, QTableWidgetItem(
                fi.modified.strftime('%d.%m.%Y %H:%M') if fi.modified else ''))
            status_map = {SyncStatus.SYNCED: '✓', SyncStatus.UPLOADING: '↑',
                          SyncStatus.DOWNLOADING: '↓', SyncStatus.ERROR: '✗',
                          SyncStatus.PENDING: '…', SyncStatus.CONFLICT: '⚡'}
            si = QTableWidgetItem(status_map.get(fi.sync_status, ''))
            si.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 3, si)

    # ── Дополнительные методы ─────────────────────────────────────────

    def _load_recent_files(self) -> List[FileItem]:
        try:
            all_files = self._database.get_all_files()
            files = [f for f in all_files if not f.is_dir and f.modified]
            files.sort(key=lambda x: x.modified, reverse=True)
            return files[:25]
        except Exception as e:
            logger.error('Ошибка загрузки последних файлов: %s', e)
            return []

    def _go_back(self) -> None:
        if self._history_back:
            path = self._history_back.pop()
            self._history_forward.append(self._current_path)
            self.navigate_to(path, add_to_history=False)

    def _go_forward(self) -> None:
        if self._history_forward:
            path = self._history_forward.pop()
            self._history_back.append(self._current_path)
            self.navigate_to(path, add_to_history=False)

    def _update_history_buttons(self) -> None:
        if hasattr(self, '_back_btn'):
            self._back_btn.setEnabled(len(self._history_back) > 0)
        if hasattr(self, '_forward_btn'):
            self._forward_btn.setEnabled(len(self._history_forward) > 0)

    def _select_current_tree_item(self) -> None:
        self._folder_tree.clearSelection()
        
        def search_item(parent_item) -> bool:
            for i in range(parent_item.childCount()):
                child = parent_item.child(i)
                path = child.data(0, Qt.ItemDataRole.UserRole)
                if path == self._current_path:
                    self._folder_tree.setCurrentItem(child)
                    child.setSelected(True)
                    return True
                if search_item(child):
                    return True
            return False

        for i in range(self._folder_tree.topLevelItemCount()):
            top_item = self._folder_tree.topLevelItem(i)
            path = top_item.data(0, Qt.ItemDataRole.UserRole)
            if path == self._current_path:
                self._folder_tree.setCurrentItem(top_item)
                top_item.setSelected(True)
                break
            if search_item(top_item):
                break

    def _on_logout(self) -> None:
        reply = QMessageBox.question(
            self,
            'Выйти из аккаунта',
            'Вы действительно хотите выйти из текущего аккаунта Google?\n\n'
            'Синхронизация будет остановлена, и приложение завершит работу. '
            'При следующем запуске потребуется войти заново.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._auth_manager.logout()
            self.logout_requested.emit()

    def _on_promo_clicked(self) -> None:
        import webbrowser
        webbrowser.open('https://one.google.com/storage')

    def _on_file_activated(self, file_item: FileItem) -> None:
        if file_item.is_dir:
            self.navigate_to(file_item.path)
        else:
            abs_path = os.path.join(self._config.sync_folder, file_item.path)
            if os.path.exists(abs_path):
                os.startfile(abs_path)

    def _on_table_double_click(self, index) -> None:
        item = self._table.item(index.row(), 0)
        if item:
            fi = item.data(Qt.ItemDataRole.UserRole)
            if fi:
                self._on_file_activated(fi)

    def _on_card_context_menu(self, file_item: FileItem, pos) -> None:
        self._show_file_context_menu(file_item, pos)

    def _on_table_context_menu(self, pos: QPoint) -> None:
        item = self._table.itemAt(pos)
        if not item:
            return
        row_item = self._table.item(item.row(), 0)
        if row_item:
            fi = row_item.data(Qt.ItemDataRole.UserRole)
            if fi:
                self._show_file_context_menu(fi, self._table.viewport().mapToGlobal(pos))

    def _show_file_context_menu(self, file_item: FileItem, pos) -> None:
        menu = QMenu(self)
        menu.setStyleSheet('''
            QMenu { background: #313244; color: #cdd6f4; border: 1px solid #45475a;
                border-radius: 6px; padding: 4px; font-family: 'Segoe UI'; }
            QMenu::item { padding: 6px 24px; border-radius: 4px; }
            QMenu::item:selected { background: #45475a; }
            QMenu::separator { height: 1px; background: #45475a; margin: 4px 8px; }
        ''')
        menu.addAction('Открыть').triggered.connect(lambda: self._on_file_activated(file_item))
        if not file_item.is_dir:
            menu.addAction('Скопировать ссылку').triggered.connect(
                lambda: self._copy_share_link(file_item))
        menu.addSeparator()
        menu.addAction('Переименовать').triggered.connect(lambda: self._rename_file(file_item))
        menu.addAction('Удалить').triggered.connect(lambda: self._delete_file(file_item))
        menu.addSeparator()
        menu.addAction('Свойства').triggered.connect(lambda: self._show_properties(file_item))
        menu.exec(pos)

    # ── Действия с файлами ────────────────────────────────────────────

    def _copy_share_link(self, file_item: FileItem) -> None:
        if not file_item.cloud_id:
            QMessageBox.warning(self, 'Ошибка', 'Файл ещё не синхронизирован с облаком.')
            return
        try:
            link = self._drive_api.create_share_link(file_item.cloud_id)
            QApplication.clipboard().setText(link)
            self._status_label.setText(f'Ссылка скопирована: {link}')
        except Exception as e:
            QMessageBox.warning(self, 'Ошибка', f'Не удалось создать ссылку: {e}')

    def _rename_file(self, file_item: FileItem) -> None:
        from PyQt6.QtWidgets import QInputDialog
        new_name, ok = QInputDialog.getText(self, 'Переименовать', 'Новое имя:', text=file_item.name)
        if ok and new_name and new_name != file_item.name:
            abs_old = os.path.join(self._config.sync_folder, file_item.path)
            abs_new = os.path.join(os.path.dirname(abs_old), new_name)
            try:
                os.rename(abs_old, abs_new)
                self.refresh()
            except OSError as e:
                QMessageBox.warning(self, 'Ошибка', f'Не удалось переименовать: {e}')

    def _delete_file(self, file_item: FileItem) -> None:
        typ = 'папку' if file_item.is_dir else 'файл'
        reply = QMessageBox.question(
            self, 'Подтверждение',
            f'Удалить {typ} «{file_item.name}»?\n\nОн будет удалён и из облака.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            abs_path = os.path.join(self._config.sync_folder, file_item.path)
            try:
                if file_item.is_dir:
                    shutil.rmtree(abs_path)
                else:
                    os.remove(abs_path)
                self.refresh()
            except OSError as e:
                QMessageBox.warning(self, 'Ошибка', f'Не удалось удалить: {e}')

    def _show_properties(self, file_item: FileItem) -> None:
        abs_path = os.path.join(self._config.sync_folder, file_item.path)
        props = [f'Имя: {file_item.name}', f'Путь: {abs_path}',
                 f'Размер: {self._format_size(file_item.size)}']
        if file_item.modified:
            props.append(f'Изменён: {file_item.modified.strftime("%d.%m.%Y %H:%M:%S")}')
        props.append(f'Тип: {"Папка" if file_item.is_dir else file_item.mime_type or "Файл"}')
        props.append(f'Статус: {file_item.sync_status.name}')
        if file_item.cloud_id:
            props.append(f'Cloud ID: {file_item.cloud_id}')
        QMessageBox.information(self, f'Свойства — {file_item.name}', '\n'.join(props))

    # ── Обновление статусов ───────────────────────────────────────────

    def _update_file_status(self, path: str, status) -> None:
        for card in self._file_cards:
            if card.file_item.path == path:
                card.file_item.sync_status = status
                QTimer.singleShot(500, self.refresh)
                return
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item:
                fi = item.data(Qt.ItemDataRole.UserRole)
                if fi and fi.path == path:
                    status_map = {SyncStatus.SYNCED: '✓', SyncStatus.UPLOADING: '↑',
                                  SyncStatus.DOWNLOADING: '↓', SyncStatus.ERROR: '✗'}
                    si = self._table.item(row, 3)
                    if si:
                        si.setText(status_map.get(status, ''))
                    return

    def _on_sync_progress(self, done: int, total: int) -> None:
        if total > 0:
            self._status_label.setText(f'Синхронизация: {done}/{total} файлов')
        else:
            self._status_label.setText('Синхронизировано')

    def _on_search(self, text: str) -> None:
        t = text.lower()
        if self._config.view_mode.startswith('tiles'):
            for card in self._file_cards:
                card.setVisible(t in card.file_item.name.lower())
        else:
            for row in range(self._table.rowCount()):
                item = self._table.item(row, 0)
                self._table.setRowHidden(row, not (t in item.text().lower()) if item else False)

    def _on_sort_changed(self, index: int) -> None:
        self._config.sort_by = {0: 'name', 1: 'date', 2: 'size'}.get(index, 'name')
        self._config.save()
        self._load_files()

    def _get_view_mode_icon(self, mode: str) -> str:
        mapping = {
            'list': '☰ ▾',
            'tiles_normal': '▤ ▾',
            'tiles_large': '▦ ▾',
            'tiles_huge': '▰ ▾',
        }
        return mapping.get(mode, '▦ ▾')

    def _update_view_menu(self) -> None:
        if not hasattr(self, '_view_menu'):
            return
        self._view_menu.clear()
        from PyQt6.QtGui import QActionGroup
        group = QActionGroup(self)

        modes = [
            ('list', 'Таблица'),
            ('tiles_normal', 'Обычные значки'),
            ('tiles_large', 'Крупные значки'),
            ('tiles_huge', 'Огромные значки')
        ]

        current_mode = self._config.view_mode
        for mode_id, label in modes:
            action = self._view_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(mode_id == current_mode)
            action.setActionGroup(group)
            action.triggered.connect(lambda checked, m=mode_id: self._set_view_mode(m))

    def _set_view_mode(self, mode: str) -> None:
        self._config.view_mode = mode
        self._config.save()

        if hasattr(self, '_view_mode_btn'):
            self._view_mode_btn.setText(self._get_view_mode_icon(mode))
            self._update_view_menu()

        self._tiles_scroll.setVisible(mode.startswith('tiles'))
        self._table.setVisible(mode == 'list')
        self._load_files()

    def _on_sync_now(self) -> None:
        try:
            if not self._sync_engine.is_running:
                self._status_label.setText('Запуск синхронизации...')
                self._sync_engine.start()
            else:
                self._sync_engine.sync_now()
                self._status_label.setText('Запущена синхронизация...')
        except Exception as e:
            logger.error('Ошибка синхронизации: %s', e)

    def _on_toggle_sync(self) -> None:
        try:
            if not self._sync_engine.is_running:
                self._sync_engine.start()
            elif self._sync_engine.is_paused:
                self._sync_engine.resume()
            else:
                self._sync_engine.pause()
        except Exception as e:
            logger.error('Ошибка переключения синхронизации: %s', e)

    def _on_status_changed(self, status: str) -> None:
        # status: 'synced' | 'syncing' | 'error' | 'paused' | 'offline'
        status_text_map = {
            'offline': 'Синхронизация отключена',
            'paused': 'Синхронизация приостановлена',
            'syncing': 'Синхронизация...',
            'synced': 'Синхронизировано',
            'error': 'Ошибка синхронизации',
        }
        self._status_label.setText(status_text_map.get(status, 'Готово'))

        # Дефолтные цвета на случай, если self._theme_colors еще не созданы
        c = getattr(self, '_theme_colors', {
            'bg_surface0': '#313244',
            'bg_surface1': '#45475a',
            'border': '#45475a',
            'green': '#a6e3a1',
            'red': '#f38ba8'
        })

        # Обновить кнопку старт/пауза
        if status in ('paused', 'offline'):
            self._toggle_sync_btn.setText('▶')
            self._toggle_sync_btn.setToolTip('Запустить автосинхронизацию')
            self._toggle_sync_btn.setStyleSheet(f'''
                QToolButton {{ background: {c["bg_surface0"]}; color: {c["green"]}; border: 1px solid {c["border"]};
                    border-radius: 6px; font-size: 14px; }}
                QToolButton:hover {{ background: {c["bg_surface1"]}; }}
            ''')
        else:
            self._toggle_sync_btn.setText('⏸')
            self._toggle_sync_btn.setToolTip('Приостановить автосинхронизацию')
            self._toggle_sync_btn.setStyleSheet(f'''
                QToolButton {{ background: {c["bg_surface0"]}; color: {c["red"]}; border: 1px solid {c["border"]};
                    border-radius: 6px; font-size: 14px; }}
                QToolButton:hover {{ background: {c["bg_surface1"]}; }}
            ''')

    # ── Drag-and-drop ─────────────────────────────────────────────────

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        if not event.mimeData().hasUrls():
            return
        target_dir = os.path.join(self._config.sync_folder, self._current_path)
        copied = 0
        for url in event.mimeData().urls():
            src = url.toLocalFile()
            if not src or not os.path.exists(src):
                continue
            dst = os.path.join(target_dir, os.path.basename(src))
            try:
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)
                copied += 1
            except Exception as e:
                logger.error('Ошибка копирования %s: %s', src, e)
        if copied:
            self._status_label.setText(f'Скопировано файлов: {copied}')
            QTimer.singleShot(500, self.refresh)
        event.acceptProposedAction()

    # ── Утилиты ───────────────────────────────────────────────────────

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        if size_bytes == 0:
            return '0 Б'
        units = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ']
        i = 0
        size = float(size_bytes)
        while size >= 1024 and i < len(units) - 1:
            size /= 1024
            i += 1
        return f'{int(size)} {units[i]}' if i == 0 else f'{size:.1f} {units[i]}'

    def _restore_geometry(self) -> None:
        geom = self._config.window_geometry
        if geom:
            try:
                self.restoreGeometry(geom)
            except Exception:
                pass

    def _save_geometry(self) -> None:
        try:
            self._config.window_geometry = self.saveGeometry().data()
            self._config.save()
        except Exception:
            pass

    def closeEvent(self, event) -> None:
        """Свернуть в трей вместо закрытия."""
        self._save_geometry()
        event.ignore()
        self.hide()

    def update_storage(self, used: int, total: int) -> None:
        """Обновить индикатор хранилища."""
        used_gb = used / (1024 ** 3)
        total_gb = total / (1024 ** 3)
        pct = (used / total * 100) if total > 0 else 0
        self._storage_bar.setValue(int(pct))
        self._storage_text.setText(f'{used_gb:.1f} ГБ из {total_gb:.1f} ГБ')
