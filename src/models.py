# -*- coding: utf-8 -*-
"""
Модели данных для Google Drive Sync.

Содержит dataclass-модели и перечисления, описывающие
файлы, операции синхронизации и информацию о пользователе.
"""

from enum import Enum, auto
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


class SyncStatus(Enum):
    """Статус синхронизации файла."""
    SYNCED = auto()
    UPLOADING = auto()
    DOWNLOADING = auto()
    ERROR = auto()
    PENDING = auto()
    CONFLICT = auto()
    CLOUD_ONLY = auto()
    LOCAL_ONLY = auto()


class OpType(Enum):
    """Тип операции синхронизации."""
    UPLOAD = auto()
    DOWNLOAD = auto()
    DELETE_LOCAL = auto()
    DELETE_CLOUD = auto()
    RENAME = auto()
    MOVE = auto()


@dataclass
class FileItem:
    """
    Элемент файловой системы (файл или папка).

    Attributes:
        path: Относительный путь от корневой папки синхронизации.
        name: Имя файла или папки.
        size: Размер файла в байтах.
        modified: Дата последнего изменения.
        is_dir: True, если это директория.
        cloud_id: Идентификатор файла в Google Drive.
        local_hash: MD5-хеш локальной копии файла.
        cloud_hash: MD5-хеш облачной копии файла.
        sync_status: Текущий статус синхронизации.
        mime_type: MIME-тип файла.
    """
    path: str
    name: str
    size: int = 0
    modified: Optional[datetime] = None
    is_dir: bool = False
    cloud_id: Optional[str] = None
    local_hash: Optional[str] = None
    cloud_hash: Optional[str] = None
    sync_status: SyncStatus = SyncStatus.PENDING
    mime_type: Optional[str] = None


@dataclass
class SyncOperation:
    """
    Операция синхронизации.

    Attributes:
        op_type: Тип операции (загрузка, скачивание и т.д.).
        file_path: Путь к файлу, над которым выполняется операция.
        progress: Прогресс выполнения (0-100%).
        error: Сообщение об ошибке, если операция завершилась неудачей.
        created_at: Дата и время создания операции.
    """
    op_type: OpType
    file_path: str
    progress: float = 0.0
    error: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class UserInfo:
    """
    Информация о пользователе Google.

    Attributes:
        name: Имя пользователя.
        email: Email пользователя.
        avatar_url: URL аватара пользователя.
        avatar_data: Бинарные данные аватара (для кеширования).
        storage_used: Использовано места в хранилище (байт).
        storage_total: Общий объём хранилища (байт).
    """
    name: str = ''
    email: str = ''
    avatar_url: Optional[str] = None
    avatar_data: Optional[bytes] = None
    storage_used: int = 0
    storage_total: int = 0

    @property
    def storage_used_gb(self) -> float:
        """Использовано места в ГБ."""
        return self.storage_used / (1024 ** 3)

    @property
    def storage_total_gb(self) -> float:
        """Общий объём хранилища в ГБ."""
        return self.storage_total / (1024 ** 3)

    @property
    def storage_percent(self) -> float:
        """Процент использования хранилища."""
        if self.storage_total == 0:
            return 0
        return (self.storage_used / self.storage_total) * 100
