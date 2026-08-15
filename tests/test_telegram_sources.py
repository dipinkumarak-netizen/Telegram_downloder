from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from telethon import utils
from telethon.tl.types import Channel, Chat

from app.database import Database
from app.services.settings_store import SettingsStore
from app.services.telegram_sources import TelegramSourceService
from app.telegram_client import TelegramService


class FakeDialogsClient:
    def __init__(self, dialogs: list[object], authorized: bool = True) -> None:
        self.dialogs = dialogs
        self.authorized = authorized

    async def is_user_authorized(self) -> bool:
        return self.authorized

    async def iter_dialogs(self):
        for dialog in self.dialogs:
            yield dialog


def make_source_service(tmp_path: Path, client: FakeDialogsClient) -> TelegramSourceService:
    return TelegramSourceService(
        SettingsStore(tmp_path / "config" / "settings.json"), lambda: client
    )


def test_dialog_discovery_filters_private_and_classifies_sources(tmp_path: Path) -> None:
    channel = Channel(123, "Movies", None, datetime.now(), broadcast=True, megagroup=False)
    supergroup = Channel(456, "Downloads", None, datetime.now(), megagroup=True)
    group = Chat(789, "Family", None, 4, datetime.now(), 1)
    private = object()
    dialogs = [
        SimpleNamespace(entity=channel, name="Movies", archived=False),
        SimpleNamespace(entity=supergroup, name="Downloads", archived=False),
        SimpleNamespace(entity=group, name="Family", archived=False),
        SimpleNamespace(entity=private, name="Private", archived=False),
    ]
    service = make_source_service(tmp_path, FakeDialogsClient(dialogs))

    authorized, sources = __import__("asyncio").run(service.list_sources(refresh=True))

    assert authorized is True
    assert {(source["title"], source["type"]) for source in sources} == {
        ("Movies", "channel"),
        ("Downloads", "supergroup"),
        ("Family", "group"),
    }
    assert sources[0]["id"] == utils.get_peer_id(channel)


def test_source_ids_persist_deduplicated_and_reload(tmp_path: Path) -> None:
    channel = Channel(123, "Movies", None, datetime.now(), broadcast=True, megagroup=False)
    service = make_source_service(
        tmp_path,
        FakeDialogsClient(
            [SimpleNamespace(entity=channel, name="Movies", archived=False)]
        ),
    )
    __import__("asyncio").run(service.list_sources(refresh=True))
    source_id = utils.get_peer_id(channel)

    assert service.save_ids([source_id, source_id], service._cached_dicts()) == [source_id]
    assert service.selected_ids() == {source_id}
    assert service.store.load()["telegram_source_ids"] == [source_id]


async def test_unselected_events_are_ignored_without_database_work(tmp_path: Path) -> None:
    database = Database(tmp_path / "db" / "downloads.db")
    await database.initialize()
    queued: list[int] = []
    service = object.__new__(TelegramService)
    service.settings = SimpleNamespace(
        allowed_chat_ids=[],
        include_saved_messages=False,
        max_file_size_gb=0,
        status_replies_enabled=False,
    )
    service.database = database
    service.enqueue = queued.append
    service.source_ids_provider = lambda: {-100123}
    message = SimpleNamespace(
        id=1,
        chat_id=-100999,
        file=SimpleNamespace(name="movie.mkv", size=10, mime_type="video/x-matroska", ext=".mkv"),
        document=None,
        date=None,
    )
    await TelegramService._on_message(service, SimpleNamespace(message=message, chat_id=-100999))

    assert queued == []
    assert await database.list_jobs() == []
    await database.close()


async def test_selected_event_is_queued_using_canonical_id(tmp_path: Path) -> None:
    database = Database(tmp_path / "db" / "downloads.db")
    await database.initialize()
    queued: list[int] = []
    service = object.__new__(TelegramService)
    service.settings = SimpleNamespace(
        allowed_chat_ids=[],
        include_saved_messages=False,
        max_file_size_gb=0,
        status_replies_enabled=False,
    )
    service.database = database

    async def enqueue(job_id: int) -> bool:
        queued.append(job_id)
        return True

    service.enqueue = enqueue
    service.source_ids_provider = lambda: {-100123}
    message = SimpleNamespace(
        id=1,
        chat_id=-100123,
        file=SimpleNamespace(
            name="movie.mkv", size=10, mime_type="video/x-matroska", ext=".mkv"
        ),
        document=SimpleNamespace(id=9, access_hash=10),
        date=None,
    )
    await TelegramService._on_message(service, SimpleNamespace(message=message, chat_id=-100123))

    assert len(queued) == 1
    assert (await database.list_jobs())[0].chat_id == -100123
    await database.close()
