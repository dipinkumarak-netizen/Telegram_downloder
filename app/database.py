from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from queue import Queue
from threading import Event, Lock, Thread
from typing import Any

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
    message_date TEXT,
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

_STOP = object()


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class _Task:
    fn: Any
    done: Event
    result: Any = None
    error: Exception | None = None


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._tasks: Queue[_Task | object] = Queue()
        self._thread: Thread | None = None
        self._lock = Lock()
        self._closed = False

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("Database is closed")
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = Thread(target=self._worker, name="database-worker", daemon=True)
            self._thread.start()

    def _worker(self) -> None:
        connection = self._connect()
        try:
            while True:
                task = self._tasks.get()
                if task is _STOP:
                    break
                assert isinstance(task, _Task)
                try:
                    task.result = task.fn(connection)
                except Exception as exc:
                    task.error = exc
                finally:
                    task.done.set()
        finally:
            connection.close()

    async def _run(self, fn):
        self._ensure_worker()
        task = _Task(fn=fn, done=Event())
        self._tasks.put(task)
        while not task.done.is_set():
            await asyncio.sleep(0)
        if task.error is not None:
            raise task.error
        return task.result

    async def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            thread = self._thread
            self._thread = None
        if thread is None:
            return
        self._tasks.put(_STOP)
        while thread.is_alive():
            thread.join(timeout=0.1)
            if thread.is_alive():
                await asyncio.sleep(0)

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

        def operation(db: sqlite3.Connection) -> None:
            db.executescript(SCHEMA)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(downloads)").fetchall()}
            if "message_date" not in columns:
                db.execute("ALTER TABLE downloads ADD COLUMN message_date TEXT")
            db.execute(
                """UPDATE downloads SET state=?, error_message=COALESCE(error_message, ?),
                   updated_at=? WHERE state=?""",
                (
                    DownloadState.QUEUED,
                    "Recovered after application restart",
                    utcnow(),
                    DownloadState.DOWNLOADING,
                ),
            )
            db.commit()

        await self._run(operation)

    async def add_download(self, **values: object) -> tuple[int | None, str | None]:
        now = utcnow()
        unique_id = values.get("file_unique_id")

        def operation(db: sqlite3.Connection) -> tuple[int | None, str | None]:
            if unique_id:
                row = db.execute(
                    """SELECT id FROM downloads WHERE file_unique_id=?
                       AND state IN (?,?,?,?) LIMIT 1""",
                    (
                        unique_id,
                        DownloadState.QUEUED,
                        DownloadState.DOWNLOADING,
                        DownloadState.PAUSED,
                        DownloadState.COMPLETED,
                    ),
                ).fetchone()
                if row:
                    return None, "file_unique_id"
            try:
                cursor = db.execute(
                    """INSERT INTO downloads
                    (chat_id,message_id,file_id,file_unique_id,original_filename,file_size,
                     mime_type,category,state,message_date,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                        values.get("message_date"),
                        now,
                        now,
                    ),
                )
                db.commit()
                return cursor.lastrowid, None
            except sqlite3.IntegrityError:
                return None, "message"

        return await self._run(operation)

    async def get(self, job_id: int) -> DownloadJob | None:
        def operation(db: sqlite3.Connection) -> DownloadJob | None:
            row = db.execute("SELECT * FROM downloads WHERE id=?", (job_id,)).fetchone()
            return self._job(row) if row else None

        return await self._run(operation)

    async def list_jobs(self, limit: int = 100, state: str | None = None) -> list[DownloadJob]:
        def operation(db: sqlite3.Connection) -> list[DownloadJob]:
            query = "SELECT * FROM downloads"
            params: list[object] = []
            if state:
                query += " WHERE state=?"
                params.append(state)
            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)
            rows = db.execute(query, params).fetchall()
            return [self._job(row) for row in rows]

        return await self._run(operation)

    async def queued_ids(self) -> list[int]:
        def operation(db: sqlite3.Connection) -> list[int]:
            rows = db.execute(
                """SELECT id FROM downloads WHERE state IN (?,?)
                   ORDER BY COALESCE(message_date, created_at), message_id, id""",
                (DownloadState.QUEUED, DownloadState.PAUSED),
            ).fetchall()
            return [row["id"] for row in rows]

        return await self._run(operation)

    async def transition(
        self,
        job_id: int,
        state: DownloadState,
        *,
        allowed_from: tuple[DownloadState, ...] | None = None,
        **fields: object,
    ) -> bool:
        def operation(db: sqlite3.Connection) -> bool:
            update_fields = dict(fields)
            update_fields.update(state=state, updated_at=utcnow())
            if state == DownloadState.COMPLETED:
                update_fields["completed_at"] = utcnow()
                update_fields["progress"] = 100.0
            assignments = ", ".join(f"{key}=?" for key in update_fields)
            params = list(update_fields.values()) + [job_id]
            query = f"UPDATE downloads SET {assignments} WHERE id=?"
            if allowed_from:
                query += f" AND state IN ({','.join('?' for _ in allowed_from)})"
                params.extend(allowed_from)
            cursor = db.execute(query, params)
            db.commit()
            return cursor.rowcount == 1

        return await self._run(operation)

    async def update_progress(self, job_id: int, downloaded: int, total: int, speed: float) -> None:
        def operation(db: sqlite3.Connection) -> None:
            progress = (downloaded / total * 100) if total else 0
            db.execute(
                """UPDATE downloads SET downloaded_bytes=?,progress=?,speed_bps=?,updated_at=?
                   WHERE id=? AND state=?""",
                (downloaded, progress, speed, utcnow(), job_id, DownloadState.DOWNLOADING),
            )
            db.commit()

        await self._run(operation)

    async def request_cancel(self, job_id: int) -> bool:
        def operation(db: sqlite3.Connection) -> bool:
            cursor = db.execute(
                """UPDATE downloads SET cancel_requested=1,updated_at=?
                   WHERE id=? AND state IN (?,?)""",
                (utcnow(), job_id, DownloadState.QUEUED, DownloadState.DOWNLOADING),
            )
            db.commit()
            return cursor.rowcount == 1

        return await self._run(operation)

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
        def operation(db: sqlite3.Connection) -> dict[str, object]:
            rows = db.execute("SELECT state,COUNT(*) count FROM downloads GROUP BY state").fetchall()
            total = db.execute(
                "SELECT COALESCE(SUM(file_size),0) total FROM downloads WHERE state=?",
                (DownloadState.COMPLETED,),
            ).fetchone()
            return {
                "counts": {row["state"]: row["count"] for row in rows},
                "total_downloaded": total["total"],
            }

        return await self._run(operation)

    async def event(self, level: str, message: str) -> None:
        def operation(db: sqlite3.Connection) -> None:
            db.execute(
                "INSERT INTO app_events(level,message,created_at) VALUES(?,?,?)",
                (level, message[:1000], utcnow()),
            )
            db.execute(
                """DELETE FROM app_events WHERE id NOT IN
                   (SELECT id FROM app_events ORDER BY id DESC LIMIT 200)"""
            )
            db.commit()

        await self._run(operation)

    async def recent_events(self, limit: int = 30) -> list[dict[str, object]]:
        def operation(db: sqlite3.Connection) -> list[dict[str, object]]:
            rows = db.execute("SELECT * FROM app_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(row) for row in rows]

        return await self._run(operation)

    async def clear_history(self) -> dict[str, int]:
        def operation(db: sqlite3.Connection) -> dict[str, int]:
            downloads = db.execute(
                "DELETE FROM downloads WHERE state IN (?,?,?)",
                (DownloadState.COMPLETED, DownloadState.FAILED, DownloadState.CANCELLED),
            ).rowcount
            events = db.execute("DELETE FROM app_events").rowcount
            db.commit()
            return {"downloads": downloads, "events": events}

        return await self._run(operation)

    @staticmethod
    def _job(row: sqlite3.Row) -> DownloadJob:
        data = dict(row)
        data["state"] = DownloadState(data["state"])
        data["cancel_requested"] = bool(data["cancel_requested"])
        return DownloadJob(**data)
