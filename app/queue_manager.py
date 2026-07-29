from __future__ import annotations

import asyncio
import logging
import os
import uuid

from app.database import Database
from app.downloader import Downloader
from app.models import DownloadState

logger = logging.getLogger(__name__)


class QueueManager:
    def __init__(self, database: Database, concurrency: int):
        self.database = database
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        self.downloader: Downloader | None = None
        self.worker: asyncio.Task[None] | None = None
        self._known: set[int] = set()
        self.active_downloads = 0
        self.instance_id = uuid.uuid4().hex[:12]
        if concurrency != 1:
            logger.warning(
                "Strict FIFO mode forces download concurrency to 1; configured value=%s",
                concurrency,
            )

    async def enqueue(self, job_id: int) -> bool:
        if job_id in self._known:
            logger.info("Duplicate queue request skipped job_id=%s", job_id)
            return False
        job = await self.database.get(job_id)
        if not job or job.state not in {DownloadState.QUEUED, DownloadState.PAUSED}:
            logger.info("Inactive queue request skipped job_id=%s", job_id)
            return False
        self._known.add(job_id)
        await self.queue.put(job_id)
        logger.info(
            "File queued chat_id=%s message_id=%s filename=%s pending=%s",
            job.chat_id,
            job.message_id,
            job.original_filename,
            self.queue.qsize(),
        )
        return True

    async def start(self, downloader: Downloader) -> None:
        self.downloader = downloader
        if self.worker is not None and not self.worker.done():
            logger.info(
                "Download worker already running instance=%s pid=%s worker_task_id=%s",
                self.instance_id,
                os.getpid(),
                id(self.worker),
            )
            return
        for job_id in await self.database.queued_ids():
            await self.enqueue(job_id)
        self.worker = asyncio.create_task(self._worker(), name="download-worker")
        logger.info(
            "Download worker started instance=%s pid=%s worker_task_id=%s workers=1",
            self.instance_id,
            os.getpid(),
            id(self.worker),
        )

    async def stop(self) -> None:
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
            self.worker = None

    async def _worker(self) -> None:
        assert self.downloader
        while True:
            job_id = await self.queue.get()
            try:
                job = await self.database.get(job_id)
                if job:
                    logger.info(
                        "Download started chat_id=%s message_id=%s filename=%s pending=%s",
                        job.chat_id,
                        job.message_id,
                        job.original_filename,
                        self.queue.qsize(),
                    )
                first_attempt = True
                while job and (
                    job.state == DownloadState.QUEUED
                    or (first_attempt and job.state == DownloadState.PAUSED)
                ):
                    first_attempt = False
                    self.active_downloads += 1
                    logger.info(
                        "Download active message_id=%s active=%s pending=%s",
                        job.message_id,
                        self.active_downloads,
                        self.queue.qsize(),
                    )
                    if self.active_downloads > 1:
                        logger.error(
                            "CONCURRENCY VIOLATION: multiple downloads active "
                            "message_id=%s active=%s",
                            job.message_id,
                            self.active_downloads,
                        )
                    try:
                        await self.downloader.process(job_id)
                    finally:
                        self.active_downloads -= 1
                        logger.info(
                            "Download inactive message_id=%s active=%s pending=%s",
                            job.message_id,
                            self.active_downloads,
                            self.queue.qsize(),
                        )
                    job = await self.database.get(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unhandled worker error job_id=%s", job_id)
            finally:
                self._known.discard(job_id)
                self.queue.task_done()
                logger.info(
                    "Moving to next queued item job_id=%s pending=%s",
                    job_id,
                    self.queue.qsize(),
                )
