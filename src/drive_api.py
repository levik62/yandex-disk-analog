# -*- coding: utf-8 -*-
"""
Обёртка над Google Drive API v3 для Google Drive Sync.

Предоставляет методы для загрузки, скачивания, удаления файлов,
управления папками и отслеживания изменений в Google Drive.
Является QObject с сигналами Qt для уведомления о прогрессе.
"""

import io
import os
import logging
import mimetypes
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QObject, pyqtSignal

from googleapiclient.discovery import build, Resource
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from googleapiclient.errors import HttpError

from auth_manager import AuthManager

logger = logging.getLogger(__name__)

# Имя корневой папки синхронизации в Google Drive
SYNC_ROOT_FOLDER_NAME = 'GoogleDriveSync'

# Размер чанка для загрузки/скачивания (5 МБ)
CHUNK_SIZE = 5 * 1024 * 1024

# Порог для resumable upload (5 МБ)
RESUMABLE_THRESHOLD = 5 * 1024 * 1024

# Google Drive MIME-тип для папок
FOLDER_MIME_TYPE = 'application/vnd.google-apps.folder'


class DriveAPI(QObject):
    """
    Обёртка Google Drive API v3.

    Наследуется от QObject для использования сигналов Qt.
    Обеспечивает полный набор операций с файлами и папками
    в Google Drive, включая resumable upload, отслеживание
    изменений и управление правами доступа.

    Signals:
        upload_progress: Прогресс загрузки файла (путь, процент).
        download_progress: Прогресс скачивания файла (путь, процент).
        api_error: Ошибка API (текст ошибки).
        file_uploaded: Файл загружен (локальный путь, cloud file_id).
        file_downloaded: Файл скачан (локальный путь).
    """

    upload_progress = pyqtSignal(str, float)      # (file_path, percent)
    download_progress = pyqtSignal(str, float)     # (file_path, percent)
    api_error = pyqtSignal(str)                    # error message
    file_uploaded = pyqtSignal(str, str)           # (local_path, file_id)
    file_downloaded = pyqtSignal(str)              # (local_path)

    def __init__(
        self,
        auth_manager: AuthManager,
        parent: Optional[QObject] = None,
    ) -> None:
        """
        Инициализация обёртки Google Drive API.

        Args:
            auth_manager: Экземпляр AuthManager для получения credentials.
            parent: Родительский QObject (опционально).
        """
        super().__init__(parent)
        self._auth_manager = auth_manager
        self._service: Optional[Resource] = None
        self._sync_root_id: Optional[str] = None

    def _get_service(self) -> Resource:
        """
        Получить или создать экземпляр Google Drive API service.

        Returns:
            Объект Resource для работы с Google Drive API.

        Raises:
            RuntimeError: Если пользователь не авторизован.
        """
        creds = self._auth_manager.get_credentials()
        if creds is None:
            error_msg = "Не удалось получить credentials. Пользователь не авторизован."
            logger.error(error_msg)
            self.api_error.emit(error_msg)
            raise RuntimeError(error_msg)

        # Пересоздаём сервис при каждом вызове, чтобы credentials
        # всегда были актуальными после refresh
        self._service = build('drive', 'v3', credentials=creds)
        return self._service

    def ensure_sync_folder(self) -> str:
        """
        Создаёт корневую папку синхронизации в Google Drive (если не существует).

        Ищет папку с именем SYNC_ROOT_FOLDER_NAME в корне Drive.
        Если не найдена — создаёт.

        Returns:
            ID папки синхронизации в Google Drive.

        Raises:
            RuntimeError: Если не удалось создать/найти папку.
        """
        if self._sync_root_id:
            return self._sync_root_id

        try:
            service = self._get_service()

            # Ищем существующую папку
            query = (
                f"name = '{SYNC_ROOT_FOLDER_NAME}' "
                f"and mimeType = '{FOLDER_MIME_TYPE}' "
                f"and 'root' in parents "
                f"and trashed = false"
            )
            results = service.files().list(
                q=query,
                spaces='drive',
                fields='files(id, name)',
                pageSize=1,
            ).execute()

            files = results.get('files', [])

            if files:
                self._sync_root_id = files[0]['id']
                logger.info(
                    "Найдена корневая папка синхронизации: %s (id: %s)",
                    SYNC_ROOT_FOLDER_NAME, self._sync_root_id,
                )
                return self._sync_root_id

            # Создаём новую папку
            file_metadata = {
                'name': SYNC_ROOT_FOLDER_NAME,
                'mimeType': FOLDER_MIME_TYPE,
            }
            folder = service.files().create(
                body=file_metadata,
                fields='id',
            ).execute()

            self._sync_root_id = folder['id']
            logger.info(
                "Создана корневая папка синхронизации: %s (id: %s)",
                SYNC_ROOT_FOLDER_NAME, self._sync_root_id,
            )
            return self._sync_root_id

        except HttpError as e:
            error_msg = f"Ошибка Google Drive API при создании корневой папки: {e}"
            logger.error(error_msg)

            # Проверяем на недостаточные права доступа (403 Forbidden)
            if e.resp.status == 403 or "insufficient" in str(e).lower():
                logger.warning("Обнаружена ошибка 403 (недостаточно прав доступа). Сбрасываем авторизацию...")
                try:
                    self._auth_manager.logout()
                except Exception as auth_err:
                    logger.error("Не удалось удалить недействительный токен: %s", auth_err)
                error_msg = "Ошибка прав доступа (403). Токен сброшен. Пожалуйста, выйдите из аккаунта и войдите заново (или перезапустите приложение), чтобы предоставить полные права на работу с Google Drive."

            self.api_error.emit(error_msg)
            raise RuntimeError(error_msg) from e

    def upload_file(
        self,
        local_path: str,
        parent_id: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> str:
        """
        Загрузить файл в Google Drive.

        Для файлов больше RESUMABLE_THRESHOLD используется resumable upload.

        Args:
            local_path: Абсолютный путь к локальному файлу.
            parent_id: ID родительской папки в Drive. Если None — корень синхронизации.
            mime_type: MIME-тип файла. Если None — определяется автоматически.

        Returns:
            ID загруженного файла в Google Drive.

        Raises:
            FileNotFoundError: Если локальный файл не найден.
            RuntimeError: При ошибке API.
        """
        if not os.path.exists(local_path):
            raise FileNotFoundError(f"Файл не найден: {local_path}")

        if parent_id is None:
            parent_id = self.ensure_sync_folder()

        if mime_type is None:
            mime_type, _ = mimetypes.guess_type(local_path)
            if mime_type is None:
                mime_type = 'application/octet-stream'

        file_name = os.path.basename(local_path)
        file_size = os.path.getsize(local_path)

        file_metadata: Dict = {
            'name': file_name,
            'parents': [parent_id],
        }

        try:
            service = self._get_service()

            # Определяем, нужен ли resumable upload
            resumable = file_size > RESUMABLE_THRESHOLD

            media = MediaFileUpload(
                local_path,
                mimetype=mime_type,
                resumable=resumable,
                chunksize=CHUNK_SIZE if resumable else -1,
            )

            request = service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id',
            )

            if resumable:
                # Загрузка по частям с отслеживанием прогресса
                response = None
                while response is None:
                    status, response = request.next_chunk()
                    if status:
                        progress = status.progress() * 100
                        self.upload_progress.emit(local_path, progress)
                        logger.debug(
                            "Загрузка %s: %.1f%%", file_name, progress,
                        )

                file_id = response['id']
            else:
                # Простая загрузка для маленьких файлов
                result = request.execute()
                file_id = result['id']

            self.upload_progress.emit(local_path, 100.0)
            self.file_uploaded.emit(local_path, file_id)
            logger.info("Файл загружен: %s -> %s", file_name, file_id)
            return file_id

        except HttpError as e:
            error_msg = f"Ошибка загрузки файла '{file_name}': {e}"
            logger.error(error_msg)
            self.api_error.emit(error_msg)
            raise RuntimeError(error_msg) from e

    def download_file(self, file_id: str, local_path: str) -> bool:
        """
        Скачать файл из Google Drive.

        Args:
            file_id: ID файла в Google Drive.
            local_path: Абсолютный путь для сохранения файла.

        Returns:
            True при успешном скачивании, False при ошибке.
        """
        try:
            service = self._get_service()

            # Создаём директорию, если не существует
            dir_path = os.path.dirname(local_path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)

            request = service.files().get_media(fileId=file_id)
            file_handle = io.FileIO(local_path, 'wb')
            downloader = MediaIoBaseDownload(file_handle, request, chunksize=CHUNK_SIZE)

            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status:
                    progress = status.progress() * 100
                    self.download_progress.emit(local_path, progress)
                    logger.debug(
                        "Скачивание %s: %.1f%%",
                        os.path.basename(local_path), progress,
                    )

            file_handle.close()
            self.download_progress.emit(local_path, 100.0)
            self.file_downloaded.emit(local_path)
            logger.info("Файл скачан: %s -> %s", file_id, local_path)
            return True

        except HttpError as e:
            error_msg = f"Ошибка скачивания файла (id: {file_id}): {e}"
            logger.error(error_msg)
            self.api_error.emit(error_msg)
            return False
        except Exception as e:
            error_msg = f"Ошибка при сохранении файла '{local_path}': {e}"
            logger.error(error_msg)
            self.api_error.emit(error_msg)
            return False

    def delete_file(self, file_id: str) -> bool:
        """
        Удалить файл из Google Drive (перемещение в корзину).

        Args:
            file_id: ID файла в Google Drive.

        Returns:
            True при успешном удалении, False при ошибке.
        """
        try:
            service = self._get_service()
            service.files().update(
                fileId=file_id,
                body={'trashed': True},
            ).execute()
            logger.info("Файл перемещён в корзину: %s", file_id)
            return True
        except HttpError as e:
            error_msg = f"Ошибка удаления файла (id: {file_id}): {e}"
            logger.error(error_msg)
            self.api_error.emit(error_msg)
            return False

    def list_folder(self, folder_id: Optional[str] = None) -> List[Dict]:
        """
        Получить список файлов в папке Google Drive.

        Args:
            folder_id: ID папки. Если None — корневая папка синхронизации.

        Returns:
            Список словарей с метаданными файлов.
            Каждый словарь содержит: id, name, mimeType, size,
            modifiedTime, md5Checksum.
        """
        if folder_id is None:
            folder_id = self.ensure_sync_folder()

        try:
            service = self._get_service()
            all_files: List[Dict] = []
            page_token: Optional[str] = None

            while True:
                query = (
                    f"'{folder_id}' in parents "
                    f"and trashed = false"
                )
                results = service.files().list(
                    q=query,
                    spaces='drive',
                    fields=(
                        'nextPageToken, '
                        'files(id, name, mimeType, size, modifiedTime, md5Checksum)'
                    ),
                    pageSize=100,
                    pageToken=page_token,
                    orderBy='folder, name',
                ).execute()

                files = results.get('files', [])
                all_files.extend(files)

                page_token = results.get('nextPageToken')
                if page_token is None:
                    break

            logger.debug(
                "Получен список файлов в папке %s: %d элементов",
                folder_id, len(all_files),
            )
            return all_files

        except HttpError as e:
            error_msg = f"Ошибка получения списка файлов (папка: {folder_id}): {e}"
            logger.error(error_msg)
            self.api_error.emit(error_msg)
            return []

    def create_folder(
        self,
        name: str,
        parent_id: Optional[str] = None,
    ) -> str:
        """
        Создать папку в Google Drive.

        Args:
            name: Имя папки.
            parent_id: ID родительской папки. Если None — корень синхронизации.

        Returns:
            ID созданной папки.

        Raises:
            RuntimeError: При ошибке API.
        """
        if parent_id is None:
            parent_id = self.ensure_sync_folder()

        file_metadata: Dict = {
            'name': name,
            'mimeType': FOLDER_MIME_TYPE,
            'parents': [parent_id],
        }

        try:
            service = self._get_service()
            folder = service.files().create(
                body=file_metadata,
                fields='id',
            ).execute()

            folder_id = folder['id']
            logger.info("Создана папка '%s' (id: %s)", name, folder_id)
            return folder_id

        except HttpError as e:
            error_msg = f"Ошибка создания папки '{name}': {e}"
            logger.error(error_msg)
            self.api_error.emit(error_msg)
            raise RuntimeError(error_msg) from e

    def get_changes(
        self,
        start_token: Optional[str] = None,
    ) -> Tuple[List[Dict], str]:
        """
        Получить список изменений в Google Drive.

        Использует Changes API для получения инкрементальных
        изменений с момента последнего запроса.

        Args:
            start_token: Токен начала изменений. Если None — получает новый.

        Returns:
            Кортеж (список_изменений, новый_токен).
            Каждое изменение содержит: fileId, removed, file (метаданные).
        """
        if start_token is None:
            start_token = self.get_start_page_token()

        try:
            service = self._get_service()
            all_changes: List[Dict] = []
            page_token = start_token
            new_start_token = start_token

            while page_token is not None:
                results = service.changes().list(
                    pageToken=page_token,
                    spaces='drive',
                    fields=(
                        'nextPageToken, newStartPageToken, '
                        'changes(fileId, removed, file(id, name, mimeType, '
                        'size, modifiedTime, md5Checksum, parents, trashed))'
                    ),
                    pageSize=100,
                    includeRemoved=True,
                ).execute()

                changes = results.get('changes', [])
                all_changes.extend(changes)

                if 'newStartPageToken' in results:
                    new_start_token = results['newStartPageToken']

                page_token = results.get('nextPageToken')

            logger.info("Получено %d изменений", len(all_changes))
            return (all_changes, new_start_token)

        except HttpError as e:
            error_msg = f"Ошибка получения изменений: {e}"
            logger.error(error_msg)
            self.api_error.emit(error_msg)
            return ([], start_token)

    def get_start_page_token(self) -> str:
        """
        Получить начальный токен для отслеживания изменений.

        Returns:
            Строка startPageToken.

        Raises:
            RuntimeError: При ошибке API.
        """
        try:
            service = self._get_service()
            response = service.changes().getStartPageToken().execute()
            token = response.get('startPageToken', '')
            logger.debug("Получен startPageToken: %s", token)
            return token
        except HttpError as e:
            error_msg = f"Ошибка получения startPageToken: {e}"
            logger.error(error_msg)
            self.api_error.emit(error_msg)
            raise RuntimeError(error_msg) from e

    def create_share_link(self, file_id: str) -> str:
        """
        Создать публичную ссылку на файл.

        Устанавливает права доступа 'anyone with link can read'
        и возвращает webViewLink.

        Args:
            file_id: ID файла в Google Drive.

        Returns:
            Публичная ссылка на файл.

        Raises:
            RuntimeError: При ошибке API.
        """
        try:
            service = self._get_service()

            # Создаём permission для доступа по ссылке
            permission = {
                'type': 'anyone',
                'role': 'reader',
            }
            service.permissions().create(
                fileId=file_id,
                body=permission,
                fields='id',
            ).execute()

            # Получаем webViewLink
            file_data = service.files().get(
                fileId=file_id,
                fields='webViewLink',
            ).execute()

            link = file_data.get('webViewLink', '')
            logger.info("Создана публичная ссылка для %s: %s", file_id, link)
            return link

        except HttpError as e:
            error_msg = f"Ошибка создания ссылки для файла (id: {file_id}): {e}"
            logger.error(error_msg)
            self.api_error.emit(error_msg)
            raise RuntimeError(error_msg) from e

    def get_file_metadata(self, file_id: str) -> Dict:
        """
        Получить метаданные файла из Google Drive.

        Args:
            file_id: ID файла в Google Drive.

        Returns:
            Словарь с метаданными файла (id, name, mimeType, size,
            modifiedTime, md5Checksum, parents, webViewLink).
        """
        try:
            service = self._get_service()
            file_data = service.files().get(
                fileId=file_id,
                fields=(
                    'id, name, mimeType, size, modifiedTime, '
                    'md5Checksum, parents, webViewLink, trashed'
                ),
            ).execute()
            logger.debug("Получены метаданные файла: %s", file_data.get('name', file_id))
            return file_data
        except HttpError as e:
            error_msg = f"Ошибка получения метаданных файла (id: {file_id}): {e}"
            logger.error(error_msg)
            self.api_error.emit(error_msg)
            return {}

    def get_file_by_path(self, path: str) -> Optional[Dict]:
        """
        Найти файл в Google Drive по относительному пути.

        Проходит по дереву папок от корня синхронизации
        и находит файл по заданному пути.

        Args:
            path: Относительный путь файла (например, 'documents/report.pdf').

        Returns:
            Словарь с метаданными файла или None, если не найден.
        """
        file_id = self._resolve_path(path)
        if file_id is None:
            return None
        return self.get_file_metadata(file_id)

    def _resolve_path(self, path: str) -> Optional[str]:
        """
        Пройти по дереву папок и получить ID файла по пути.

        Args:
            path: Относительный путь (разделитель — '/' или '\\').

        Returns:
            ID файла/папки в Google Drive или None, если не найден.
        """
        # Нормализуем путь
        path = path.replace('\\', '/').strip('/')
        if not path:
            return self.ensure_sync_folder()

        parts = path.split('/')
        current_id = self.ensure_sync_folder()

        try:
            service = self._get_service()

            for i, part in enumerate(parts):
                is_last = (i == len(parts) - 1)

                query = (
                    f"name = '{part}' "
                    f"and '{current_id}' in parents "
                    f"and trashed = false"
                )

                # Если не последний элемент пути — ищем папку
                if not is_last:
                    query += f" and mimeType = '{FOLDER_MIME_TYPE}'"

                results = service.files().list(
                    q=query,
                    spaces='drive',
                    fields='files(id, name, mimeType)',
                    pageSize=1,
                ).execute()

                files = results.get('files', [])
                if not files:
                    logger.debug("Путь не найден: '%s' (не найден сегмент '%s')", path, part)
                    return None

                current_id = files[0]['id']

            return current_id

        except HttpError as e:
            error_msg = f"Ошибка при разрешении пути '{path}': {e}"
            logger.error(error_msg)
            self.api_error.emit(error_msg)
            return None

    def __repr__(self) -> str:
        auth_status = "авторизован" if self._auth_manager.is_authenticated else "не авторизован"
        return f"DriveAPI(статус={auth_status})"
