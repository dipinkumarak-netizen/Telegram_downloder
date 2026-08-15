from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any


class StorageBrowser:
    """Browse only the explicitly mounted storage tree exposed to the container."""

    def __init__(
        self, container_root: str | Path | None = None, host_root: str | Path | None = None
    ) -> None:
        self.container_root = Path(
            container_root or os.environ.get("TMD_STORAGE_BROWSE_CONTAINER_ROOT", "/host-storage")
        ).resolve()
        configured_host = host_root or os.environ.get("TMD_STORAGE_BROWSE_HOST_ROOT", "")
        self.host_root = Path(configured_host).expanduser().resolve() if configured_host else None

    @property
    def available(self) -> bool:
        return self.host_root is not None and self.container_root.is_dir()

    def _safe(self, relative: str | None = "") -> Path:
        if not self.available:
            raise ValueError("Storage browsing is not configured.")
        value = (relative or "").strip()
        if "\x00" in value:
            raise ValueError("Invalid storage path.")
        candidate = (self.container_root / value.lstrip("/")).resolve(strict=False)
        try:
            candidate.relative_to(self.container_root)
        except ValueError as exc:
            raise ValueError("Storage path is outside the approved root.") from exc
        return candidate

    def _host_path(self, path: Path) -> str:
        assert self.host_root is not None
        relative = path.relative_to(self.container_root)
        return str(self.host_root / relative)

    def container_path(self, relative: str | None = "") -> str:
        return str(self._safe(relative))

    def relative_for_host(self, value: str) -> str:
        if self.host_root is None:
            raise ValueError("Storage browsing is not configured.")
        candidate = Path(value).expanduser().resolve(strict=False)
        try:
            return str(candidate.relative_to(self.host_root))
        except ValueError as exc:
            raise ValueError("Storage path is outside the approved root.") from exc

    def roots(self) -> list[dict[str, Any]]:
        if not self.available:
            return []
        return [self._metadata(self.container_root, "")] if self.container_root.is_dir() else []

    def browse(self, relative: str | None = "") -> dict[str, Any]:
        path = self._safe(relative)
        if not path.is_dir():
            raise ValueError("Storage folder is unavailable.")
        folders = []
        try:
            for child in sorted(path.iterdir(), key=lambda item: item.name.casefold()):
                if child.is_dir() and not child.is_symlink():
                    folders.append(
                        self._metadata(child, str(child.relative_to(self.container_root)))
                    )
        except OSError as exc:
            raise ValueError("Storage folder cannot be read.") from exc
        parent = None
        if path != self.container_root:
            parent = str(path.parent.relative_to(self.container_root))
        return {
            "current": self._metadata(path, str(path.relative_to(self.container_root))),
            "parent": parent,
            "folders": folders,
        }

    def validate(self, relative: str | None = "") -> dict[str, Any]:
        path = self._safe(relative)
        if not path.is_dir():
            return {"writable": False, "reason": "Folder is unavailable."}
        probe = path / ".tmd-write-probe"
        try:
            probe.write_bytes(b"")
            probe.unlink()
            writable = True
        except OSError:
            writable = False
        return {
            "writable": writable,
            "free_bytes": shutil.disk_usage(path).free,
            "host_path": self._host_path(path),
        }

    def create_folder(self, relative: str, name: str) -> dict[str, Any]:
        if not name or name in {".", ".."} or any(char in name for char in "/\\\x00\r\n\t"):
            raise ValueError("Folder name is invalid.")
        parent = self._safe(relative)
        if not parent.is_dir():
            raise ValueError("Parent folder is unavailable.")
        target = self._safe(str(Path(relative or "") / name))
        try:
            target.mkdir()
        except FileExistsError as exc:
            raise ValueError("Folder already exists.") from exc
        except OSError as exc:
            raise ValueError("Folder could not be created.") from exc
        return self._metadata(target, str(target.relative_to(self.container_root)))

    def _metadata(self, path: Path, relative: str) -> dict[str, Any]:
        try:
            usage = shutil.disk_usage(path)
            writable = os.access(path, os.W_OK)
        except OSError:
            usage = None
            writable = False
        return {
            "name": path.name or (self.host_root.name if self.host_root else "Storage"),
            "path": relative,
            "host_path": self._host_path(path),
            "total_bytes": usage.total if usage else None,
            "free_bytes": usage.free if usage else None,
            "writable": writable,
        }
