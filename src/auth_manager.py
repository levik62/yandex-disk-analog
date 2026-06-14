# -*- coding: utf-8 -*-
"""
Менеджер авторизации Google OAuth 2.0 для Google Drive Sync.

Управляет OAuth flow, токенами, информацией о пользователе
и хранилище Google Drive. Является QObject с сигналами Qt.
"""

import os
# Разрешить oauthlib принимать измененный набор scopes (например, добавление openid)
os.environ['OAUTHLIB_RELAX_TOKEN_SCOPE'] = '1'
import json
import logging
import requests
from typing import Optional, Tuple

from PyQt6.QtCore import QObject, pyqtSignal

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request as GoogleAuthRequest
from googleapiclient.discovery import build

from models import UserInfo
from config import Config

logger = logging.getLogger(__name__)

# Области доступа OAuth 2.0
SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/userinfo.profile',
    'https://www.googleapis.com/auth/userinfo.email',
]


class AuthManager(QObject):
    """
    Менеджер авторизации Google OAuth 2.0.

    Наследуется от QObject для использования сигналов Qt.
    Управляет полным циклом авторизации: OAuth flow, обновление
    токена, получение информации о пользователе и хранилище.

    Signals:
        authenticated: Испускается при успешной авторизации. Передаёт UserInfo.
        auth_error: Испускается при ошибке авторизации. Передаёт текст ошибки.
        token_refreshed: Испускается при успешном обновлении токена.
    """

    authenticated = pyqtSignal(object)   # UserInfo
    auth_error = pyqtSignal(str)
    token_refreshed = pyqtSignal()

    def __init__(self, config: Config, parent: Optional[QObject] = None) -> None:
        """
        Инициализация менеджера авторизации.

        Args:
            config: Экземпляр Config с настройками приложения.
            parent: Родительский QObject (опционально).
        """
        super().__init__(parent)
        self._config = config
        self._credentials: Optional[Credentials] = None

        # Пути к файлам авторизации
        self._app_data_dir = config.app_data_dir
        self._credentials_path = os.path.join(self._app_data_dir, 'credentials.json')
        self._token_path = os.path.join(self._app_data_dir, 'token.json')

        # Попытка загрузить существующий токен
        self._load_token()

    @property
    def credentials_path(self) -> str:
        """Путь к файлу credentials.json."""
        return self._credentials_path

    @property
    def token_path(self) -> str:
        """Путь к файлу token.json."""
        return self._token_path

    @property
    def is_authenticated(self) -> bool:
        """
        Проверяет, авторизован ли пользователь.

        Returns:
            True, если есть валидные credentials.
        """
        return self._credentials is not None and self._credentials.valid

    def _load_token(self) -> None:
        """
        Загружает токен из файла token.json.

        Если токен существует и истёк, пытается обновить его.
        Если token.json не существует, ничего не делает.
        """
        if not os.path.exists(self._token_path):
            logger.info("Файл token.json не найден. Требуется авторизация.")
            return

        try:
            self._credentials = Credentials.from_authorized_user_file(
                self._token_path, SCOPES
            )
            logger.info("Токен загружен из %s", self._token_path)

            if self._credentials and self._credentials.expired and self._credentials.refresh_token:
                logger.info("Токен истёк, пытаемся обновить...")
                self.refresh_token()

        except Exception as e:
            logger.error("Ошибка загрузки токена: %s", e)
            self._credentials = None

    def _save_token(self) -> None:
        """Сохраняет текущие credentials в файл token.json."""
        if self._credentials is None:
            return

        try:
            os.makedirs(self._app_data_dir, exist_ok=True)
            with open(self._token_path, 'w', encoding='utf-8') as f:
                f.write(self._credentials.to_json())
            logger.info("Токен сохранён в %s", self._token_path)
        except Exception as e:
            logger.error("Ошибка сохранения токена: %s", e)

    def _check_credentials_file(self) -> bool:
        """
        Проверяет наличие файла credentials.json.

        Если файл не найден, логирует инструкцию по его получению.

        Returns:
            True, если файл найден.
        """
        if os.path.exists(self._credentials_path):
            return True

        instruction = (
            "Файл credentials.json не найден!\n\n"
            "Для настройки авторизации Google Drive:\n"
            "1. Перейдите на https://console.cloud.google.com/\n"
            "2. Создайте проект или выберите существующий\n"
            "3. Включите Google Drive API (APIs & Services → Library)\n"
            "4. Создайте OAuth 2.0 Client ID:\n"
            "   - APIs & Services → Credentials → Create Credentials\n"
            "   - Тип: Desktop Application\n"
            "5. Скачайте JSON-файл и сохраните как:\n"
            f"   {self._credentials_path}\n"
            "6. Перезапустите приложение"
        )

        logger.error(instruction)
        self.auth_error.emit(
            f"Файл credentials.json не найден.\n"
            f"Поместите его в:\n{self._credentials_path}\n\n"
            f"Подробная инструкция выведена в лог."
        )
        return False

    def authenticate(self) -> bool:
        """
        Запускает полный цикл авторизации OAuth 2.0.

        Открывает браузер для авторизации пользователя,
        получает и сохраняет токен доступа.

        Returns:
            True при успешной авторизации, False при ошибке.
        """
        if not self._check_credentials_file():
            return False

        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                self._credentials_path, SCOPES
            )
            # Запуск локального сервера для OAuth callback
            self._credentials = flow.run_local_server(
                port=0,
                prompt='consent',
                success_message=(
                    'Авторизация прошла успешно! '
                    'Вы можете закрыть эту вкладку.'
                ),
            )
            self._save_token()

            # Получаем информацию о пользователе
            user_info = self.get_user_info()
            if user_info:
                self._config.last_user_name = user_info.name
                self._config.last_user_email = user_info.email
                self._config.save()

            logger.info("Авторизация успешна для %s", user_info.email if user_info else 'unknown')
            self.authenticated.emit(user_info)
            return True

        except Exception as e:
            error_msg = f"Ошибка авторизации: {e}"
            logger.error(error_msg)
            self.auth_error.emit(error_msg)
            return False

    def refresh_token(self) -> bool:
        """
        Обновляет истекший токен доступа.

        Returns:
            True при успешном обновлении, False при ошибке.
        """
        if self._credentials is None:
            logger.warning("Нет credentials для обновления токена")
            return False

        if not self._credentials.refresh_token:
            logger.warning("Нет refresh_token, требуется повторная авторизация")
            self.auth_error.emit(
                "Токен обновления отсутствует. Требуется повторная авторизация."
            )
            return False

        try:
            self._credentials.refresh(GoogleAuthRequest())
            self._save_token()
            logger.info("Токен успешно обновлён")
            self.token_refreshed.emit()
            return True
        except Exception as e:
            error_msg = f"Ошибка обновления токена: {e}"
            logger.error(error_msg)
            self.auth_error.emit(error_msg)
            self._credentials = None
            return False

    def get_credentials(self) -> Optional[Credentials]:
        """
        Получить текущие OAuth-credentials.

        Автоматически обновляет токен, если он истёк.

        Returns:
            Объект Credentials или None, если не авторизован.
        """
        if self._credentials is None:
            return None

        if self._credentials.expired and self._credentials.refresh_token:
            if not self.refresh_token():
                return None

        return self._credentials

    def get_user_info(self) -> Optional[UserInfo]:
        """
        Получить информацию о текущем пользователе Google.

        Запрашивает данные профиля через Google People API
        и информацию о хранилище через Drive API.

        Returns:
            Объект UserInfo или None при ошибке.
        """
        creds = self.get_credentials()
        if creds is None:
            logger.warning("Нет credentials для получения информации о пользователе")
            return None

        try:
            # Получаем профиль через OAuth2 userinfo endpoint
            service = build('oauth2', 'v2', credentials=creds)
            user_data = service.userinfo().get().execute()

            name = user_data.get('name', '')
            email = user_data.get('email', '')
            avatar_url = user_data.get('picture', None)

            # Загрузка аватара
            avatar_data = None
            if avatar_url:
                try:
                    resp = requests.get(avatar_url, timeout=10)
                    if resp.status_code == 200:
                        avatar_data = resp.content
                except Exception as e:
                    logger.warning("Не удалось загрузить аватар: %s", e)

            # Получаем информацию о хранилище
            storage_used, storage_total = self.get_storage_info()

            user_info = UserInfo(
                name=name,
                email=email,
                avatar_url=avatar_url,
                avatar_data=avatar_data,
                storage_used=storage_used,
                storage_total=storage_total,
            )

            logger.info(
                "Получена информация о пользователе: %s (%s)",
                user_info.name, user_info.email,
            )
            return user_info

        except Exception as e:
            logger.error("Ошибка получения информации о пользователе: %s", e)
            return None

    def get_storage_info(self) -> Tuple[int, int]:
        """
        Получить информацию о хранилище Google Drive.

        Returns:
            Кортеж (использовано_байт, всего_байт).
            При ошибке возвращает (0, 0).
        """
        creds = self.get_credentials()
        if creds is None:
            return (0, 0)

        try:
            service = build('drive', 'v3', credentials=creds)
            about = service.about().get(fields='storageQuota').execute()
            quota = about.get('storageQuota', {})

            used = int(quota.get('usage', 0))
            total = int(quota.get('limit', 0))

            logger.debug(
                "Хранилище: использовано %d байт из %d байт", used, total,
            )
            return (used, total)

        except Exception as e:
            logger.error("Ошибка получения информации о хранилище: %s", e)
            return (0, 0)

    def logout(self) -> None:
        """
        Выполнить выход из аккаунта.

        Удаляет файл token.json и сбрасывает credentials.
        """
        self._credentials = None

        if os.path.exists(self._token_path):
            try:
                os.remove(self._token_path)
                logger.info("Файл token.json удалён: %s", self._token_path)
            except OSError as e:
                logger.error("Ошибка удаления token.json: %s", e)

        logger.info("Пользователь вышел из аккаунта")

    def __repr__(self) -> str:
        status = "авторизован" if self.is_authenticated else "не авторизован"
        return f"AuthManager(статус={status})"
