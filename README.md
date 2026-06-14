# Google Drive Sync Client (Yandex.Disk Style GUI)

A desktop synchronization client for Google Drive featuring a premium, user-friendly interface inspired by Yandex.Disk. Built with Python 3 and PyQt6.

## Features

* **Premium UI**: Light theme faithfully reproducing Yandex.Disk's design language (colors, typography, rounded borders, custom icons, and storage upgrade button).
* **Real-time Sync**: Automatic two-way background synchronization between a local folder and Google Drive.
* **System Tray Integration**: Full-featured system tray experience with custom dynamic status icons (Synced, Syncing, Paused, Error) and a context menu.
* **Folder Navigation**: Local directory structure viewing prior to sync and remote file management.
* **Global Hotkeys**: Quick screen capture hotkeys with integration for Yandex.Disk screenshot editor.
* **Account Switching**: Easy log-in and log-out (account switching) directly from the application interface.

## Requirements

* Python 3.10+
* PyQt6
* Google API Client Libraries (`google-api-python-client`, `google-auth-oauthlib`, `google-auth-httplib2`)

## Installation and Run

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd <repository-folder>
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run the application:
   ```bash
   python main.pyw
   ```

---

# Клиент синхронизации Google Drive (в стиле Яндекс.Диска)

Десктопный клиент синхронизации для Google Drive с премиальным и удобным интерфейсом, вдохновленным Яндекс.Диском. Разработан на Python 3 и PyQt6.

## Возможности

* **Премиальный интерфейс**: Светлая тема, точно воспроизводящая визуальный стиль Яндекс.Диска (цвета, шрифты, скругления, кастомные иконки и промо-кнопка покупки места).
* **Синхронизация в реальном времени**: Автоматическая двусторонняя фоновая синхронизация между локальной папкой и Google Drive.
* **Интеграция с системным треем**: Удобная иконка в трее с динамическими статусами (Синхронизировано, Синхронизация, Пауза, Ошибка) и контекстным меню.
* **Навигация по папкам**: Локальный просмотр структуры папок перед синхронизацией и удаленное управление файлами.
* **Глобальные горячие клавиши**: Быстрое создание скриншотов с поддержкой запуска встроенного редактора скриншотов Яндекс.Диска.
* **Смена аккаунта**: Быстрый вход и выход из профиля Google Drive прямо из приложения.
