import asyncio
from pathlib import Path

import pytest

from app.database import Database
from app.models import DownloadState


async def new_job(database: Database, **overrides: object) -> int:
    data = {
        "chat_id": -1001,
        "message_id": 7,
        "file_id": "42",
        "file_unique_id": "42:hash:100",
        "original_filename": "Movie.2025.mkv",
        "file_size": 100,
        "mime_type": "video/x-matroska",
        "category": "movies",
    }
    data.update(overrides)
    job_id, duplicate = await database.add_download(**data)
    assert duplicate is None and job_id is not None
    return job_id


@pytest.fixture
def database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "db.sqlite")
    asyncio.run(database.initialize())
    yield database
    asyncio.run(database.close())


async def test_duplicate_message_is_rejected(database: Database) -> None:
    await new_job(database)
    job_id, reason = await database.add_download(
        chat_id=-1001,
        message_id=7,
        original_filename="different.mkv",
        file_size=200,
        category="videos",
    )
    assert job_id is None and reason == "message"


async def test_active_unique_file_is_rejected(database: Database) -> None:
    await new_job(database)
    second, reason = await database.add_download(
        chat_id=-1002,
        message_id=8,
        file_unique_id="42:hash:100",
        original_filename="copy.mkv",
        file_size=100,
        category="videos",
    )
    assert second is None and reason == "file_unique_id"


async def test_completed_unique_file_is_rejected(database: Database) -> None:
    job_id = await new_job(database)
    await database.transition(job_id, DownloadState.COMPLETED)
    second, reason = await database.add_download(
        chat_id=-1002,
        message_id=8,
        file_unique_id="42:hash:100",
        original_filename="copy.mkv",
        file_size=100,
        category="videos",
    )
    assert second is None and reason == "file_unique_id"


async def test_database_state_transitions(database: Database) -> None:
    job_id = await new_job(database)
    assert await database.transition(
        job_id, DownloadState.DOWNLOADING, allowed_from=(DownloadState.QUEUED,)
    )
    await database.update_progress(job_id, 50, 100, 25)
    job = await database.get(job_id)
    assert job and job.progress == 50 and job.speed_bps == 25
    await database.transition(job_id, DownloadState.COMPLETED, saved_path="/x/movie.mkv")
    job = await database.get(job_id)
    assert job and job.state == DownloadState.COMPLETED and job.completed_at


async def test_restart_recovers_downloading_job(database: Database) -> None:
    job_id = await new_job(database)
    await database.transition(job_id, DownloadState.DOWNLOADING)
    await database.initialize()
    job = await database.get(job_id)
    assert job and job.state == DownloadState.QUEUED


async def test_clear_history_keeps_active_downloads(database: Database) -> None:
    active = await new_job(database, message_id=10, file_unique_id="active")
    completed = await new_job(database, message_id=11, file_unique_id="completed")
    cancelled = await new_job(database, message_id=12, file_unique_id="cancelled")
    failed = await new_job(database, message_id=13, file_unique_id="failed")
    await database.transition(completed, DownloadState.COMPLETED)
    await database.transition(cancelled, DownloadState.CANCELLED)
    await database.transition(failed, DownloadState.FAILED)
    await database.event("INFO", "one")
    await database.event("ERROR", "two")

    cleared = await database.clear_history()

    assert cleared == {"downloads": 3, "events": 2}
    jobs = await database.list_jobs(limit=10)
    assert [job.id for job in jobs] == [active]
    assert (await database.get(active)).state == DownloadState.QUEUED  # type: ignore[union-attr]
    assert await database.recent_events() == []
