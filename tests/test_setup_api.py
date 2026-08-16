from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

from app.api.setup_routes import router
from app.config import Settings
from app.services.admin_auth import AdminAuthService
from app.services.settings_store import RuntimeSettings, SettingsStore
from app.services.setup import SetupService


class FakeTelegramAuth:
    async def status(self) -> dict[str, object]:
        return {
            "authorized": True,
            "display_name": "Test User",
            "username": "test_user",
            "phone": "91********10",
        }

    async def cancel(self) -> dict[str, bool]:
        return {"ok": True}


def make_app(tmp_path: Path) -> FastAPI:
    settings = Settings(
        data_dir=tmp_path / "data",
        download_root=tmp_path / "downloads",
        temp_dir=tmp_path / "incomplete",
        _env_file=None,
    )
    store = SettingsStore(tmp_path / "config" / "settings.json")
    runtime = RuntimeSettings(settings, store, environment_fields=set())
    admin = AdminAuthService(store, settings)
    telegram = FakeTelegramAuth()
    setup = SetupService(
        settings, store, runtime, admin, telegram  # type: ignore[arg-type]
    )
    app = FastAPI()
    app.include_router(router)
    app.state.settings = settings
    app.state.admin_auth = admin
    app.state.setup_service = setup
    app.state.telegram_auth = telegram
    app.state.telegram = None
    app.state.queue = SimpleNamespace(active_downloads=0)
    return app


async def test_fresh_setup_shows_create_admin_after_status_load(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/settings")
        status_response = await client.get("/api/setup/status")

    assert page.status_code == 200
    assert status_response.json() == {
        "setup_completed": False,
        "admin_configured": False,
        "authenticated": False,
        "csrf_token": None,
    }
    assert 'id="admin-create" style="display:none"' in page.text
    assert 'id="admin-login" style="display:none"' in page.text
    assert "const createRequired=!state.setup_completed&&!state.admin_configured" in page.text
    assert 'createRequired?"block":"none"' in page.text


async def test_completed_setup_shows_login_only_and_existing_login_succeeds(
    tmp_path: Path,
) -> None:
    app = make_app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    password = "correct-horse-123"
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as setup_client:
        created = await setup_client.post(
            "/api/setup/admin",
            json={
                "username": "administrator",
                "password": password,
                "password_confirmation": password,
            },
        )
    assert created.status_code == 200
    app.state.setup_service.store.set_setup_completed(True)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        page = await client.get("/settings")
        before_login = await client.get("/api/setup/status")
        forbidden = await client.post(
            "/api/setup/admin",
            json={
                "username": "replacement",
                "password": "replacement-password-123",
                "password_confirmation": "replacement-password-123",
            },
        )
        logged_in = await client.post(
            "/api/admin/login",
            json={"username": "administrator", "password": password},
        )
        after_login = await client.get("/api/setup/status")

    assert before_login.json() == {
        "setup_completed": True,
        "admin_configured": True,
        "authenticated": False,
        "csrf_token": None,
    }
    assert 'id="admin-create" style="display:none"' in page.text
    assert 'id="admin-login" style="display:none"' in page.text
    assert 'state.authenticated?"Application Settings":"Administrator Login"' in page.text
    assert (
        'document.getElementById("wizard-actions").style.display=loginRequired?"none":"flex"'
        in page.text
    )
    assert 'if(state.setup_completed)return' in page.text
    assert forbidden.status_code == 403
    assert logged_in.status_code == 200
    assert after_login.json()["setup_completed"] is True
    assert after_login.json()["admin_configured"] is True
    assert after_login.json()["authenticated"] is True
    assert "storage_configured" in after_login.json()
    assert 'data-step="3"' in page.text
    assert 'id="storage-parent"' in page.text
    assert 'id="storage-create"' in page.text
    assert 'step=state.authenticated&&location.pathname==="/settings"?3:1' in page.text


async def test_setup_configuration_requires_session_and_csrf(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rejected = await client.post(
            "/api/setup/telegram-config", json={"api_id": 12345, "api_hash": "fake-hash"}
        )
        created = await client.post(
            "/api/setup/admin",
            json={
                "username": "administrator",
                "password": "correct-horse-123",
                "password_confirmation": "correct-horse-123",
            },
        )
        missing_csrf = await client.post(
            "/api/setup/telegram-config", json={"api_id": 12345, "api_hash": "fake-hash"}
        )
        csrf = created.json()["csrf_token"]
        saved = await client.post(
            "/api/setup/telegram-config",
            json={"api_id": 12345, "api_hash": "fake-hash"},
            headers={"X-CSRF-Token": csrf},
        )

    assert rejected.status_code == 401
    assert created.status_code == 200
    assert missing_csrf.status_code == 403
    assert saved.status_code == 200
    assert "fake-hash" not in saved.text


async def test_setup_completion_locks_admin_creation(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/setup/admin",
            json={
                "username": "administrator",
                "password": "correct-horse-123",
                "password_confirmation": "correct-horse-123",
            },
        )
        csrf = created.json()["csrf_token"]
        headers = {"X-CSRF-Token": csrf}
        await client.post(
            "/api/setup/telegram-config",
            json={"api_id": 12345, "api_hash": "fake-hash"},
            headers=headers,
        )
        await client.post(
            "/api/setup/storage",
            json={
                "download_dir": str(tmp_path / "media"),
                "temp_dir": str(tmp_path / "incomplete"),
            },
            headers=headers,
        )
        completed = await client.post("/api/setup/complete", headers=headers)
        second_admin = await client.post(
            "/api/setup/admin",
            json={
                "username": "other-admin",
                "password": "another-password-123",
                "password_confirmation": "another-password-123",
            },
        )

    assert completed.status_code == 200
    assert second_admin.status_code == 403
