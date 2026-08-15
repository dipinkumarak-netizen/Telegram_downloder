from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon.errors import (
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)

from app.config import Settings
from app.services.telegram_auth import TelegramAuthError, TelegramAuthService


@dataclass
class FakeAccount:
    first_name: str = "Test"
    last_name: str = "User"
    username: str = "test_user"
    phone: str = "919876543210"


class FakeClient:
    def __init__(self, *, authorized: bool = False) -> None:
        self.authorized = authorized
        self.connected = False
        self.disconnected = False
        self.send_error: Exception | None = None
        self.code_error: Exception | None = None
        self.password_error: Exception | None = None
        self.sent_phone: str | None = None
        self.codes: list[str] = []
        self.passwords: list[str] = []
        self.account = FakeAccount()

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False
        self.disconnected = True

    async def is_user_authorized(self) -> bool:
        return self.authorized

    async def get_me(self) -> FakeAccount:
        return self.account

    async def send_code_request(self, phone: str):
        if self.send_error:
            raise self.send_error
        self.sent_phone = phone
        return SimpleNamespace(phone_code_hash="server-only-hash")

    async def sign_in(self, phone=None, code=None, phone_code_hash=None, password=None):
        if password is not None:
            self.passwords.append(password)
            if self.password_error:
                raise self.password_error
        else:
            self.codes.append(code)
            assert phone_code_hash == "server-only-hash"
            if self.code_error:
                raise self.code_error
        self.authorized = True
        return self.account


def settings(tmp_path: Path, *, configured: bool = True) -> Settings:
    values = {
        "telegram_session_path": tmp_path / "session" / "test-account",
        "database_path": tmp_path / "db" / "test.db",
        "log_dir": tmp_path / "logs",
        "download_root": tmp_path / "downloads",
        "_env_file": None,
    }
    if configured:
        values.update(telegram_api_id=12345, telegram_api_hash="fake-api-hash")
    return Settings(**values)


async def test_status_when_not_authorized(tmp_path: Path) -> None:
    client = FakeClient()
    service = TelegramAuthService(settings(tmp_path), client_factory=lambda: client)

    result = await service.status()

    assert result["configured"] is True
    assert result["authorized"] is False
    assert result["login_needed"] is True
    assert client.disconnected is True


async def test_status_when_authorized_masks_account_phone(tmp_path: Path) -> None:
    service = TelegramAuthService(
        settings(tmp_path), client_factory=lambda: FakeClient(authorized=True)
    )

    result = await service.status()

    assert result["authorized"] is True
    assert result["display_name"] == "Test User"
    assert result["username"] == "test_user"
    assert result["phone"] == "91********10"


async def test_send_code_success_keeps_internal_hash_server_side(tmp_path: Path) -> None:
    client = FakeClient()
    service = TelegramAuthService(settings(tmp_path), client_factory=lambda: client)

    result = await service.send_code(" +91 98765-43210 ")

    assert result == {"ok": True, "code_sent": True, "password_required": False}
    assert client.sent_phone == "+919876543210"
    assert "hash" not in repr(result).lower()


async def test_missing_api_credentials(tmp_path: Path) -> None:
    service = TelegramAuthService(settings(tmp_path, configured=False))

    status = await service.status()
    assert status["configured"] is False
    with pytest.raises(TelegramAuthError, match="credentials are not configured") as error:
        await service.send_code("+919876543210")
    assert error.value.code == "credentials_missing"


@pytest.mark.parametrize(
    ("phone", "client_error"),
    [("not-a-phone", None), ("+919876543210", PhoneNumberInvalidError(None))],
)
async def test_invalid_phone_handling(
    tmp_path: Path, phone: str, client_error: Exception | None
) -> None:
    client = FakeClient()
    client.send_error = client_error
    service = TelegramAuthService(settings(tmp_path), client_factory=lambda: client)

    with pytest.raises(TelegramAuthError) as error:
        await service.send_code(phone)

    assert error.value.code == "invalid_phone"


async def test_verify_code_success_clears_pending_state(tmp_path: Path) -> None:
    client = FakeClient()
    callbacks = 0

    async def authorized() -> None:
        nonlocal callbacks
        callbacks += 1

    service = TelegramAuthService(
        settings(tmp_path), client_factory=lambda: client, on_authorized=authorized
    )
    await service.send_code("+919876543210")

    result = await service.verify_code(" 12 345 ")

    assert result["authorized"] is True
    assert client.codes == ["12345"]
    assert client.disconnected is True
    assert callbacks == 1
    with pytest.raises(TelegramAuthError) as error:
        await service.verify_code("12345")
    assert error.value.code == "no_pending_login"


async def test_verify_code_requires_two_factor_password(tmp_path: Path) -> None:
    client = FakeClient()
    client.code_error = SessionPasswordNeededError(None)
    service = TelegramAuthService(settings(tmp_path), client_factory=lambda: client)
    await service.send_code("+919876543210")

    result = await service.verify_code("12345")

    assert result == {"ok": True, "authorized": False, "password_required": True}
    assert (await service.status())["pending_step"] == "password"


@pytest.mark.parametrize(
    ("exception", "expected_code"),
    [
        (PhoneCodeInvalidError(None), "invalid_code"),
        (PhoneCodeExpiredError(None), "code_expired"),
    ],
)
async def test_invalid_or_expired_code(
    tmp_path: Path, exception: Exception, expected_code: str
) -> None:
    client = FakeClient()
    client.code_error = exception
    service = TelegramAuthService(settings(tmp_path), client_factory=lambda: client)
    await service.send_code("+919876543210")

    with pytest.raises(TelegramAuthError) as error:
        await service.verify_code("12345")

    assert error.value.code == expected_code


async def test_verify_password_success_does_not_retain_password(tmp_path: Path) -> None:
    client = FakeClient()
    client.code_error = SessionPasswordNeededError(None)
    service = TelegramAuthService(settings(tmp_path), client_factory=lambda: client)
    await service.send_code("+919876543210")
    await service.verify_code("12345")
    client.code_error = None

    result = await service.verify_password("temporary-secret")

    assert result["authorized"] is True
    assert client.passwords == ["temporary-secret"]
    assert "temporary-secret" not in repr(service)
    assert client.disconnected is True


async def test_incorrect_two_factor_password_keeps_pending_flow(tmp_path: Path) -> None:
    client = FakeClient()
    client.code_error = SessionPasswordNeededError(None)
    service = TelegramAuthService(settings(tmp_path), client_factory=lambda: client)
    await service.send_code("+919876543210")
    await service.verify_code("12345")
    client.password_error = PasswordHashInvalidError(None)

    with pytest.raises(TelegramAuthError) as error:
        await service.verify_password("wrong-password")

    assert error.value.code == "invalid_password"
    assert (await service.status())["pending_step"] == "password"


async def test_no_pending_authentication_flow(tmp_path: Path) -> None:
    service = TelegramAuthService(settings(tmp_path), client_factory=FakeClient)

    with pytest.raises(TelegramAuthError) as error:
        await service.verify_code("12345")

    assert error.value.code == "no_pending_login"


async def test_pending_flow_expires(tmp_path: Path) -> None:
    now = 100.0
    client = FakeClient()
    service = TelegramAuthService(
        settings(tmp_path),
        client_factory=lambda: client,
        pending_ttl_seconds=10,
        clock=lambda: now,
    )
    await service.send_code("+919876543210")
    now = 111.0

    with pytest.raises(TelegramAuthError) as error:
        await service.verify_code("12345")

    assert error.value.code == "code_expired"
    assert client.disconnected is True


async def test_payloads_never_return_secrets(tmp_path: Path) -> None:
    client = FakeClient(authorized=True)
    service = TelegramAuthService(settings(tmp_path), client_factory=lambda: client)

    payload = await service.status()
    serialized = repr(payload)

    assert "fake-api-hash" not in serialized
    assert "server-only-hash" not in serialized
    assert "919876543210" not in serialized
    assert "telegram_session_path" not in serialized
