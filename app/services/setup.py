from __future__ import annotations

import os
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import SecretStr

from app.config import Settings
from app.services.admin_auth import AdminAuthService
from app.services.settings_store import RuntimeSettings, SettingsStore, SettingsStoreError
from app.services.telegram_auth import TelegramAuthService

JellyfinTester = Callable[[str, str | None], Awaitable[bool]]


class SetupError(ValueError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class SetupService:
    def __init__(
        self,
        settings: Settings,
        store: SettingsStore,
        runtime: RuntimeSettings,
        admin: AdminAuthService,
        telegram_auth: TelegramAuthService,
        *,
        jellyfin_tester: JellyfinTester | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.runtime = runtime
        self.admin = admin
        self.telegram_auth = telegram_auth
        self._jellyfin_tester = jellyfin_tester or self._test_jellyfin

    async def status(self) -> dict[str, Any]:
        data = self.store.load()
        telegram = data.get("telegram", {})
        storage = data.get("storage", {})
        auth = await self.telegram_auth.status()
        return {
            "setup_completed": self.runtime.setup_completed,
            "legacy_environment_configured": self.runtime.legacy_environment_configured,
            "admin_configured": self.admin.configured
            or bool(self.settings.dashboard_username and self.settings.dashboard_password),
            "telegram_api_configured": bool(
                self.settings.telegram_api_id and self.settings.telegram_api_hash
            ),
            "telegram_api_id": self.settings.telegram_api_id,
            "telegram_api_hash_configured": bool(
                telegram.get("api_hash") or self.settings.telegram_api_hash
            ),
            "telegram_authorized": bool(auth["authorized"]),
            "telegram_account": {
                "display_name": auth.get("display_name"),
                "username": auth.get("username"),
                "phone": auth.get("phone"),
            },
            "download_dir": str(storage.get("download_dir") or self.settings.download_root),
            "temp_dir": str(storage.get("temp_dir") or self.settings.temp_dir),
            "storage_configured": bool(
                storage.get("download_dir") and storage.get("temp_dir")
            )
            or self.runtime.legacy_environment_configured,
            "jellyfin": {
                "enabled": self.settings.jellyfin_refresh_enabled,
                "url": self.settings.jellyfin_url,
                "api_key_configured": bool(self.settings.jellyfin_api_key),
            },
        }

    def save_telegram(self, api_id: int, api_hash: str) -> dict[str, Any]:
        if api_id <= 0:
            raise SetupError("Telegram API ID must be a positive integer.")
        api_hash = api_hash.strip()
        existing = self.store.load().get("telegram", {})
        if not api_hash and not (existing.get("api_hash") or self.settings.telegram_api_hash):
            raise SetupError("Telegram API hash is required.")
        values: dict[str, Any] = {"api_id": api_id}
        if api_hash:
            values["api_hash"] = api_hash
        self._store_update("telegram", values)
        if "telegram_api_id" not in self.runtime.environment_fields:
            self.settings.telegram_api_id = api_id
        if api_hash and "telegram_api_hash" not in self.runtime.environment_fields:
            self.settings.telegram_api_hash = SecretStr(api_hash)
        return {
            "ok": True,
            "configured": True,
            "environment_override": bool(
                {"telegram_api_id", "telegram_api_hash"} & self.runtime.environment_fields
            ),
        }

    def save_storage(self, download_dir: str, temp_dir: str) -> dict[str, Any]:
        download = self._validate_directory(download_dir, "Download directory")
        temporary = self._validate_directory(temp_dir, "Temporary directory")
        self._store_update(
            "storage", {"download_dir": str(download), "temp_dir": str(temporary)}
        )
        if "download_root" not in self.runtime.environment_fields:
            self.settings.download_root = download
        if "temp_dir" not in self.runtime.environment_fields:
            self.settings.temp_dir = temporary
        return {
            "ok": True,
            "download_dir": str(self.settings.download_root),
            "temp_dir": str(self.settings.temp_dir),
            "environment_override": bool(
                {"download_root", "temp_dir"} & self.runtime.environment_fields
            ),
        }

    def save_jellyfin(
        self, enabled: bool, url: str | None, api_key: str | None
    ) -> dict[str, Any]:
        normalized_url = (url or "").strip().rstrip("/")
        if enabled:
            self._validate_url(normalized_url)
        existing = self.store.load().get("jellyfin", {})
        values: dict[str, Any] = {"enabled": enabled, "url": normalized_url or None}
        if api_key:
            values["api_key"] = api_key
        elif enabled and not (existing.get("api_key") or self.settings.jellyfin_api_key):
            raise SetupError("Jellyfin API key is required when integration is enabled.")
        self._store_update("jellyfin", values)
        if "jellyfin_refresh_enabled" not in self.runtime.environment_fields:
            self.settings.jellyfin_refresh_enabled = enabled
        if "jellyfin_url" not in self.runtime.environment_fields:
            self.settings.jellyfin_url = normalized_url or None
        if api_key and "jellyfin_api_key" not in self.runtime.environment_fields:
            self.settings.jellyfin_api_key = SecretStr(api_key)
        return {
            "ok": True,
            "enabled": self.settings.jellyfin_refresh_enabled,
            "url": self.settings.jellyfin_url,
            "api_key_configured": bool(self.settings.jellyfin_api_key),
        }

    async def test_jellyfin(
        self, url: str | None = None, api_key: str | None = None
    ) -> dict[str, bool]:
        effective_url = (url or self.settings.jellyfin_url or "").strip().rstrip("/")
        self._validate_url(effective_url)
        effective_key = api_key
        if not effective_key and self.settings.jellyfin_api_key:
            effective_key = self.settings.jellyfin_api_key.get_secret_value()
        try:
            connected = await self._jellyfin_tester(effective_url, effective_key)
        except (OSError, httpx.HTTPError):
            connected = False
        return {"ok": connected}

    async def validate(self) -> dict[str, Any]:
        auth = await self.telegram_auth.status()
        checks = {
            "admin": self.admin.configured
            or bool(self.settings.dashboard_username and self.settings.dashboard_password),
            "telegram_api": bool(self.settings.telegram_api_id and self.settings.telegram_api_hash),
            "telegram_authorized": bool(auth["authorized"]),
            "storage": self._directory_writable(self.settings.download_root)
            and self._directory_writable(self.settings.temp_dir),
            "jellyfin": not self.settings.jellyfin_refresh_enabled
            or bool(self.settings.jellyfin_url and self.settings.jellyfin_api_key),
        }
        return {"ok": all(checks.values()), "checks": checks}

    async def complete(self) -> dict[str, bool]:
        validation = await self.validate()
        if not validation["ok"]:
            raise SetupError("Setup cannot be completed until all required checks pass.", 409)
        try:
            self.store.set_setup_completed(True)
        except SettingsStoreError as exc:
            raise SetupError(str(exc), 500) from exc
        return {"ok": True, "setup_completed": True}

    def _store_update(self, section: str, values: dict[str, Any]) -> None:
        try:
            self.store.update(section, values)
        except SettingsStoreError as exc:
            raise SetupError(str(exc), 500) from exc

    @staticmethod
    def _validate_directory(value: str, label: str) -> Path:
        if not value.strip():
            raise SetupError(f"{label} is required.")
        path = Path(value).expanduser()
        if path.resolve(strict=False) == Path("/"):
            raise SetupError(f"{label} cannot be the filesystem root.")
        try:
            path.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix=".tmd-write-test-", dir=path):
                pass
        except OSError as exc:
            raise SetupError(f"{label} is not writable.") from exc
        return path

    @staticmethod
    def _directory_writable(path: Path) -> bool:
        try:
            return path.is_dir() and os.access(path, os.W_OK)
        except OSError:
            return False

    @staticmethod
    def _validate_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise SetupError("Enter a valid Jellyfin HTTP or HTTPS URL.")

    @staticmethod
    async def _test_jellyfin(url: str, api_key: str | None) -> bool:
        headers = {"X-Emby-Token": api_key} if api_key else {}
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{url}/System/Info/Public", headers=headers)
            response.raise_for_status()
        return True
