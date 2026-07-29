from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.downloader import Downloader, retry_delay
from app.models import DownloadState


def test_retry_delay_exponential_and_capped() -> None:
    assert retry_delay(1, 10) == 10
    assert retry_delay(2, 10) == 20
    assert retry_delay(20, 10) == 3600


class StubDatabase:
    def __init__(self, job) -> None:
        self.job = job
        self.progress_updates: list[tuple[int, int, float]] = []

    async def get(self, job_id: int):
        return self.job if self.job.id == job_id else None

    async def transition(self, job_id: int, state: DownloadState, **fields) -> bool:
        assert job_id == self.job.id
        self.job.state = state
        for key, value in fields.items():
            setattr(self.job, key, value)
        return True

    async def update_progress(self, job_id: int, downloaded: int, total: int, speed: float) -> None:
        assert job_id == self.job.id
        self.job.downloaded_bytes = downloaded
        self.progress_updates.append((downloaded, total, speed))

    async def event(self, level: str, message: str) -> None:
        return None


class StubTelegramClient:
    def __init__(self, message, chunks: list[bytes]) -> None:
        self.message = message
        self.chunks = chunks
        self.iter_download_offsets: list[int] = []
        self.download_media_calls = 0

    async def get_messages(self, chat_id: int, ids: int):
        return self.message

    async def download_media(self, message, file: str, progress_callback) -> str:
        self.download_media_calls += 1
        path = Path(file)
        written = 0
        with path.open("wb") as handle:
            for chunk in self.chunks:
                handle.write(chunk)
                written += len(chunk)
        await progress_callback(written, self.message.file.size)
        return file

    async def iter_download(self, message, offset: int):
        self.iter_download_offsets.append(offset)
        sent = 0
        for chunk in self.chunks:
            sent += len(chunk)
            if sent <= offset:
                continue
            if sent - len(chunk) < offset:
                yield chunk[offset - (sent - len(chunk)) :]
            else:
                yield chunk


class StubTelegramService:
    def __init__(self, client) -> None:
        self.client = client

    async def ensure_connected(self) -> None:
        return None


class StubJellyfin:
    async def refresh(self) -> None:
        return None


async def test_process_resumes_existing_partial_download(tmp_path: Path) -> None:
    part_dir = tmp_path / "downloads" / "incomplete"
    movie_dir = tmp_path / "downloads" / "movies"
    part_dir.mkdir(parents=True)
    movie_dir.mkdir(parents=True)
    part = part_dir / "1-Movie.2025.mkv.part"
    part.write_bytes(b"abcd")

    job = SimpleNamespace(
        id=1,
        chat_id=-1001,
        message_id=7,
        original_filename="Movie.2025.mkv",
        category="movies",
        file_size=8,
        state=DownloadState.QUEUED,
        cancel_requested=False,
        retry_count=0,
    )
    message = SimpleNamespace(file=SimpleNamespace(size=8))
    database = StubDatabase(job)
    telegram_client = StubTelegramClient(message, [b"abcdefgh"])
    downloader = Downloader(
        settings=SimpleNamespace(
            download_root=tmp_path / "downloads",
            min_free_space_gb=0,
            bandwidth_limit_mbps=0,
            retry_base_seconds=1,
            max_retries=3,
        ),
        database=database,
        telegram=StubTelegramService(telegram_client),
        jellyfin=StubJellyfin(),
    )

    await downloader.process(job.id)

    assert telegram_client.download_media_calls == 0
    assert telegram_client.iter_download_offsets == [4]
    assert not part.exists()
    assert (movie_dir / "Movie.2025.mkv").read_bytes() == b"abcdefgh"
    assert database.progress_updates[0][:2] == (4, 8)
