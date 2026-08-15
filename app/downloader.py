from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from collections.abc import AsyncIterator
from pathlib import Path

from telethon.errors import FloodWaitError

from app.classifier import unique_destination
from app.config import Settings
from app.database import Database
from app.jellyfin import JellyfinClient
from app.models import DownloadState
from app.telegram_client import TelegramService

logger = logging.getLogger(__name__)


class CancelledDownload(Exception):
    pass


def retry_delay(attempt: int, base: float, cap: float = 3600) -> float:
    return min(cap, base * (2 ** max(0, attempt - 1)))


class Downloader:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        telegram: TelegramService,
        jellyfin: JellyfinClient,
    ):
        self.settings = settings
        self.database = database
        self.telegram = telegram
        self.jellyfin = jellyfin

    async def process(self, job_id: int) -> None:
        job = await self.database.get(job_id)
        if not job or job.state not in {DownloadState.QUEUED, DownloadState.PAUSED}:
            return
        if job.cancel_requested:
            await self.database.transition(job_id, DownloadState.CANCELLED)
            return
        free = shutil.disk_usage(self.settings.download_root).free
        if free < int(self.settings.min_free_space_gb * 1024**3) + job.file_size:
            await self.database.transition(
                job_id, DownloadState.PAUSED, error_message="Insufficient free disk space"
            )
            await self.database.event("WARNING", f"Paused for disk space: {job.original_filename}")
            return
        destination = unique_destination(
            self.settings.download_root / job.category, job.original_filename
        )
        part = self.settings.temp_dir / f"{job.id}-{destination.name}.part"
        await self.database.transition(
            job_id,
            DownloadState.DOWNLOADING,
            allowed_from=(DownloadState.QUEUED, DownloadState.PAUSED),
            saved_path=str(destination),
            temp_path=str(part),
            error_message=None,
        )
        started = time.monotonic()
        last_update = 0.0
        resume_from = self._resume_offset(job.file_size, part)

        async def progress(received: int, total: int) -> None:
            nonlocal last_update
            current = time.monotonic()
            refreshed = await self.database.get(job_id)
            if refreshed and refreshed.cancel_requested:
                raise CancelledDownload()
            if current - last_update >= 1 or received == total:
                session_received = max(received - resume_from, 0)
                speed = session_received / max(current - started, 0.001)
                await self.database.update_progress(job_id, received, total, speed)
                last_update = current
            if self.settings.bandwidth_limit_mbps > 0:
                session_received = max(received - resume_from, 0)
                expected = session_received * 8 / (self.settings.bandwidth_limit_mbps * 1_000_000)
                delay = expected - (current - started)
                if delay > 0:
                    await asyncio.sleep(min(delay, 1))

        try:
            await self.telegram.ensure_connected()
            message = await self.telegram.client.get_messages(job.chat_id, ids=job.message_id)
            if not message or not message.file:
                raise RuntimeError("Telegram message or media is no longer available")
            part.parent.mkdir(parents=True, exist_ok=True)
            total = int(message.file.size or job.file_size or 0)
            if total and resume_from > total:
                part.unlink(missing_ok=True)
                resume_from = 0
            if resume_from:
                await self.database.update_progress(job_id, resume_from, total, 0)
            if total and resume_from == total and part.exists():
                result = str(part)
            else:
                result = await self._download_media(message, part, resume_from, total, progress)
            if not result or not part.exists() or part.stat().st_size <= 0:
                raise RuntimeError("Telegram returned an empty or missing file")
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(part, destination)
            await self.database.transition(
                job_id,
                DownloadState.COMPLETED,
                downloaded_bytes=destination.stat().st_size,
                speed_bps=0,
            )
            await self.database.event("INFO", f"Completed: {destination.name}")
            logger.info(
                "Download completed chat_id=%s message_id=%s filename=%s path=%s",
                job.chat_id,
                job.message_id,
                job.original_filename,
                destination,
            )
            if job.category in {"movies", "tv", "videos", "audio"}:
                await self.jellyfin.refresh()
        except CancelledDownload:
            part.unlink(missing_ok=True)
            await self.database.transition(job_id, DownloadState.CANCELLED, speed_bps=0)
            await self.database.event("INFO", f"Cancelled: {job.original_filename}")
        except asyncio.CancelledError:
            await self.database.transition(
                job_id, DownloadState.QUEUED, error_message="Interrupted by shutdown", speed_bps=0
            )
            raise
        except Exception as exc:
            await self._failure(job_id, exc)

    @staticmethod
    def _resume_offset(file_size: int, part: Path) -> int:
        if not part.exists():
            return 0
        current_size = part.stat().st_size
        if current_size <= 0:
            part.unlink(missing_ok=True)
            return 0
        if file_size and current_size > file_size:
            part.unlink(missing_ok=True)
            return 0
        return current_size

    async def _download_media(
        self,
        message,
        part: Path,
        resume_from: int,
        total: int,
        progress,
    ) -> str:
        if resume_from == 0:
            result = await self.telegram.client.download_media(
                message, file=str(part), progress_callback=progress
            )
            return str(result) if result else ""
        await self._download_with_resume(message, part, resume_from, total, progress)
        return str(part)

    async def _download_with_resume(
        self,
        message,
        part: Path,
        resume_from: int,
        total: int,
        progress,
    ) -> None:
        await progress(resume_from, total)
        with part.open("ab") as handle:
            async for chunk in self._iter_chunks(message, resume_from):
                handle.write(bytes(chunk))
                current_size = handle.tell()
                await progress(current_size, total)

    def _iter_chunks(self, message, offset: int) -> AsyncIterator[bytes]:
        return self.telegram.client.iter_download(message, offset=offset)

    async def _failure(self, job_id: int, exc: Exception) -> None:
        job = await self.database.get(job_id)
        if not job:
            return
        retry_count = job.retry_count + 1
        wait = (
            exc.seconds
            if isinstance(exc, FloodWaitError)
            else retry_delay(retry_count, self.settings.retry_base_seconds)
        )
        if retry_count <= self.settings.max_retries:
            await self.database.transition(
                job_id,
                DownloadState.QUEUED,
                retry_count=retry_count,
                error_message=str(exc)[:1000],
                speed_bps=0,
            )
            await self.database.event(
                "WARNING", f"Retrying in {wait:.0f}s: {job.original_filename}"
            )
            logger.warning(
                "Retry attempt=%s/%s in %.0fs chat_id=%s message_id=%s filename=%s",
                retry_count,
                self.settings.max_retries,
                wait,
                job.chat_id,
                job.message_id,
                job.original_filename,
            )
            await asyncio.sleep(wait)
        else:
            await self.database.transition(
                job_id,
                DownloadState.FAILED,
                retry_count=retry_count,
                error_message=str(exc)[:1000],
                speed_bps=0,
            )
            await self.database.event("ERROR", f"Failed: {job.original_filename}: {exc}")
            logger.exception(
                "Download failed chat_id=%s message_id=%s filename=%s",
                job.chat_id,
                job.message_id,
                job.original_filename,
                exc_info=exc,
            )
