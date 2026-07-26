from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from app.models import DownloadJob, DownloadState

SCHEMA = """
CREATE TABLE IF NOT EXISTS downloads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    file_id TEXT,
    file_unique_id TEXT,
    original_filename TEXT NOT NULL,
    saved_path TEXT,
    temp_path TEXT,
    file_size INTEGER NOT NULL DEFAULT 0,
    mime_type TEXT,
    category TEXT NOT NULL,
    state TEXT NOT NULL,
    progress REAL NOT NULL DEFAULT 0,
    downloaded_bytes INTEGER NOT NULL DEFAULT 0,
    speed_bps REAL NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    status_message_id INTEGER,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    UNIQUE(chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_downloads_state ON downloads(state);
CREATE INDEX IF NOT EXISTS idx_downloads_unique ON downloads(file_unique_id);
CREATE TABLE IF NOT EXISTS app_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
PRAGMA user_version = 1;
"""


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[aiosqlite.Connection]:
        db = await aiosqlite.connect(self.path)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA busy_timeout=5000")
        try:
            yield db
        finally:
            await db.close()

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self.connect() as db:
            await db.executescript(SCHEMA)
            await db.execute(
                """UPDATE downloads SET state=?, error_message=COALESCE(error_message, ?),
                   updated_at=? WHERE state=?""",
                (
                    DownloadState.QUEUED,
                    "Recovered after application restart",
                    utcnow(),
                    DownloadState.DOWNLOADING,
                ),
            )
            await db.commit()

    async def add_download(self, **values: object) -> tuple[int | None, str | None]:
        now = utcnow()
        unique_id = values.get("file_unique_id")
        async with self.connect() as db:
            if unique_id:
                row = await (
                    await db.execute(
                        "SELECT id FROM downloads WHERE file_unique_id=? AND state=? LIMIT 1",
                        (unique_id, DownloadState.COMPLETED),
                    )
                ).fetchone()
                if row:
                    return None, "file_unique_id"
            try:
                cursor = await db.execute(
                    """INSERT INTO downloads
                    (chat_id,message_id,file_id,file_unique_id,original_filename,file_size,
                     mime_type,category,state,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        values["chat_id"],
                        values["message_id"],
                        values.get("file_id"),
                        unique_id,
                        values["original_filename"],
                        values.get("file_size", 0),
                        values.get("mime_type"),
                        values["category"],
                        DownloadState.QUEUED,
                        now,
                        now,
                    ),
                )
                await db.commit()
                return cursor.lastrowid, None
            except aiosqlite.IntegrityError:
                return None, "message"

    async def get(self, job_id: int) -> DownloadJob | None:
        async with self.connect() as db:
            row = await (
                await db.execute("SELECT * FROM downloads WHERE id=?", (job_id,))
            ).fetchone()
        return self._job(row) if row else None

    async def list_jobs(self, limit: int = 100, state: str | None = None) -> list[DownloadJob]:
        query, params = "SELECT * FROM downloads", []
        if state:
            query += " WHERE state=?"
            params.append(state)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        async with self.connect() as db:
            rows = await (await db.execute(query, params)).fetchall()
        return [self._job(row) for row in rows]

    async def queued_ids(self) -> list[int]:
        async with self.connect() as db:
            rows = await (
                await db.execute(
                    "SELECT id FROM downloads WHERE state IN (?,?) ORDER BY id",
                    (DownloadState.QUEUED, DownloadState.PAUSED),
                )
            ).fetchall()
        return [row["id"] for row in rows]

    async def transition(
        self,
        job_id: int,
        state: DownloadState,
        *,
        allowed_from: tuple[DownloadState, ...] | None = None,
        **fields: object,
    ) -> bool:
        fields.update(state=state, updated_at=utcnow())
        if state == DownloadState.COMPLETED:
            fields["completed_at"] = utcnow()
            fields["progress"] = 100.0
        assignments = ", ".join(f"{key}=?" for key in fields)
        params = list(fields.values()) + [job_id]
        query = f"UPDATE downloads SET {assignments} WHERE id=?"
        if allowed_from:
            query += f" AND state IN ({','.join('?' for _ in allowed_from)})"
            params.extend(allowed_from)
        async with self.connect() as db:
            cursor = await db.execute(query, params)
            await db.commit()
            return cursor.rowcount == 1

    async def update_progress(self, job_id: int, downloaded: int, total: int, speed: float) -> None:
        progress = (downloaded / total * 100) if total else 0
        async with self.connect() as db:
            await db.execute(
                """UPDATE downloads SET downloaded_bytes=?,progress=?,speed_bps=?,updated_at=?
                   WHERE id=? AND state=?""",
                (downloaded, progress, speed, utcnow(), job_id, DownloadState.DOWNLOADING),
            )
            await db.commit()

    async def request_cancel(self, job_id: int) -> bool:
        async with self.connect() as db:
            cursor = await db.execute(
                """UPDATE downloads SET cancel_requested=1,updated_at=?
                   WHERE id=? AND state IN (?,?)""",
                (utcnow(), job_id, DownloadState.QUEUED, DownloadState.DOWNLOADING),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def retry(self, job_id: int) -> bool:
        return await self.transition(
            job_id,
            DownloadState.QUEUED,
            allowed_from=(DownloadState.FAILED, DownloadState.CANCELLED),
            error_message=None,
            cancel_requested=0,
            progress=0,
            downloaded_bytes=0,
            speed_bps=0,
        )

    async def stats(self) -> dict[str, object]:
        async with self.connect() as db:
            rows = await (
                await db.execute("SELECT state,COUNT(*) count FROM downloads GROUP BY state")
            ).fetchall()
            total = await (
                await db.execute(
                    "SELECT COALESCE(SUM(file_size),0) total FROM downloads WHERE state=?",
                    (DownloadState.COMPLETED,),
                )
            ).fetchone()
        return {
            "counts": {row["state"]: row["count"] for row in rows},
            "total_downloaded": total["total"],
        }

    async def event(self, level: str, message: str) -> None:
        async with self.connect() as db:
            await db.execute(
                "INSERT INTO app_events(level,message,created_at) VALUES(?,?,?)",
                (level, message[:1000], utcnow()),
            )
            await db.execute(
                """DELETE FROM app_events WHERE id NOT IN
                   (SELECT id FROM app_events ORDER BY id DESC LIMIT 200)"""
            )
            await db.commit()

    async def recent_events(self, limit: int = 30) -> list[dict[str, object]]:
        async with self.connect() as db:
            rows = await (
                await db.execute("SELECT * FROM app_events ORDER BY id DESC LIMIT ?", (limit,))
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _job(row: aiosqlite.Row) -> DownloadJob:
        data = dict(row)
        data["state"] = DownloadState(data["state"])
        data["cancel_requested"] = bool(data["cancel_requested"])
        return DownloadJob(**data)
