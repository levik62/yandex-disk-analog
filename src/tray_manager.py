# -*- coding: utf-8 -*-
"""
Менеджер системного трея — точная копия поведения Яндекс.Диска.

Управляет иконкой в системном трее, контекстным меню,
анимацией статуса синхронизации и всплывающими уведомлениями.
Все иконки генерируются программно через QPainter (без файлов).
"""

import os
import math
import subprocess
from typing import Optional, List

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, Qt, QPoint, QRectF, QPointF
from PyQt6.QtGui import (
    QIcon, QPixmap, QPainter, QColor, QPen, QBrush,
    QFont, QPainterPath, QAction, QPolygonF,
    QRadialGradient, QLinearGradient
)
from PyQt6.QtWidgets import QSystemTrayIcon, QMenu, QMessageBox

# Путь к редактору скриншотов Яндекс.Диска
_appdata = os.environ.get("APPDATA", "")
ЯНДЕКС_SCREENSHOT_EDITOR = os.path.join(
    _appdata, "Yandex", "YandexDisk2", "3.2.47.5133", "YandexDiskScreenshotEditor.exe"
)

# Размер генерируемых иконок (пиксели)
ICON_SIZE: int = 64

# Интервал анимации синхронизации (мс)
ANIMATION_INTERVAL: int = 300

# Количество кадров анимации синхронизации
ANIMATION_FRAMES: int = 8


class TrayManager(QObject):
    """
    Менеджер системного трея.

    Отображает иконку в системном трее с контекстным меню,
    анимированными статусами синхронизации и balloon-уведомлениями.
    Полностью воспроизводит поведение Яндекс.Диска.

    Signals:
        show_main_window: Показать главное окно приложения.
        hide_main_window: Скрыть главное окно приложения.
        open_settings: Открыть окно настроек.
        quit_app: Завершить приложение.
        pause_sync: Приостановить синхронизацию.
        resume_sync: Возобновить синхронизацию.
        take_screenshot: Сделать скриншот.
    """

    # --- Сигналы ---
    show_main_window = pyqtSignal()
    hide_main_window = pyqtSignal()
    open_settings = pyqtSignal()
    quit_app = pyqtSignal()
    pause_sync = pyqtSignal()
    resume_sync = pyqtSignal()
    take_screenshot = pyqtSignal()

    def __init__(
        self,
        sync_engine: 'SyncEngine',
        config: 'Config',
        auth_manager: 'AuthManager',
        parent: Optional[QObject] = None
    ) -> None:
        """
        Инициализация менеджера системного трея.

        Args:
            sync_engine: Движок синхронизации для получения статуса.
            config: Конфигурация приложения (путь к папке синхронизации и т.д.).
            auth_manager: Менеджер авторизации для информации о пользователе.
            parent: Родительский QObject.
        """
        super().__init__(parent)

        self._sync_engine = sync_engine
        self._config = config
        self._auth_manager = auth_manager

        # Текущее состояние
        self._current_status: str = 'synced'
        self._pending_count: int = 0
        self._storage_used_gb: float = 0.0
        self._storage_total_gb: float = 0.0
        self._is_main_window_visible: bool = False

        # Анимация синхронизации
        self._animation_frame: int = 0
        self._syncing_frames: List[QIcon] = []
        self._animation_timer: QTimer = QTimer(self)
        self._animation_timer.setInterval(ANIMATION_INTERVAL)
        self._animation_timer.timeout.connect(self._next_animation_frame)

        # Генерация иконок
        self._icon_synced: QIcon = self._create_synced_icon()
        self._icon_error: QIcon = self._create_error_icon()
        self._icon_paused: QIcon = self._create_paused_icon()
        self._syncing_frames = self._create_syncing_frames()

        # Системный трей
        self._tray_icon: QSystemTrayIcon = QSystemTrayIcon(self)
        self._tray_icon.setIcon(self._icon_synced)
        self._tray_icon.setToolTip('Google Drive Sync — Синхронизировано')
        self._tray_icon.activated.connect(self._on_tray_activated)

        # Контекстное меню
        self._menu: QMenu = QMenu()
        self._build_menu()

        self._tray_icon.setContextMenu(self._menu)

        # Подключение сигналов sync_engine
        self._sync_engine.status_changed.connect(self._on_status_changed)
        self._sync_engine.sync_progress.connect(self._on_sync_progress)
        self._sync_engine.sync_error.connect(self._on_sync_error)

    @staticmethod
    def _create_synced_icon() -> QIcon:
        """
        Создать иконку «синхронизировано» — жёлтая летающая тарелка с зелёным кружком и галочкой.
        """
        pixmap = QPixmap(ICON_SIZE, ICON_SIZE)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Рисуем тарелку
        painter.save()
        painter.translate(4, 4)
        draw_yellow_saucer(painter, 56)
        painter.restore()

        # Зелёный кружок в углу
        badge_x, badge_y = 52.0, 52.0
        badge_r = 8.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(76, 175, 80)))  # Material Green 500
        painter.drawEllipse(QPointF(badge_x, badge_y), badge_r, badge_r)

        # Белая галочка
        pen = QPen(QColor(255, 255, 255))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        path = QPainterPath()
        path.moveTo(badge_x - 3.5, badge_y)
        path.lineTo(badge_x - 1.0, badge_y + 2.5)
        path.lineTo(badge_x + 3.5, badge_y - 2.5)
        painter.drawPath(path)

        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _create_error_icon() -> QIcon:
        """
        Создать иконку «ошибка» — жёлтая летающая тарелка с красным кружком и крестиком.
        """
        pixmap = QPixmap(ICON_SIZE, ICON_SIZE)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Рисуем тарелку
        painter.save()
        painter.translate(4, 4)
        draw_yellow_saucer(painter, 56)
        painter.restore()

        # Красный кружок в углу
        badge_x, badge_y = 52.0, 52.0
        badge_r = 8.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(244, 67, 54)))  # Material Red 500
        painter.drawEllipse(QPointF(badge_x, badge_y), badge_r, badge_r)

        # Белый крестик
        pen = QPen(QColor(255, 255, 255))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)

        painter.drawLine(QPointF(badge_x - 2.5, badge_y - 2.5), QPointF(badge_x + 2.5, badge_y + 2.5))
        painter.drawLine(QPointF(badge_x + 2.5, badge_y - 2.5), QPointF(badge_x - 2.5, badge_y + 2.5))

        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _create_paused_icon() -> QIcon:
        """
        Создать иконку «пауза» — жёлтая летающая тарелка с серым кружком и знаком паузы.
        """
        pixmap = QPixmap(ICON_SIZE, ICON_SIZE)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Рисуем тарелку
        painter.save()
        painter.translate(4, 4)
        draw_yellow_saucer(painter, 56)
        painter.restore()

        # Серый кружок в углу
        badge_x, badge_y = 52.0, 52.0
        badge_r = 8.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(158, 158, 158)))  # Material Grey 500
        painter.drawEllipse(QPointF(badge_x, badge_y), badge_r, badge_r)

        # Две белые вертикальные полоски
        painter.setBrush(QBrush(QColor(255, 255, 255)))
        painter.drawRect(QRectF(badge_x - 2.5, badge_y - 3.5, 1.8, 7.0))
        painter.drawRect(QRectF(badge_x + 0.7, badge_y - 3.5, 1.8, 7.0))

        painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _create_syncing_frames() -> List[QIcon]:
        """
        Создать кадры анимации синхронизации — жёлтая летающая тарелка с вращающимися синими стрелками вокруг.
        """
        frames: List[QIcon] = []

        for frame_idx in range(ANIMATION_FRAMES):
            pixmap = QPixmap(ICON_SIZE, ICON_SIZE)
            pixmap.fill(Qt.GlobalColor.transparent)

            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            # 1. Рисуем статичную жёлтую тарелку в центре (размер 46, чтобы стрелки были дальше)
            painter.save()
            painter.translate(9, 9)
            draw_yellow_saucer(painter, 46)
            painter.restore()

            # 2. Рисуем вращающиеся синие стрелки вокруг тарелки
            center_x = ICON_SIZE / 2.0
            center_y = ICON_SIZE / 2.0
            angle = (360.0 / ANIMATION_FRAMES) * frame_idx

            painter.save()
            painter.translate(center_x, center_y)
            painter.rotate(angle)
            painter.translate(-center_x, -center_y)

            arrow_color = QColor(33, 150, 243)  # Material Blue 500
            radius = 25.0

            _draw_circular_arrow(
                painter, center_x, center_y, radius,
                start_angle=210, sweep_angle=120,
                arrow_color=arrow_color, clockwise=True
            )

            _draw_circular_arrow(
                painter, center_x, center_y, radius,
                start_angle=30, sweep_angle=120,
                arrow_color=arrow_color, clockwise=True
            )
            painter.restore()

            painter.end()
            frames.append(QIcon(pixmap))

        return frames

    # =========================================================================
    # Построение контекстного меню
    # =========================================================================

    def _build_menu(self) -> None:
        """
        Построить контекстное меню трея.

        Создаёт все пункты меню с соответствующими действиями,
        разделителями и неактивными пунктами статуса.
        """
        self._menu.clear()

        # --- Открыть приложение ---
        action_open_app = QAction('Открыть Google Drive Sync', self._menu)
        action_open_app.triggered.connect(self.show_main_window.emit)
        self._menu.addAction(action_open_app)

        # --- Открыть папку на компьютере ---
        action_open_folder = QAction('Открыть папку на компьютере', self._menu)
        action_open_folder.triggered.connect(self._open_sync_folder)
        self._menu.addAction(action_open_folder)

        self._menu.addSeparator()

        # --- Сделать скриншот ---
        action_screenshot = QAction('Сделать скриншот', self._menu)
        action_screenshot.triggered.connect(self._launch_screenshot_editor)
        self._menu.addAction(action_screenshot)

        self._menu.addSeparator()

        # --- Статус синхронизации (disabled) ---
        self._status_action = QAction('✓ Синхронизировано', self._menu)
        self._status_action.setEnabled(False)
        self._menu.addAction(self._status_action)

        # --- Информация о хранилище (disabled) ---
        self._storage_action = QAction('Занято: 0 ГБ из 0 ГБ', self._menu)
        self._storage_action.setEnabled(False)
        self._menu.addAction(self._storage_action)

        self._menu.addSeparator()

        # --- Приостановить / Возобновить синхронизацию ---
        self._pause_resume_action = QAction('Приостановить синхронизацию', self._menu)
        self._pause_resume_action.triggered.connect(self._toggle_pause_resume)
        self._menu.addAction(self._pause_resume_action)

        # --- Настройки ---
        action_settings = QAction('Настройки...', self._menu)
        action_settings.triggered.connect(self.open_settings.emit)
        self._menu.addAction(action_settings)

        # --- Выйти из аккаунта ---
        action_logout = QAction('Выйти из аккаунта Google...', self._menu)
        action_logout.triggered.connect(self._on_logout_clicked)
        self._menu.addAction(action_logout)

        # --- Выход ---
        action_quit = QAction('Выход', self._menu)
        action_quit.triggered.connect(self.quit_app.emit)
        self._menu.addAction(action_quit)

    # =========================================================================
    # Публичные методы
    # =========================================================================

    def update_status(self, status: str) -> None:
        """
        Обновить иконку и текст статуса в трее.

        Args:
            status: Статус синхронизации.
                    Допустимые значения: 'synced', 'syncing', 'error', 'paused'.
        """
        self._current_status = status
        self._pending_count = self._sync_engine.get_pending_count()

        if status == 'synced':
            self._stop_animation()
            self._tray_icon.setIcon(self._icon_synced)
            self._tray_icon.setToolTip('Google Drive Sync — Синхронизировано')
            self._status_action.setText('✓ Синхронизировано')
            self._pause_resume_action.setText('Приостановить синхронизацию')

        elif status == 'syncing':
            self._start_animation()
            count = self._pending_count
            tooltip = f'Google Drive Sync — Синхронизация ({count} файлов)'
            self._tray_icon.setToolTip(tooltip)
            self._status_action.setText(f'↕ Синхронизация ({count} файлов)')
            self._pause_resume_action.setText('Приостановить синхронизацию')

        elif status == 'error':
            self._stop_animation()
            self._tray_icon.setIcon(self._icon_error)
            self._tray_icon.setToolTip('Google Drive Sync — Ошибка синхронизации')
            self._status_action.setText('✗ Ошибка')
            self._pause_resume_action.setText('Приостановить синхронизацию')

        elif status == 'paused':
            self._stop_animation()
            self._tray_icon.setIcon(self._icon_paused)
            self._tray_icon.setToolTip('Google Drive Sync — Синхронизация приостановлена')
            self._status_action.setText('⏸ Приостановлено')
            self._pause_resume_action.setText('Возобновить синхронизацию')

    def update_storage(self, used_gb: float, total_gb: float) -> None:
        """
        Обновить информацию о занятом пространстве хранилища.

        Args:
            used_gb: Использовано гигабайт.
            total_gb: Всего доступно гигабайт.
        """
        self._storage_used_gb = used_gb
        self._storage_total_gb = total_gb
        self._storage_action.setText(
            f'Занято: {used_gb:.1f} ГБ из {total_gb:.1f} ГБ'
        )

    def show_notification(self, title: str, message: str) -> None:
        """
        Показать всплывающее balloon-уведомление в системном трее.

        Args:
            title: Заголовок уведомления.
            message: Текст уведомления.
        """
        self._tray_icon.showMessage(
            title,
            message,
            QSystemTrayIcon.MessageIcon.Information,
            5000  # Длительность показа (мс)
        )

    def show(self) -> None:
        """Показать иконку в системном трее."""
        self._tray_icon.show()

    def hide(self) -> None:
        """Скрыть иконку из системного трея."""
        self._tray_icon.hide()

    def set_main_window_visible(self, visible: bool) -> None:
        """
        Установить состояние видимости главного окна.

        Используется для корректной работы toggle при клике на иконку.

        Args:
            visible: True, если главное окно видимо.
        """
        self._is_main_window_visible = visible

    # =========================================================================
    # Обработчики событий трея
    # =========================================================================

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """
        Обработка активации иконки в трее (клик, двойной клик и т.д.).

        Args:
            reason: Причина активации (тип клика).
        """
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            # Одинарный левый клик — toggle show/hide
            if self._is_main_window_visible:
                self.hide_main_window.emit()
            else:
                self.show_main_window.emit()

        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            # Двойной клик — всегда показать
            self.show_main_window.emit()

    # =========================================================================
    # Обработчики сигналов SyncEngine
    # =========================================================================

    def _on_status_changed(self, status: str) -> None:
        """
        Обработка изменения статуса синхронизации от SyncEngine.

        Args:
            status: Новый статус ('synced', 'syncing', 'error', 'paused').
        """
        self.update_status(status)

    def _on_sync_progress(self, done: int, total: int) -> None:
        """
        Обработка прогресса синхронизации.

        Обновляет количество оставшихся файлов в статусе трея.

        Args:
            done: Количество обработанных файлов.
            total: Общее количество файлов для синхронизации.
        """
        remaining = total - done
        self._pending_count = remaining

        if self._current_status == 'syncing':
            if remaining > 0:
                self._status_action.setText(f'↕ Синхронизация ({remaining} файлов)')
                self._tray_icon.setToolTip(
                    f'Google Drive Sync — Синхронизация ({remaining} файлов)'
                )
            else:
                self._status_action.setText('✓ Синхронизировано')
                self._tray_icon.setToolTip('Google Drive Sync — Синхронизировано')

    def _on_sync_error(self, error_message: str) -> None:
        """
        Обработка ошибки синхронизации.

        Показывает balloon-уведомление с текстом ошибки.

        Args:
            error_message: Текст ошибки.
        """
        self.show_notification('Ошибка синхронизации', error_message)

    # =========================================================================
    # Действия контекстного меню
    # =========================================================================

    def _open_sync_folder(self) -> None:
        """Открыть папку синхронизации в проводнике Windows."""
        sync_folder = self._config.sync_folder
        if sync_folder and os.path.isdir(sync_folder):
            os.startfile(sync_folder)
        else:
            self.show_notification(
                'Ошибка',
                f'Папка синхронизации не найдена: {sync_folder}'
            )

    def _launch_screenshot_editor(self) -> None:
        """
        Запустить редактор скриншотов Яндекс.Диска.

        Если исполняемый файл не найден, показывает предупреждение.
        """
        self.take_screenshot.emit()

        if os.path.isfile(ЯНДЕКС_SCREENSHOT_EDITOR):
            try:
                subprocess.Popen(
                    [ЯНДЕКС_SCREENSHOT_EDITOR],
                    creationflags=subprocess.DETACHED_PROCESS
                )
            except OSError as e:
                self.show_notification(
                    'Ошибка запуска',
                    f'Не удалось запустить редактор скриншотов:\n{e}'
                )
        else:
            QMessageBox.warning(
                None,
                'Скриншот',
                f'Редактор скриншотов не найден:\n{ЯНДЕКС_SCREENSHOT_EDITOR}\n\n'
                'Убедитесь, что Яндекс.Диск установлен.'
            )

    def _toggle_pause_resume(self) -> None:
        """Переключить приостановку/возобновление синхронизации."""
        if self._sync_engine.is_paused:
            self._sync_engine.resume()
            self.resume_sync.emit()
        else:
            self._sync_engine.pause()
            self.pause_sync.emit()

    def _on_logout_clicked(self) -> None:
        """
        Обработка нажатия на кнопку выхода из аккаунта.
        Запрашивает подтверждение, стирает токен и выходит.
        """
        reply = QMessageBox.question(
            None,
            'Выйти из аккаунта',
            'Вы действительно хотите выйти из текущего аккаунта Google?\n\n'
            'Синхронизация будет остановлена, и приложение завершит работу. '
            'При следующем запуске потребуется войти заново.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._auth_manager.logout()
            self.quit_app.emit()

    # =========================================================================
    # Анимация синхронизации
    # =========================================================================

    def _start_animation(self) -> None:
        """Запустить анимацию иконки синхронизации."""
        if not self._animation_timer.isActive():
            self._animation_frame = 0
            self._tray_icon.setIcon(self._syncing_frames[0])
            self._animation_timer.start()

    def _stop_animation(self) -> None:
        """Остановить анимацию иконки синхронизации."""
        if self._animation_timer.isActive():
            self._animation_timer.stop()
            self._animation_frame = 0

    def _next_animation_frame(self) -> None:
        """
        Переключить на следующий кадр анимации.

        Вызывается по таймеру каждые ANIMATION_INTERVAL мс.
        Циклически переключает кадры анимации стрелок синхронизации.
        """
        self._animation_frame = (self._animation_frame + 1) % ANIMATION_FRAMES
        self._tray_icon.setIcon(self._syncing_frames[self._animation_frame])


# =============================================================================
# Вспомогательные функции рисования
# =============================================================================

def draw_yellow_saucer(painter: QPainter, size: int) -> None:
    """
    Нарисовать жёлтую летающую тарелку (бананового цвета) с 3D градиентом и свечением.
    """
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.translate(size / 2.0, size / 2.0)
    painter.scale((size / 256.0) * 1.38, (size / 256.0) * 1.38)
    painter.rotate(-25) # Наклон 25 градусов для соответствия Яндекс.Диску
    
    # Свечение снизу
    glow = QRadialGradient(0, 35, 75)
    glow.setColorAt(0.0, QColor(255, 255, 255, 255))
    glow.setColorAt(0.4, QColor(255, 255, 220, 180))
    glow.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.setBrush(QBrush(glow))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(-65, 18, 130, 45)
    
    # Купол тарелки (кабина) - более выраженный и высокий
    dome_grad = QLinearGradient(-35, -55, 35, 5)
    dome_grad.setColorAt(0.0, QColor("#ffeb3b"))
    dome_grad.setColorAt(0.6, QColor("#fbc02d"))
    dome_grad.setColorAt(1.0, QColor("#f57f17"))
    painter.setBrush(QBrush(dome_grad))
    painter.drawEllipse(-35, -55, 70, 60)
    
    # Основной диск тарелки - толще и шире
    body_grad = QLinearGradient(-90, -18, 90, 32)
    body_grad.setColorAt(0.0, QColor("#fff59d"))
    body_grad.setColorAt(0.3, QColor("#fbc02d"))
    body_grad.setColorAt(0.8, QColor("#f9a825"))
    body_grad.setColorAt(1.0, QColor("#e65100"))
    painter.setBrush(QBrush(body_grad))
    painter.drawEllipse(-90, -18, 180, 50)
    
    # Центральный иллюминатор/светодиод снизу
    light_grad = QRadialGradient(0, 5, 25)
    light_grad.setColorAt(0.0, QColor(255, 255, 255, 255))
    light_grad.setColorAt(0.7, QColor(255, 255, 220, 220))
    light_grad.setColorAt(1.0, QColor(255, 248, 200, 0))
    painter.setBrush(QBrush(light_grad))
    painter.drawEllipse(-25, -2, 50, 16)
    
    painter.restore()


def _draw_circular_arrow(
    painter: QPainter,
    cx: float,
    cy: float,
    radius: float,
    start_angle: float,
    sweep_angle: float,
    arrow_color: QColor,
    clockwise: bool = True
) -> None:
    """
    Нарисовать дугу со стрелкой на конце (для анимации синхронизации).

    Рисует кривую дугу вокруг центра (cx, cy) с заданным радиусом
    и добавляет треугольный наконечник стрелки на конце дуги.

    Args:
        painter: QPainter для рисования.
        cx: X-координата центра.
        cy: Y-координата центра.
        radius: Радиус дуги.
        start_angle: Начальный угол дуги (градусы, против часовой стрелки от 3 часов).
        sweep_angle: Угол развёртки дуги (градусы).
        arrow_color: Цвет стрелки.
        clockwise: Направление дуги (по часовой стрелке).
    """
    # Настройка пера для дуги
    pen = QPen(arrow_color)
    pen.setWidth(4)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    # Прямоугольник для дуги
    arc_rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)

    # Qt использует 1/16 градуса для drawArc
    qt_start = int(start_angle * 16)
    qt_sweep = int(sweep_angle * 16)
    if clockwise:
        qt_sweep = -qt_sweep

    painter.drawArc(arc_rect, qt_start, qt_sweep)

    # Вычисление позиции и направления наконечника стрелки
    if clockwise:
        end_angle_deg = start_angle - sweep_angle
    else:
        end_angle_deg = start_angle + sweep_angle

    end_angle_rad = math.radians(end_angle_deg)

    # Позиция конца дуги
    arrow_x = cx + radius * math.cos(end_angle_rad)
    arrow_y = cy - radius * math.sin(end_angle_rad)

    # Направление касательной к дуге в конечной точке
    if clockwise:
        tangent_angle = end_angle_rad - math.pi / 2
    else:
        tangent_angle = end_angle_rad + math.pi / 2

    # Треугольный наконечник
    arrow_size = 8.0
    angle1 = tangent_angle + math.radians(150)
    angle2 = tangent_angle - math.radians(150)

    p1_x = arrow_x + arrow_size * math.cos(angle1)
    p1_y = arrow_y - arrow_size * math.sin(angle1)
    p2_x = arrow_x + arrow_size * math.cos(angle2)
    p2_y = arrow_y - arrow_size * math.sin(angle2)

    # Рисуем заполненный треугольник
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(arrow_color))

    triangle = QPainterPath()
    triangle.moveTo(arrow_x, arrow_y)
    triangle.lineTo(p1_x, p1_y)
    triangle.lineTo(p2_x, p2_y)
    triangle.closeSubpath()

    painter.drawPath(triangle)
