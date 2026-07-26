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
    telegram = TelegramService(settings, database, queue.enqueue)
    jellyfin = JellyfinClient(settings)
    downloader = Downloader(settings, database, telegram, jellyfin)
    app.state.settings = settings
    app.state.database = database
    app.state.queue = queue
    app.state.telegram = telegram
    await telegram.start()
    await queue.start(downloader)
    await database.event("INFO", "Application started")
    try:
        yield
    finally:
        logger.info("Graceful shutdown started")
        await queue.stop()
        await telegram.stop()


app = FastAPI(title="Telegram Media Downloader", lifespan=lifespan)
app.include_router(router)


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.dashboard_host,
        port=settings.dashboard_port,
        log_level=settings.log_level.lower(),
    )
