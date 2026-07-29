from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.database import Database
from app.models import DownloadState
from app.queue_manager import QueueManager


async def add_job(database: Database, message_id: int, message_date: str | None = None) -> int:
    job_id, duplicate = await database.add_download(
        chat_id=-1001,
        message_id=message_id,
        file_id=str(message_id),
        file_unique_id=f"file-{message_id}",
        original_filename=f"{message_id}.mkv",
        file_size=100,
        mime_type="video/x-matroska",
        category="movies",
        message_date=message_date,
    )
    assert job_id is not None and duplicate is None
    return job_id


@pytest.fixture
def database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "queue.sqlite")
    asyncio.run(database.initialize())
    yield database
    asyncio.run(database.close())


class RecordingDownloader:
    def __init__(
        self, database: Database, retry_job: int | None = None, fail_job: int | None = None
    ):
        self.database = database
        self.retry_job = retry_job
        self.fail_job = fail_job
        self.attempts: dict[int, int] = {}
        self.started: list[int] = []
        self.active = 0
        self.max_active = 0

    async def process(self, job_id: int) -> None:
        self.started.append(job_id)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.attempts[job_id] = self.attempts.get(job_id, 0) + 1
        await asyncio.sleep(0.01)
        self.active -= 1
        if job_id == self.retry_job and self.attempts[job_id] == 1:
            await self.database.transition(job_id, DownloadState.QUEUED, retry_count=1)
        elif job_id == self.fail_job:
            await self.database.transition(job_id, DownloadState.FAILED)
        else:
            await self.database.transition(job_id, DownloadState.COMPLETED)


async def test_single_worker_fifo_duplicate_failure_and_retry(database: Database) -> None:
    first = await add_job(database, 1)
    second = await add_job(database, 2)
    third = await add_job(database, 3)
    downloader = RecordingDownloader(database, retry_job=first, fail_job=second)
    queue = QueueManager(database, concurrency=8)

    assert await queue.enqueue(first)
    assert not await queue.enqueue(first)
    assert await queue.enqueue(second)
    assert await queue.enqueue(third)
    await queue.start(downloader)  # startup scan must not duplicate already-known jobs
    first_worker = queue.worker
    await queue.start(downloader)  # repeated startup must not create a second worker
    assert queue.worker is first_worker
    await asyncio.wait_for(queue.queue.join(), timeout=2)
    await queue.stop()

    assert downloader.started == [first, first, second, third]
    assert downloader.max_active == 1
    assert queue.active_downloads == 0
    assert (await database.get(first)).state == DownloadState.COMPLETED  # type: ignore[union-attr]
    assert (await database.get(second)).state == DownloadState.FAILED  # type: ignore[union-attr]
    assert (await database.get(third)).state == DownloadState.COMPLETED  # type: ignore[union-attr]


async def test_restart_queue_is_oldest_first(database: Database) -> None:
    newest = await add_job(database, 30, "2026-01-03T00:00:00+00:00")
    oldest = await add_job(database, 10, "2026-01-01T00:00:00+00:00")
    middle = await add_job(database, 20, "2026-01-02T00:00:00+00:00")
    await database.transition(oldest, DownloadState.DOWNLOADING)
    await database.initialize()

    assert await database.queued_ids() == [oldest, middle, newest]
