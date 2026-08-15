from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any

from pydantic import SecretStr

from app.config import Settings

DEFAULT_DATA: dict[str, Any] = {
    "version": 1,
    "setup_completed": False,
    "admin": {},
    "telegram": {},
    "storage": {},
    "jellyfin": {"enabled": False},
}


class SettingsStoreError(RuntimeError):
    pass


class SettingsStore:
    """Atomic, owner-readable persistent settings storage."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = RLock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return deepcopy(DEFAULT_DATA)
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SettingsStoreError("Persistent settings could not be read.") from exc
            if not isinstance(loaded, dict) or loaded.get("version") != 1:
                raise SettingsStoreError("Persistent settings have an unsupported format.")
            data = deepcopy(DEFAULT_DATA)
            for key in data:
                if key in loaded:
                    data[key] = loaded[key]
            return data

    def update(self, section: str, values: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            data = self.load()
            current = data.get(section)
            if not isinstance(current, dict):
                current = {}
            current.update(values)
            data[section] = current
            self._write(data)
            return deepcopy(data)

    def set_setup_completed(self, completed: bool) -> None:
        with self._lock:
            data = self.load()
            data["setup_completed"] = completed
            self._write(data)

    def _write(self, data: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor, temporary = tempfile.mkstemp(
                prefix=f".{self.path.name}.", dir=self.path.parent
            )
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, indent=2, sort_keys=True)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
                os.chmod(self.path, 0o600)
            except Exception:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
                raise
        except OSError as exc:
            raise SettingsStoreError("Persistent settings could not be written.") from exc


class RuntimeSettings:
    """Applies web settings without overriding environment-provided fields."""

    def __init__(
        self,
        settings: Settings,
        store: SettingsStore,
        *,
        environment_fields: set[str] | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.environment_fields = (
            settings.explicit_fields
            if environment_fields is None
            else set(environment_fields)
        )

    @property
    def legacy_environment_configured(self) -> bool:
        return bool(
            {"telegram_api_id", "telegram_api_hash"} <= self.environment_fields
            and self.settings.telegram_api_id
            and self.settings.telegram_api_hash
            and self.settings.dashboard_username
            and self.settings.dashboard_password
        )

    @property
    def setup_completed(self) -> bool:
        return bool(self.store.load().get("setup_completed")) or self.legacy_environment_configured

    def apply_persisted(self) -> None:
        data = self.store.load()
        telegram = data.get("telegram", {})
        storage = data.get("storage", {})
        jellyfin = data.get("jellyfin", {})
        if "telegram_api_id" not in self.environment_fields and telegram.get("api_id"):
            self.settings.telegram_api_id = int(telegram["api_id"])
        if "telegram_api_hash" not in self.environment_fields and telegram.get("api_hash"):
            self.settings.telegram_api_hash = SecretStr(str(telegram["api_hash"]))
        if "download_root" not in self.environment_fields and storage.get("download_dir"):
            self.settings.download_root = Path(storage["download_dir"])
        if "temp_dir" not in self.environment_fields and storage.get("temp_dir"):
            self.settings.temp_dir = Path(storage["temp_dir"])
        if "jellyfin_refresh_enabled" not in self.environment_fields:
            self.settings.jellyfin_refresh_enabled = bool(jellyfin.get("enabled", False))
        if "jellyfin_url" not in self.environment_fields and "url" in jellyfin:
            self.settings.jellyfin_url = jellyfin.get("url") or None
        if "jellyfin_api_key" not in self.environment_fields and jellyfin.get("api_key"):
            self.settings.jellyfin_api_key = SecretStr(str(jellyfin["api_key"]))
