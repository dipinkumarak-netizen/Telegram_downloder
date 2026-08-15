from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    RPCError,
    SessionPasswordNeededError,
)

from app.config import Settings

logger = logging.getLogger(__name__)


class AuthClient(Protocol):
    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def is_user_authorized(self) -> bool: ...

    async def get_me(self) -> Any: ...

    async def send_code_request(self, phone: str) -> Any: ...

    async def sign_in(self, *args: Any, **kwargs: Any) -> Any: ...


ClientFactory = Callable[[], AuthClient]
AuthorizedCallback = Callable[[], Awaitable[None]]
ActiveClientProvider = Callable[[], AuthClient | None]


class TelegramAuthError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


@dataclass(slots=True)
class PendingAuth:
    phone: str
    phone_code_hash: str
    expires_at: float
    password_required: bool = False


def mask_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    prefix = "+" if phone.startswith("+") else ""
    digits = phone.removeprefix("+")
    if len(digits) <= 4:
        return f"{prefix}{'*' * len(digits)}"
    return f"{prefix}{digits[:2]}{'*' * (len(digits) - 4)}{digits[-2:]}"


class TelegramAuthService:
    """Single-administrator, in-memory Telegram login flow.

    OTPs and 2FA passwords exist only as request-local values. Telegram's phone-code hash
    remains server-side and the pending flow expires automatically.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: ClientFactory | None = None,
        on_authorized: AuthorizedCallback | None = None,
        active_client_provider: ActiveClientProvider | None = None,
        pending_ttl_seconds: int = 600,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._client_factory = client_factory or self._default_client
        self._on_authorized = on_authorized
        self._active_client_provider = active_client_provider
        self._pending_ttl_seconds = pending_ttl_seconds
        self._clock = clock
        self._lock = asyncio.Lock()
        self._pending: PendingAuth | None = None
        self._client: AuthClient | None = None

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.telegram_api_id
            and self.settings.telegram_api_hash
            and self.settings.telegram_api_hash.get_secret_value().strip()
        )

    def _default_client(self) -> AuthClient:
        if not self.configured:
            raise TelegramAuthError(
                "credentials_missing", "Telegram API credentials are not configured.", 503
            )
        return TelegramClient(
            str(self.settings.telegram_session_path),
            self.settings.telegram_api_id,
            self.settings.telegram_api_hash.get_secret_value(),
        )

    async def status(self) -> dict[str, object]:
        async with self._lock:
            await self._expire_pending()
            if not self.configured:
                return self._status_payload(connected=False, authorized=False)
            active = self._active_client_provider() if self._active_client_provider else None
            if active is not None:
                try:
                    authorized = await active.is_user_authorized()
                    account = await active.get_me() if authorized else None
                    return self._status_payload(
                        connected=True, authorized=authorized, account=account
                    )
                except (OSError, RPCError):
                    return self._status_payload(connected=False, authorized=False)
            client = self._client or self._client_factory()
            temporary = client is not self._client
            try:
                await client.connect()
                authorized = await client.is_user_authorized()
                account = await client.get_me() if authorized else None
                return self._status_payload(
                    connected=True, authorized=authorized, account=account
                )
            except (OSError, RPCError):
                return self._status_payload(connected=False, authorized=False)
            finally:
                if temporary:
                    await self._safe_disconnect(client)

    async def send_code(self, phone: str) -> dict[str, object]:
        normalized = self._normalize_phone(phone)
        async with self._lock:
            await self._expire_pending()
            if not self.configured:
                raise TelegramAuthError(
                    "credentials_missing", "Telegram API credentials are not configured.", 503
                )
            if self._pending is not None:
                raise TelegramAuthError(
                    "login_in_progress",
                    "A Telegram login is already in progress. Cancel it before starting again.",
                    409,
                )
            client = self._client_factory()
            try:
                await client.connect()
                if await client.is_user_authorized():
                    await self._safe_disconnect(client)
                    raise TelegramAuthError(
                        "already_authorized", "The Telegram session is already authorized.", 409
                    )
                sent = await client.send_code_request(normalized)
            except PhoneNumberInvalidError as exc:
                await self._safe_disconnect(client)
                raise TelegramAuthError(
                    "invalid_phone", "The Telegram phone number is invalid."
                ) from exc
            except FloodWaitError as exc:
                await self._safe_disconnect(client)
                raise TelegramAuthError(
                    "rate_limited",
                    f"Telegram rate-limited this request. Try again in {exc.seconds} seconds.",
                    429,
                ) from exc
            except TelegramAuthError:
                raise
            except (OSError, RPCError) as exc:
                await self._safe_disconnect(client)
                raise TelegramAuthError(
                    "telegram_unavailable",
                    "Telegram is currently unavailable. Try again later.",
                    503,
                ) from exc
            self._client = client
            self._pending = PendingAuth(
                phone=normalized,
                phone_code_hash=sent.phone_code_hash,
                expires_at=self._clock() + self._pending_ttl_seconds,
            )
            return {"ok": True, "code_sent": True, "password_required": False}

    async def verify_code(self, code: str) -> dict[str, object]:
        normalized = "".join(code.split())
        if not normalized:
            raise TelegramAuthError("invalid_code", "Enter the Telegram login code.")
        async with self._lock:
            pending, client = await self._require_pending()
            if pending.password_required:
                raise TelegramAuthError(
                    "password_required", "Telegram two-step verification password is required.", 409
                )
            try:
                await client.sign_in(
                    pending.phone, normalized, phone_code_hash=pending.phone_code_hash
                )
            except SessionPasswordNeededError:
                pending.password_required = True
                return {"ok": True, "authorized": False, "password_required": True}
            except PhoneCodeInvalidError as exc:
                raise TelegramAuthError(
                    "invalid_code", "The Telegram login code is invalid."
                ) from exc
            except PhoneCodeExpiredError as exc:
                await self._clear_pending()
                raise TelegramAuthError(
                    "code_expired", "The Telegram login code expired. Request a new code."
                ) from exc
            except FloodWaitError as exc:
                raise TelegramAuthError(
                    "rate_limited",
                    f"Telegram rate-limited this request. Try again in {exc.seconds} seconds.",
                    429,
                ) from exc
            except (OSError, RPCError) as exc:
                raise TelegramAuthError(
                    "telegram_unavailable",
                    "Telegram is currently unavailable. Try again later.",
                    503,
                ) from exc
            return await self._complete_authorization(client)

    async def verify_password(self, password: str) -> dict[str, object]:
        if not password:
            raise TelegramAuthError("invalid_password", "Enter the Telegram 2FA password.")
        async with self._lock:
            pending, client = await self._require_pending()
            if not pending.password_required:
                raise TelegramAuthError(
                    "password_not_required", "This login is not waiting for a 2FA password.", 409
                )
            try:
                await client.sign_in(password=password)
            except PasswordHashInvalidError as exc:
                raise TelegramAuthError(
                    "invalid_password", "The Telegram 2FA password is incorrect."
                ) from exc
            except FloodWaitError as exc:
                raise TelegramAuthError(
                    "rate_limited",
                    f"Telegram rate-limited this request. Try again in {exc.seconds} seconds.",
                    429,
                ) from exc
            except (OSError, RPCError) as exc:
                raise TelegramAuthError(
                    "telegram_unavailable",
                    "Telegram is currently unavailable. Try again later.",
                    503,
                ) from exc
            return await self._complete_authorization(client)

    async def cancel(self) -> dict[str, bool]:
        async with self._lock:
            await self._clear_pending()
            return {"ok": True}

    async def close(self) -> None:
        async with self._lock:
            await self._clear_pending()

    async def _complete_authorization(self, client: AuthClient) -> dict[str, object]:
        if not await client.is_user_authorized():
            raise TelegramAuthError(
                "authorization_failed", "Telegram did not authorize the session."
            )
        account = await client.get_me()
        payload = self._status_payload(connected=True, authorized=True, account=account)
        await self._clear_pending()
        if self._on_authorized:
            try:
                await self._on_authorized()
            except Exception as exc:
                logger.warning("Telegram authorized, but the listener could not start: %s", exc)
        return {"ok": True, "password_required": False, **payload}

    async def _require_pending(self) -> tuple[PendingAuth, AuthClient]:
        if await self._expire_pending():
            raise TelegramAuthError(
                "code_expired", "The Telegram login request expired. Request a new code."
            )
        if self._pending is None or self._client is None:
            raise TelegramAuthError(
                "no_pending_login", "No Telegram login request is pending.", 409
            )
        return self._pending, self._client

    async def _expire_pending(self) -> bool:
        if self._pending and self._pending.expires_at <= self._clock():
            await self._clear_pending()
            return True
        return False

    async def _clear_pending(self) -> None:
        client, self._client = self._client, None
        self._pending = None
        if client is not None:
            await self._safe_disconnect(client)

    @staticmethod
    async def _safe_disconnect(client: AuthClient) -> None:
        try:
            result = client.disconnect()
            if inspect.isawaitable(result):
                await result
        except (OSError, RPCError):
            pass

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        normalized = phone.strip().replace(" ", "").replace("-", "")
        if not normalized or not normalized.startswith("+") or not normalized[1:].isdigit():
            raise TelegramAuthError(
                "invalid_phone", "Enter a valid phone number in international format."
            )
        return normalized

    def _status_payload(
        self, *, connected: bool, authorized: bool, account: Any | None = None
    ) -> dict[str, object]:
        first = getattr(account, "first_name", None) if account else None
        last = getattr(account, "last_name", None) if account else None
        display_name = " ".join(part for part in (first, last) if part) or None
        step = None
        if self._pending:
            step = "password" if self._pending.password_required else "code"
        return {
            "configured": self.configured,
            "connected": connected,
            "authorized": authorized,
            "login_needed": self.configured and not authorized,
            "pending_step": step,
            "display_name": display_name,
            "username": getattr(account, "username", None) if account else None,
            "phone": mask_phone(getattr(account, "phone", None) if account else None),
        }
