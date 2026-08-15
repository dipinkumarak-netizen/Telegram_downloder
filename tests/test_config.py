from __future__ import annotations

from pathlib import Path

from app.config import Settings

FAKE_CREDENTIALS = {
    "telegram_api_id": 12345,
    "telegram_api_hash": "not-a-real-api-hash",
    "_env_file": None,
}


def test_portable_default_paths() -> None:
    settings = Settings(**FAKE_CREDENTIALS)

    assert settings.data_dir == Path("/data")
    assert settings.database_path == Path("/data/db/downloads.db")
    assert settings.session_dir == Path("/data/session")
    assert settings.telegram_session_path == Path("/data/session/downloader")
    assert settings.config_dir == Path("/data/config")
    assert settings.log_dir == Path("/data/logs")
    assert settings.download_root == Path("/downloads")
    assert settings.temp_dir == Path("/downloads/incomplete")


def test_new_environment_overrides_and_derived_paths(monkeypatch) -> None:
    monkeypatch.setenv("TMD_DATA_DIR", "/tmp/tmd-state")
    monkeypatch.setenv("TMD_SESSION_NAME", "portable-account.session")
    monkeypatch.setenv("TMD_DOWNLOAD_DIR", "/tmp/tmd-media")
    monkeypatch.setenv("TMD_WEB_HOST", "127.0.0.1")
    monkeypatch.setenv("TMD_WEB_PORT", "9999")

    settings = Settings(**FAKE_CREDENTIALS)

    assert settings.database_path == Path("/tmp/tmd-state/db/downloads.db")
    assert settings.telegram_session_path == Path("/tmp/tmd-state/session/portable-account")
    assert settings.download_root == Path("/tmp/tmd-media")
    assert settings.temp_dir == Path("/tmp/tmd-media/incomplete")
    assert settings.dashboard_host == "127.0.0.1"
    assert settings.dashboard_port == 9999


def test_specific_path_overrides_take_precedence(monkeypatch, tmp_path: Path) -> None:
    database = tmp_path / "custom.sqlite"
    session = tmp_path / "legacy" / "account"
    temporary = tmp_path / "partial"
    monkeypatch.setenv("TMD_DATABASE_PATH", str(database))
    monkeypatch.setenv("TELEGRAM_SESSION_PATH", str(session))
    monkeypatch.setenv("TMD_TEMP_DIR", str(temporary))

    settings = Settings(**FAKE_CREDENTIALS)

    assert settings.database_path == database
    assert settings.telegram_session_path == session
    assert settings.temp_dir == temporary


def test_legacy_environment_names_remain_supported(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_PATH", "/legacy/database/downloads.db")
    monkeypatch.setenv("LOG_DIR", "/legacy/logs")
    monkeypatch.setenv("DOWNLOAD_ROOT", "/legacy/downloads")
    monkeypatch.setenv("DASHBOARD_PORT", "8989")

    settings = Settings(**FAKE_CREDENTIALS)

    assert settings.database_path == Path("/legacy/database/downloads.db")
    assert settings.log_dir == Path("/legacy/logs")
    assert settings.download_root == Path("/legacy/downloads")
    assert settings.temp_dir == Path("/legacy/downloads/incomplete")
    assert settings.dashboard_port == 8989


def test_defaults_contain_no_production_secrets() -> None:
    settings = Settings(**FAKE_CREDENTIALS)

    assert settings.telegram_phone is None
    assert settings.jellyfin_api_key is None
    assert settings.dashboard_password is None
    assert "dipin" not in repr(settings).lower()
    assert "/storage" not in repr(settings)


def test_missing_telegram_credentials_can_load_web_configuration() -> None:
    settings = Settings(_env_file=None)

    assert settings.telegram_api_id is None
    assert settings.telegram_api_hash is None
