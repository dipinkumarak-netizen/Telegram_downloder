import httpx
import pytest

from app.config import Settings
from app.jellyfin import JellyfinClient


async def test_jellyfin_unavailable_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(*args: object, **kwargs: object) -> None:
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(httpx.AsyncClient, "post", fail)
    config = Settings(
        telegram_api_id=1,
        telegram_api_hash="secret",
        jellyfin_url="http://jellyfin:8096",
        jellyfin_api_key="key",
        jellyfin_refresh_enabled=True,
    )
    assert await JellyfinClient(config).refresh() is False
