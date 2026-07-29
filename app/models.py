from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class DownloadState(StrEnum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


ACTIVE_STATES = (DownloadState.QUEUED, DownloadState.DOWNLOADING, DownloadState.PAUSED)


@dataclass(slots=True)
class DownloadJob:
    id: int
    chat_id: int
    message_id: int
    file_id: str | None
    file_unique_id: str | None
    original_filename: str
    saved_path: str | None
    temp_path: str | None
    file_size: int
    mime_type: str | None
    category: str
    state: DownloadState
    progress: float
    downloaded_bytes: int
    speed_bps: float
    retry_count: int
    error_message: str | None
    message_date: str | None
    created_at: str
    updated_at: str
    completed_at: str | None
    status_message_id: int | None
    cancel_requested: bool

    @property
    def filename(self) -> str:
        return Path(self.saved_path or self.original_filename).name
