# -*- coding: utf-8 -*-
"""
Менеджер конфигурации Google Drive Sync.

Хранит настройки приложения в XML-файле (аналог Яндекс.Диск settings.xml).
Путь к файлу настроек: %LOCALAPPDATA%\\GoogleDriveSync\\settings.xml
"""

import os
import logging
import base64
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Пути по умолчанию
DEFAULT_APP_DATA_DIR = os.path.join(
    os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
    'GoogleDriveSync'
)
DEFAULT_SETTINGS_PATH = os.path.join(DEFAULT_APP_DATA_DIR, 'settings.xml')
DEFAULT_SYNC_FOLDER = os.path.join(os.path.expanduser('~'), 'YandexDisk')
DEFAULT_DOWNLOADS_FOLDER = os.path.join(
    os.path.expanduser('~'), 'Downloads', 'GoogleDrive.Files'
)


class Config:
    """
    Менеджер конфигурации приложения.

    Загружает/сохраняет настройки в XML-файл.
    Поддерживает property-доступ к каждой настройке,
    а также универсальные get/set.
    """

    # Значения по умолчанию
    DEFAULTS: Dict[str, Any] = {
        'sync_folder': DEFAULT_SYNC_FOLDER,
        'downloads_folder': DEFAULT_DOWNLOADS_FOLDER,
        'auto_start': True,
        'theme': 'system',
        'sync_interval': 60,
        'proxy_mode': 0,
        'proxy_server': '',
        'proxy_port': '',
        'proxy_auth': '',
        'notifications': {
            'file_upload': True,
            'file_download': True,
            'errors': True,
            'low_space': True,
        },
        'hotkeys_enabled': True,
        'hotkeys': {
            'capture_region': 'PrtScr',
            'capture_fullscreen': 'Ctrl+Shift+3',
            'capture_window': 'Ctrl+Shift+4',
        },
        'last_user_name': '',
        'last_user_email': '',
        'window_geometry': b'',
        'view_mode': 'tiles_large',
        'sort_by': 'name',
        'sort_order': 'asc',
        'selective_sync_folders': [],
    }

    def __init__(self, settings_path: Optional[str] = None) -> None:
        """
        Инициализация менеджера конфигурации.

        Args:
            settings_path: Путь к файлу настроек. Если None, используется путь по умолчанию.
        """
        self._settings_path = settings_path or DEFAULT_SETTINGS_PATH
        self._data: Dict[str, Any] = {}
        self._init_defaults()
        self.load()

    def _init_defaults(self) -> None:
        """Инициализирует настройки значениями по умолчанию."""
        import copy
        self._data = copy.deepcopy(self.DEFAULTS)

    @property
    def settings_path(self) -> str:
        """Путь к файлу настроек."""
        return self._settings_path

    @property
    def app_data_dir(self) -> str:
        """Директория данных приложения."""
        return os.path.dirname(self._settings_path)

    # ========== Property-аксессоры для настроек ==========

    @property
    def sync_folder(self) -> str:
        """Путь к папке синхронизации."""
        return self._data.get('sync_folder', self.DEFAULTS['sync_folder'])

    @sync_folder.setter
    def sync_folder(self, value: str) -> None:
        self._data['sync_folder'] = value

    @property
    def downloads_folder(self) -> str:
        """Путь к папке загрузок."""
        return self._data.get('downloads_folder', self.DEFAULTS['downloads_folder'])

    @downloads_folder.setter
    def downloads_folder(self, value: str) -> None:
        self._data['downloads_folder'] = value

    @property
    def auto_start(self) -> bool:
        """Автозапуск при загрузке системы."""
        return self._data.get('auto_start', self.DEFAULTS['auto_start'])

    @auto_start.setter
    def auto_start(self, value: bool) -> None:
        self._data['auto_start'] = bool(value)

    @property
    def theme(self) -> str:
        """Тема оформления ('system', 'dark', 'light')."""
        return self._data.get('theme', self.DEFAULTS['theme'])

    @theme.setter
    def theme(self, value: str) -> None:
        if value not in ('system', 'dark', 'light'):
            logger.warning("Некорректная тема '%s', используется 'system'", value)
            value = 'system'
        self._data['theme'] = value

    @property
    def sync_interval(self) -> int:
        """Интервал синхронизации в секундах."""
        return self._data.get('sync_interval', self.DEFAULTS['sync_interval'])

    @sync_interval.setter
    def sync_interval(self, value: int) -> None:
        self._data['sync_interval'] = max(10, int(value))

    @property
    def proxy_mode(self) -> int:
        """Режим прокси (0=нет, 1=системный, 2=ручной)."""
        return self._data.get('proxy_mode', self.DEFAULTS['proxy_mode'])

    @proxy_mode.setter
    def proxy_mode(self, value: int) -> None:
        if value not in (0, 1, 2):
            value = 0
        self._data['proxy_mode'] = value

    @property
    def proxy_server(self) -> str:
        """Адрес прокси-сервера."""
        return self._data.get('proxy_server', self.DEFAULTS['proxy_server'])

    @proxy_server.setter
    def proxy_server(self, value: str) -> None:
        self._data['proxy_server'] = value

    @property
    def proxy_port(self) -> str:
        """Порт прокси-сервера."""
        return self._data.get('proxy_port', self.DEFAULTS['proxy_port'])

    @proxy_port.setter
    def proxy_port(self, value: str) -> None:
        self._data['proxy_port'] = value

    @property
    def proxy_auth(self) -> str:
        """Авторизация прокси (login:password)."""
        return self._data.get('proxy_auth', self.DEFAULTS['proxy_auth'])

    @proxy_auth.setter
    def proxy_auth(self, value: str) -> None:
        self._data['proxy_auth'] = value

    @property
    def notifications(self) -> Dict[str, bool]:
        """Настройки уведомлений."""
        return self._data.get('notifications', self.DEFAULTS['notifications'].copy())

    @notifications.setter
    def notifications(self, value: Dict[str, bool]) -> None:
        self._data['notifications'] = value

    @property
    def hotkeys_enabled(self) -> bool:
        """Включены ли горячие клавиши."""
        return self._data.get('hotkeys_enabled', self.DEFAULTS['hotkeys_enabled'])

    @hotkeys_enabled.setter
    def hotkeys_enabled(self, value: bool) -> None:
        self._data['hotkeys_enabled'] = bool(value)

    @property
    def hotkeys(self) -> Dict[str, str]:
        """Назначения горячих клавиш."""
        return self._data.get('hotkeys', self.DEFAULTS['hotkeys'].copy())

    @hotkeys.setter
    def hotkeys(self, value: Dict[str, str]) -> None:
        self._data['hotkeys'] = value

    @property
    def last_user_name(self) -> str:
        """Имя последнего авторизованного пользователя."""
        return self._data.get('last_user_name', '')

    @last_user_name.setter
    def last_user_name(self, value: str) -> None:
        self._data['last_user_name'] = value

    @property
    def last_user_email(self) -> str:
        """Email последнего авторизованного пользователя."""
        return self._data.get('last_user_email', '')

    @last_user_email.setter
    def last_user_email(self, value: str) -> None:
        self._data['last_user_email'] = value

    @property
    def window_geometry(self) -> bytes:
        """Геометрия главного окна (сериализованные байты Qt)."""
        return self._data.get('window_geometry', b'')

    @window_geometry.setter
    def window_geometry(self, value: bytes) -> None:
        self._data['window_geometry'] = value

    @property
    def view_mode(self) -> str:
        """Режим отображения ('list' | 'tiles_normal' | 'tiles_large' | 'tiles_huge')."""
        val = self._data.get('view_mode', 'tiles_large')
        if val == 'tiles':
            val = 'tiles_large'
        return val

    @view_mode.setter
    def view_mode(self, value: str) -> None:
        if value not in ('list', 'tiles_normal', 'tiles_large', 'tiles_huge', 'tiles'):
            value = 'tiles_large'
        if value == 'tiles':
            value = 'tiles_large'
        self._data['view_mode'] = value

    @property
    def sort_by(self) -> str:
        """Сортировка по полю ('name' | 'date' | 'size')."""
        return self._data.get('sort_by', self.DEFAULTS['sort_by'])

    @sort_by.setter
    def sort_by(self, value: str) -> None:
        if value not in ('name', 'date', 'size'):
            value = 'name'
        self._data['sort_by'] = value

    @property
    def sort_order(self) -> str:
        """Порядок сортировки ('asc' | 'desc')."""
        return self._data.get('sort_order', self.DEFAULTS['sort_order'])

    @sort_order.setter
    def sort_order(self, value: str) -> None:
        if value not in ('asc', 'desc'):
            value = 'asc'
        self._data['sort_order'] = value

    @property
    def selective_sync_folders(self) -> List[str]:
        """Список папок для избирательной синхронизации (пустой = все)."""
        return self._data.get('selective_sync_folders', [])

    @selective_sync_folders.setter
    def selective_sync_folders(self, value: List[str]) -> None:
        self._data['selective_sync_folders'] = list(value)

    # ========== Универсальные get/set ==========

    def get(self, key: str, default: Any = None) -> Any:
        """
        Получить значение настройки по ключу.

        Args:
            key: Ключ настройки.
            default: Значение по умолчанию, если ключ не найден.

        Returns:
            Значение настройки.
        """
        return self._data.get(key, default if default is not None else self.DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        """
        Установить значение настройки.

        Args:
            key: Ключ настройки.
            value: Новое значение.
        """
        self._data[key] = value

    # ========== Загрузка / сохранение XML ==========

    def load(self) -> None:
        """
        Загружает настройки из XML-файла.

        Если файл не существует, создаёт директорию и файл
        со значениями по умолчанию.
        """
        if not os.path.exists(self._settings_path):
            logger.info(
                "Файл настроек не найден: %s. Создаю с настройками по умолчанию.",
                self._settings_path,
            )
            self._ensure_directory()
            self.save()
            return

        try:
            tree = ET.parse(self._settings_path)
            root = tree.getroot()
            self._parse_xml(root)
            logger.info("Настройки загружены из %s", self._settings_path)
        except ET.ParseError as e:
            logger.error("Ошибка парсинга XML настроек: %s. Используем значения по умолчанию.", e)
            self._init_defaults()
        except Exception as e:
            logger.error("Ошибка загрузки настроек: %s", e)
            self._init_defaults()

    def save(self) -> None:
        """Сохраняет текущие настройки в XML-файл."""
        self._ensure_directory()

        root = ET.Element('GoogleDriveSyncSettings')
        root.set('version', '1.0')

        self._write_xml(root)

        tree = ET.ElementTree(root)
        ET.indent(tree, space='  ')

        try:
            tree.write(self._settings_path, encoding='utf-8', xml_declaration=True)
            logger.info("Настройки сохранены в %s", self._settings_path)
        except Exception as e:
            logger.error("Ошибка сохранения настроек: %s", e)

    def _ensure_directory(self) -> None:
        """Создаёт директорию для файла настроек, если она не существует."""
        dir_path = os.path.dirname(self._settings_path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
            logger.info("Создана директория: %s", dir_path)

    def _write_xml(self, root: ET.Element) -> None:
        """
        Записывает все настройки в XML-дерево.

        Args:
            root: Корневой XML-элемент.
        """
        # Простые строковые настройки
        for key in ('sync_folder', 'downloads_folder', 'theme', 'proxy_server',
                     'proxy_port', 'proxy_auth', 'last_user_name', 'last_user_email',
                     'view_mode', 'sort_by', 'sort_order'):
            elem = ET.SubElement(root, key)
            elem.text = str(self._data.get(key, self.DEFAULTS.get(key, '')))

        # Булевы настройки
        for key in ('auto_start', 'hotkeys_enabled'):
            elem = ET.SubElement(root, key)
            elem.text = str(self._data.get(key, self.DEFAULTS.get(key, True))).lower()

        # Числовые настройки
        for key in ('sync_interval', 'proxy_mode'):
            elem = ET.SubElement(root, key)
            elem.text = str(self._data.get(key, self.DEFAULTS.get(key, 0)))

        # Словарь уведомлений
        notif_elem = ET.SubElement(root, 'notifications')
        notifications = self._data.get('notifications', self.DEFAULTS['notifications'])
        for k, v in notifications.items():
            sub = ET.SubElement(notif_elem, k)
            sub.text = str(v).lower()

        # Словарь горячих клавиш
        hotkeys_elem = ET.SubElement(root, 'hotkeys')
        hotkeys = self._data.get('hotkeys', self.DEFAULTS['hotkeys'])
        for k, v in hotkeys.items():
            sub = ET.SubElement(hotkeys_elem, k)
            sub.text = str(v)

        # Геометрия окна (base64)
        geom_elem = ET.SubElement(root, 'window_geometry')
        geom = self._data.get('window_geometry', b'')
        if geom:
            geom_elem.text = base64.b64encode(geom).decode('ascii')
        else:
            geom_elem.text = ''

        # Список папок избирательной синхронизации
        sel_elem = ET.SubElement(root, 'selective_sync_folders')
        folders = self._data.get('selective_sync_folders', [])
        for folder in folders:
            f_elem = ET.SubElement(sel_elem, 'folder')
            f_elem.text = folder

    def _parse_xml(self, root: ET.Element) -> None:
        """
        Парсит XML-дерево и заполняет внутренний словарь настроек.

        Args:
            root: Корневой XML-элемент.
        """
        # Простые строковые настройки
        for key in ('sync_folder', 'downloads_folder', 'theme', 'proxy_server',
                     'proxy_port', 'proxy_auth', 'last_user_name', 'last_user_email',
                     'view_mode', 'sort_by', 'sort_order'):
            elem = root.find(key)
            if elem is not None and elem.text is not None:
                self._data[key] = elem.text

        # Булевы настройки
        for key in ('auto_start', 'hotkeys_enabled'):
            elem = root.find(key)
            if elem is not None and elem.text is not None:
                self._data[key] = elem.text.lower() in ('true', '1', 'yes')

        # Числовые настройки
        elem = root.find('sync_interval')
        if elem is not None and elem.text is not None:
            try:
                self._data['sync_interval'] = max(10, int(elem.text))
            except ValueError:
                pass

        elem = root.find('proxy_mode')
        if elem is not None and elem.text is not None:
            try:
                val = int(elem.text)
                if val in (0, 1, 2):
                    self._data['proxy_mode'] = val
            except ValueError:
                pass

        # Словарь уведомлений
        notif_elem = root.find('notifications')
        if notif_elem is not None:
            notifications = {}
            for child in notif_elem:
                notifications[child.tag] = child.text.lower() in ('true', '1', 'yes') if child.text else False
            if notifications:
                self._data['notifications'] = notifications

        # Словарь горячих клавиш
        hotkeys_elem = root.find('hotkeys')
        if hotkeys_elem is not None:
            hotkeys = {}
            for child in hotkeys_elem:
                hotkeys[child.tag] = child.text or ''
            if hotkeys:
                self._data['hotkeys'] = hotkeys

        # Геометрия окна
        geom_elem = root.find('window_geometry')
        if geom_elem is not None and geom_elem.text:
            try:
                self._data['window_geometry'] = base64.b64decode(geom_elem.text)
            except Exception:
                self._data['window_geometry'] = b''

        # Список папок избирательной синхронизации
        sel_elem = root.find('selective_sync_folders')
        if sel_elem is not None:
            folders = []
            for f_elem in sel_elem.findall('folder'):
                if f_elem.text:
                    folders.append(f_elem.text)
            self._data['selective_sync_folders'] = folders

    def __repr__(self) -> str:
        return f"Config(path='{self._settings_path}')"
