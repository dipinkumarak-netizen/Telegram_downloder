from __future__ import annotations

from pathlib import Path

import httpx
from fastapi import FastAPI

from app.api.routes import router
from app.config import Settings
from app.services.admin_auth import AdminAuthService
from app.services.settings_store import SettingsStore


class StubAuthService:
    async def status(self) -> dict[str, object]:
        return {
            "configured": True,
            "connected": False,
            "authorized": False,
            "login_needed": True,
            "pending_step": None,
            "display_name": None,
            "username": None,
            "phone": None,
        }

    async def send_code(self, phone: str) -> dict[str, object]:
        assert phone == "+919876543210"
        return {"ok": True, "code_sent": True, "password_required": False}

    async def verify_code(self, code: str) -> dict[str, object]:
        assert code == "12345"
        return {"ok": True, "authorized": True, "password_required": False}

    async def verify_password(self, password: str) -> dict[str, object]:
        assert password == "fake-password"
        return {"ok": True, "authorized": True, "password_required": False}

    async def cancel(self) -> dict[str, bool]:
        return {"ok": True}


def make_app(tmp_path: Path, *, protected: bool = True) -> FastAPI:
    values = {
        "telegram_api_id": 12345,
        "telegram_api_hash": "fake-api-hash",
        "data_dir": tmp_path / "data",
        "download_root": tmp_path / "downloads",
        "_env_file": None,
    }
    if protected:
        values.update(dashboard_username="admin", dashboard_password="fake-dashboard-password")
    app = FastAPI()
    app.include_router(router)
    app.state.settings = Settings(**values)
    app.state.admin_auth = AdminAuthService(
        SettingsStore(tmp_path / "config" / "settings.json"), app.state.settings
    )
    app.state.telegram_auth = StubAuthService()
    return app


async def test_telegram_auth_api_requires_dashboard_authentication(tmp_path: Path) -> None:
    app = make_app(tmp_path, protected=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/telegram/status")

    assert response.status_code == 401


async def test_telegram_auth_api_flow_does_not_echo_secrets(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    auth = ("admin", "fake-dashboard-password")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", auth=auth
    ) as client:
        status = await client.get("/api/telegram/status")
        sent = await client.post(
            "/api/telegram/auth/send-code", json={"phone": "+919876543210"}
        )
        verified = await client.post(
            "/api/telegram/auth/verify-code", json={"code": "12345"}
        )
        password = await client.post(
            "/api/telegram/auth/verify-password", json={"password": "fake-password"}
        )
        cancelled = await client.post("/api/telegram/auth/cancel")

    assert status.status_code == 200
    assert sent.status_code == verified.status_code == password.status_code == 200
    assert cancelled.status_code == 200
    serialized = " ".join(
        response.text for response in (status, sent, verified, password, cancelled)
    )
    assert "fake-api-hash" not in serialized
    assert "12345" not in serialized
    assert "fake-password" not in serialized
    assert "phone_code_hash" not in serialized
