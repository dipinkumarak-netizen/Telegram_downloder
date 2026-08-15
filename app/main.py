from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.api.routes import router
from app.api.setup_routes import router as setup_router
from app.config import get_settings
from app.database import Database
from app.downloader import Downloader
from app.jellyfin import JellyfinClient
from app.logging_config import configure_logging
from app.queue_manager import QueueManager
from app.services.admin_auth import AdminAuthService
from app.services.settings_store import RuntimeSettings, SettingsStore
from app.services.setup import SetupService
from app.services.telegram_auth import TelegramAuthService
from app.services.telegram_sources import TelegramSourceService
from app.telegram_client import TelegramService

settings = get_settings()
settings.ensure_directories()
configure_logging(settings.log_level, settings.log_dir)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = SettingsStore(settings.config_dir / "settings.json")
    runtime_settings = RuntimeSettings(settings, store)
    runtime_settings.apply_persisted()
    settings.ensure_directories()
    database = Database(settings.database_path)
    await database.initialize()
    queue = QueueManager(database, settings.concurrent_downloads)
    jellyfin = JellyfinClient(settings)
    app.state.settings = settings
    app.state.database = database
    app.state.queue = queue
    app.state.telegram = None
    app.state.downloader = None
    app.state.settings_store = store
    app.state.runtime_settings = runtime_settings
    admin_auth = AdminAuthService(store, settings)
    app.state.admin_auth = admin_auth

    async def start_telegram_runtime() -> None:
        current = app.state.telegram
        if current is not None:
            await current.stop()
        telegram = TelegramService(
            settings,
            database,
            queue.enqueue,
            source_ids_provider=lambda: telegram_sources.effective_ids(settings.allowed_chat_ids),
        )
        try:
            await telegram.start()
        except Exception:
            await telegram.stop()
            raise
        downloader = Downloader(settings, database, telegram, jellyfin)
        app.state.telegram = telegram
        app.state.downloader = downloader
        await queue.start(downloader)

    def active_auth_client():
        telegram = app.state.telegram
        return telegram.client if telegram is not None and telegram.connected else None

    telegram_sources = TelegramSourceService(store, active_auth_client)
    app.state.telegram_sources = telegram_sources

    telegram_auth = TelegramAuthService(
        settings,
        on_authorized=start_telegram_runtime,
        active_client_provider=active_auth_client,
    )
    app.state.telegram_auth = telegram_auth
    app.state.setup_service = SetupService(
        settings,
        store,
        runtime_settings,
        admin_auth,
        telegram_auth,
        telegram_sources=telegram_sources,
    )
    if telegram_auth.configured:
        try:
            await start_telegram_runtime()
        except Exception as exc:
            logger.warning("Telegram listener is not ready; browser login is available: %s", exc)
    await database.event("INFO", "Application started")
    try:
        yield
    finally:
        logger.info("Graceful shutdown started")
        await telegram_auth.close()
        await queue.stop()
        if app.state.telegram is not None:
            await app.state.telegram.stop()
        await database.close()


app = FastAPI(title="Telegram Media Downloader", lifespan=lifespan)
app.include_router(router)
app.include_router(setup_router)


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.dashboard_host,
        port=settings.dashboard_port,
        log_level=settings.log_level.lower(),
    )
