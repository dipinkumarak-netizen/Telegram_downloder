from __future__ import annotations

import json
from pathlib import Path

from app.config import Settings
from app.services.settings_store import RuntimeSettings, SettingsStore


def fake_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "data_dir": tmp_path / "data",
        "download_root": tmp_path / "defaults" / "downloads",
        "temp_dir": tmp_path / "defaults" / "incomplete",
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def test_store_writes_atomically_with_private_permissions(tmp_path: Path) -> None:
    path = tmp_path / "config" / "settings.json"
    store = SettingsStore(path)

    store.update("telegram", {"api_id": 12345, "api_hash": "fake-hash"})

    assert path.exists()
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text())["telegram"]["api_id"] == 12345
    assert not list(path.parent.glob(f".{path.name}.*"))


def test_persisted_settings_apply_over_defaults(tmp_path: Path) -> None:
    settings = fake_settings(tmp_path)
    store = SettingsStore(tmp_path / "config" / "settings.json")
    store.update(
        "storage",
        {
            "download_dir": str(tmp_path / "web-downloads"),
            "temp_dir": str(tmp_path / "web-temp"),
        },
    )
    runtime = RuntimeSettings(settings, store, environment_fields=set())

    runtime.apply_persisted()

    assert settings.download_root == tmp_path / "web-downloads"
    assert settings.temp_dir == tmp_path / "web-temp"


def test_explicit_environment_precedes_persisted_settings(
    monkeypatch, tmp_path: Path
) -> None:
    environment_downloads = tmp_path / "environment-downloads"
    monkeypatch.setenv("TMD_DOWNLOAD_DIR", str(environment_downloads))
    settings = Settings(data_dir=tmp_path / "data", _env_file=None)
    store = SettingsStore(tmp_path / "config" / "settings.json")
    store.update("storage", {"download_dir": str(tmp_path / "web-downloads")})
    runtime = RuntimeSettings(settings, store)

    runtime.apply_persisted()

    assert settings.download_root == environment_downloads
    assert "download_root" in runtime.environment_fields


def test_derived_paths_are_not_mistaken_for_environment_overrides(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", _env_file=None)
    runtime = RuntimeSettings(
        settings, SettingsStore(tmp_path / "config" / "settings.json")
    )

    assert "data_dir" in runtime.environment_fields
    assert "database_path" not in runtime.environment_fields
    assert "session_dir" not in runtime.environment_fields
    assert "temp_dir" not in runtime.environment_fields


def test_setup_completion_is_explicit(tmp_path: Path) -> None:
    settings = fake_settings(tmp_path)
    store = SettingsStore(tmp_path / "config" / "settings.json")
    runtime = RuntimeSettings(settings, store)

    assert runtime.setup_completed is False
    store.update("telegram", {"api_id": 12345})
    assert runtime.setup_completed is False
    store.set_setup_completed(True)
    assert runtime.setup_completed is True


def test_environment_configured_deployment_is_treated_as_complete(tmp_path: Path) -> None:
    settings = fake_settings(
        tmp_path,
        telegram_api_id=12345,
        telegram_api_hash="fake-hash",
        dashboard_username="admin",
        dashboard_password="fake-dashboard-password",
    )
    runtime = RuntimeSettings(
        settings, SettingsStore(tmp_path / "config" / "settings.json")
    )

    assert runtime.legacy_environment_configured is True
    assert runtime.setup_completed is True
