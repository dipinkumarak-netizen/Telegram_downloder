from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.services.admin_auth import AdminAuthService
from app.services.settings_store import RuntimeSettings, SettingsStore
from app.services.setup import SetupError, SetupService


class FakeTelegramAuth:
    def __init__(self, authorized: bool = False) -> None:
        self.authorized = authorized

    async def status(self) -> dict[str, object]:
        return {
            "authorized": self.authorized,
            "display_name": "Test User" if self.authorized else None,
            "username": "test_user" if self.authorized else None,
            "phone": "91********10" if self.authorized else None,
        }


def setup_service(
    tmp_path: Path, *, authorized: bool = False, jellyfin_result: bool = True
) -> SetupService:
    settings = Settings(
        data_dir=tmp_path / "data",
        download_root=tmp_path / "downloads",
        temp_dir=tmp_path / "incomplete",
        _env_file=None,
    )
    store = SettingsStore(tmp_path / "config" / "settings.json")
    runtime = RuntimeSettings(settings, store, environment_fields=set())
    admin = AdminAuthService(store, settings)

    async def test_jellyfin(url: str, api_key: str | None) -> bool:
        assert url.startswith("http")
        return jellyfin_result

    return SetupService(
        settings,
        store,
        runtime,
        admin,
        FakeTelegramAuth(authorized),  # type: ignore[arg-type]
        jellyfin_tester=test_jellyfin,
    )


async def test_fresh_and_partially_configured_status(tmp_path: Path) -> None:
    service = setup_service(tmp_path)

    fresh = await service.status()
    service.save_telegram(12345, "fake-api-hash")
    partial = await service.status()

    assert fresh["setup_completed"] is False
    assert fresh["telegram_api_configured"] is False
    assert partial["telegram_api_configured"] is True
    assert partial["setup_completed"] is False


def test_telegram_config_validation_and_secret_masking(tmp_path: Path) -> None:
    service = setup_service(tmp_path)

    with pytest.raises(SetupError, match="positive"):
        service.save_telegram(0, "hash")
    with pytest.raises(SetupError, match="hash is required"):
        service.save_telegram(12345, "")

    response = service.save_telegram(12345, "fake-api-hash")
    assert "fake-api-hash" not in repr(response)


def test_storage_validation(tmp_path: Path) -> None:
    service = setup_service(tmp_path)
    result = service.save_storage(
        str(tmp_path / "media"), str(tmp_path / "media" / "incomplete")
    )
    assert result["ok"] is True
    with pytest.raises(SetupError, match="filesystem root"):
        service.save_storage("/", str(tmp_path / "temp"))
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    with pytest.raises(SetupError, match="not writable"):
        service.save_storage(str(blocker / "child"), str(tmp_path / "temp"))


async def test_jellyfin_disabled_save_masking_and_connection_tests(tmp_path: Path) -> None:
    service = setup_service(tmp_path)
    disabled = service.save_jellyfin(False, None, None)
    enabled = service.save_jellyfin(True, "http://jellyfin:8096", "fake-jellyfin-key")

    assert disabled["enabled"] is False
    assert enabled["api_key_configured"] is True
    assert "fake-jellyfin-key" not in repr(enabled)
    assert await service.test_jellyfin() == {"ok": True}

    failed = setup_service(tmp_path / "failed", jellyfin_result=False)
    assert await failed.test_jellyfin("http://jellyfin:8096", "fake-key") == {"ok": False}


async def test_setup_requires_telegram_authorization_before_completion(tmp_path: Path) -> None:
    service = setup_service(tmp_path)
    service.admin.create_admin("administrator", "correct-horse-123", "correct-horse-123")
    service.save_telegram(12345, "fake-api-hash")
    service.save_storage(str(tmp_path / "media"), str(tmp_path / "incomplete"))

    with pytest.raises(SetupError, match="cannot be completed"):
        await service.complete()


async def test_setup_completes_after_mocked_authorization(tmp_path: Path) -> None:
    service = setup_service(tmp_path, authorized=True)
    service.admin.create_admin("administrator", "correct-horse-123", "correct-horse-123")
    service.save_telegram(12345, "fake-api-hash")
    service.save_storage(str(tmp_path / "media"), str(tmp_path / "incomplete"))

    result = await service.complete()

    assert result == {"ok": True, "setup_completed": True}
    assert service.runtime.setup_completed is True
