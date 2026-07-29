from __future__ import annotations

import pytest

from app.telegram_client import TelegramService


class StubClient:
    def __init__(self, *, connected: bool, authorized: bool) -> None:
        self._connected = connected
        self._authorized = authorized
        self.connect_calls = 0

    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        self.connect_calls += 1
        self._connected = True

    async def is_user_authorized(self) -> bool:
        return self._authorized


async def test_ensure_connected_reconnects_disconnected_client() -> None:
    service = object.__new__(TelegramService)
    service.client = StubClient(connected=False, authorized=True)
    service.connected = False

    await TelegramService.ensure_connected(service)

    assert service.client.connect_calls == 1
    assert service.connected is True


async def test_ensure_connected_fails_for_unauthorized_session() -> None:
    service = object.__new__(TelegramService)
    service.client = StubClient(connected=False, authorized=False)
    service.connected = False

    with pytest.raises(RuntimeError, match="not authorized"):
        await TelegramService.ensure_connected(service)

    assert service.client.connect_calls == 1
    assert service.connected is False
