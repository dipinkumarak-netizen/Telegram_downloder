from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from app.api.routes import router
from app.config import get_settings
from app.database import Database
from app.downloader import Downloader
from app.jellyfin import JellyfinClient
from app.logging_config import configure_logging
from app.queue_manager import QueueManager
from app.services.telegram_auth import TelegramAuthService
from app.telegram_client import TelegramService

settings = get_settings()
settings.ensure_directories()
configure_logging(settings.log_level, settings.log_dir)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database = Database(settings.database_path)
    await database.initialize()
    queue = QueueManager(database, settings.concurrent_downloads)
    jellyfin = JellyfinClient(settings)
    app.state.settings = settings
    app.state.database = database
    app.state.queue = queue
    app.state.telegram = None
    app.state.downloader = None

    async def start_telegram_runtime() -> None:
        current = app.state.telegram
        if current is not None:
            await current.stop()
        telegram = TelegramService(settings, database, queue.enqueue)
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

    telegram_auth = TelegramAuthService(
        settings,
        on_authorized=start_telegram_runtime,
        active_client_provider=active_auth_client,
    )
    app.state.telegram_auth = telegram_auth
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


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.dashboard_host,
        port=settings.dashboard_port,
        log_level=settings.log_level.lower(),
    )
