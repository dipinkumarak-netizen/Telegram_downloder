from __future__ import annotations

import asyncio
import logging

from app.database import Database
from app.downloader import Downloader

logger = logging.getLogger(__name__)


class QueueManager:
    def __init__(self, database: Database, concurrency: int):
        self.database = database
        self.concurrency = concurrency
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        self.downloader: Downloader | None = None
        self.workers: list[asyncio.Task[None]] = []
        self._known: set[int] = set()

    async def enqueue(self, job_id: int) -> None:
        if job_id not in self._known:
            self._known.add(job_id)
            await self.queue.put(job_id)

    async def start(self, downloader: Downloader) -> None:
        self.downloader = downloader
        for job_id in await self.database.queued_ids():
            await self.enqueue(job_id)
        self.workers = [
            asyncio.create_task(self._worker(index), name=f"download-worker-{index}")
            for index in range(self.concurrency)
        ]

    async def stop(self) -> None:
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()

    async def _worker(self, index: int) -> None:
        assert self.downloader
        while True:
            job_id = await self.queue.get()
            requeued = False
            try:
                await self.downloader.process(job_id)
                job = await self.database.get(job_id)
                if job and job.state.value == "queued":
                    await self.queue.put(job_id)
                    requeued = True
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unhandled worker error worker=%s job_id=%s", index, job_id)
            finally:
                if not requeued:
                    self._known.discard(job_id)
                self.queue.task_done()
