from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import Settings
from app.services.admin_auth import AdminAuthError, AdminAuthService
from app.services.settings_store import SettingsStore


def service(tmp_path: Path) -> tuple[AdminAuthService, SettingsStore]:
    store = SettingsStore(tmp_path / "config" / "settings.json")
    settings = Settings(data_dir=tmp_path / "data", _env_file=None)
    return AdminAuthService(store, settings), store


def test_create_admin_hashes_password_and_issues_session(tmp_path: Path) -> None:
    admin, store = service(tmp_path)

    token, csrf = admin.create_admin("administrator", "correct-horse-123", "correct-horse-123")

    stored = json.loads(store.path.read_text())
    password_hash = stored["admin"]["password_hash"]
    assert password_hash.startswith("$argon2")
    assert "correct-horse-123" not in store.path.read_text()
    assert admin.session(token).csrf_token == csrf  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("password", "confirmation", "message"),
    [
        ("short", "short", "at least 12"),
        ("correct-horse-123", "different-value", "does not match"),
    ],
)
def test_invalid_admin_password(
    tmp_path: Path, password: str, confirmation: str, message: str
) -> None:
    admin, _ = service(tmp_path)

    with pytest.raises(AdminAuthError, match=message):
        admin.create_admin("administrator", password, confirmation)


def test_admin_login_success_and_failure(tmp_path: Path) -> None:
    admin, _ = service(tmp_path)
    admin.create_admin("administrator", "correct-horse-123", "correct-horse-123")

    with pytest.raises(AdminAuthError, match="Invalid"):
        admin.login("administrator", "wrong-password", "test-client")

    token, _ = admin.login("administrator", "correct-horse-123", "test-client")
    assert admin.session(token) is not None
