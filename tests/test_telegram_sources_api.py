from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from telethon.tl.types import Channel

from app.api.routes import router
from app.config import Settings
from app.services.admin_auth import AdminAuthService
from app.services.settings_store import SettingsStore
from app.services.telegram_sources import TelegramSourceService


class FakeClient:
    def __init__(self, authorized: bool = True) -> None:
        self.authorized = authorized
        self.channel = Channel(123, "Movies", None, datetime.now(), broadcast=True)

    async def is_user_authorized(self) -> bool:
        return self.authorized

    async def iter_dialogs(self):
        yield SimpleNamespace(entity=self.channel, name="Movies", archived=False)


def make_app(tmp_path: Path, client: FakeClient) -> tuple[FastAPI, str, str]:
    settings = Settings(data_dir=tmp_path / "data", _env_file=None)
    store = SettingsStore(tmp_path / "config" / "settings.json")
    admin = AdminAuthService(store, settings)
    token, csrf = admin.create_admin("admin", "correct-horse-123", "correct-horse-123")
    app = FastAPI()
    app.include_router(router)
    app.state.settings = settings
    app.state.admin_auth = admin
    app.state.telegram_sources = TelegramSourceService(store, lambda: client)
    return app, token, csrf


async def test_sources_api_lists_and_saves_without_secrets(tmp_path: Path) -> None:
    app, token, csrf = make_app(tmp_path, FakeClient())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("tmd_admin_session", token)
        listed = await client.get("/api/telegram/sources?refresh=true")
        source_id = listed.json()["sources"][0]["id"]
        saved = await client.put(
            "/api/settings/telegram-sources",
            json={"source_ids": [source_id, source_id]},
            headers={"X-CSRF-Token": csrf},
        )

    assert listed.status_code == 200
    assert saved.status_code == 200
    assert saved.json()["source_ids"] == [source_id]
    assert saved.json()["sources"][0]["selected"] is True
    assert "access_hash" not in saved.text
    assert "api_hash" not in saved.text


async def test_sources_api_reports_unauthorized_without_login_attempt(tmp_path: Path) -> None:
    app, token, csrf = make_app(tmp_path, FakeClient(authorized=False))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("tmd_admin_session", token)
        response = await client.get("/api/telegram/sources")

    assert response.status_code == 200
    assert response.json() == {"authorized": False, "sources": []}
