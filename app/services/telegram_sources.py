from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from telethon import utils
from telethon.tl.types import Channel, Chat

from app.services.settings_store import SettingsStore, SettingsStoreError


@dataclass(slots=True, frozen=True)
class TelegramSource:
    id: int
    title: str
    type: str
    selected: bool
    available: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "type": self.type,
            "selected": self.selected,
            "available": self.available,
        }


ClientProvider = Callable[[], Any | None]


def normalize_source_id(value: int | str) -> int:
    """Return Telethon's signed peer ID representation."""

    return int(value)


def source_type(entity: object) -> str | None:
    if isinstance(entity, Channel):
        return "supergroup" if entity.megagroup else "channel"
    if isinstance(entity, Chat):
        return "group"
    return None


class TelegramSourceService:
    def __init__(self, store: SettingsStore, client_provider: ClientProvider):
        self.store = store
        self.client_provider = client_provider
        self._cache: list[TelegramSource] = []
        self._cache_lock = asyncio.Lock()

    def selected_ids(self) -> set[int]:
        data = self.store.load()
        values = data.get("telegram_source_ids", [])
        if not isinstance(values, list):
            return set()
        try:
            return {normalize_source_id(value) for value in values}
        except (TypeError, ValueError):
            return set()

    def has_web_selection(self) -> bool:
        return bool(self.selected_ids())

    def effective_ids(self, legacy_ids: list[int]) -> set[int]:
        selected = self.selected_ids()
        return (
            selected
            if selected or not legacy_ids
            else {normalize_source_id(i) for i in legacy_ids}
        )

    async def list_sources(self, *, refresh: bool = False) -> tuple[bool, list[dict[str, object]]]:
        client = self.client_provider()
        if client is None or not await client.is_user_authorized():
            return False, self._cached_dicts()
        if refresh or not self._cache:
            async with self._cache_lock:
                if refresh or not self._cache:
                    self._cache = [source async for source in self._discover(client)]
        selected = self.selected_ids()
        known = {source.id for source in self._cache}
        cached = [source for source in self._cache]
        for source_id in sorted(selected - known):
            cached.append(
                TelegramSource(source_id, "Unavailable source", "unknown", True, False)
            )
        return True, [
            TelegramSource(s.id, s.title, s.type, s.id in selected, s.available).as_dict()
            for s in cached
        ]

    async def _discover(self, client: Any) -> AsyncIterator[TelegramSource]:
        async for dialog in client.iter_dialogs():
            entity = getattr(dialog, "entity", None)
            kind = source_type(entity)
            if kind is None or getattr(dialog, "archived", False):
                continue
            try:
                peer_id = normalize_source_id(utils.get_peer_id(entity))
            except (TypeError, ValueError):
                continue
            title = str(getattr(dialog, "name", None) or getattr(entity, "title", None) or peer_id)
            yield TelegramSource(peer_id, title, kind, False)

    def save_ids(self, ids: list[int], available: list[dict[str, object]]) -> list[int]:
        normalized = sorted({normalize_source_id(value) for value in ids})
        accessible = {
            normalize_source_id(item["id"])
            for item in available
            if item.get("available", True)
            and item.get("type") in {"channel", "supergroup", "group"}
        }
        # Previously selected IDs remain retainable when Telegram temporarily hides them;
        # the UI marks those entries unavailable so the administrator can remove them.
        accessible.update(self.selected_ids())
        if not set(normalized) <= accessible:
            raise ValueError("One or more selected Telegram sources are not accessible.")
        metadata = [
            {
                "id": normalize_source_id(item["id"]),
                "title": str(item["title"]),
                "type": str(item["type"]),
            }
            for item in available
            if normalize_source_id(item["id"]) in normalized
        ]
        try:
            self.store.update_root(
                {"telegram_source_ids": normalized, "telegram_sources": metadata}
            )
        except SettingsStoreError as exc:
            raise RuntimeError("Telegram sources could not be saved.") from exc
        return normalized

    def _cached_dicts(self) -> list[dict[str, object]]:
        selected = self.selected_ids()
        return [
            TelegramSource(s.id, s.title, s.type, s.id in selected, s.available).as_dict()
            for s in self._cache
        ]
