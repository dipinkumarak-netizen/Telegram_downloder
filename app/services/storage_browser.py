from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

MEDIA_CATEGORIES = (
    "movies",
    "tv",
    "videos",
    "audio",
    "images",
    "documents",
    "archives",
    "other",
)
FORBIDDEN_ROOTS = {
    Path("/"),
    Path("/etc"),
    Path("/home"),
    Path("/root"),
    Path("/proc"),
    Path("/sys"),
    Path("/dev"),
    Path("/run"),
    Path("/var/lib/docker"),
}


class StorageBrowser:
    """Discover and select only explicitly approved mounted storage roots."""

    def __init__(
        self,
        container_root: str | Path | None = None,
        host_root: str | Path | None = None,
        display_name: str | None = None,
    ) -> None:
        self.container_root = Path(
            container_root or os.environ.get("TMD_STORAGE_BROWSE_CONTAINER_ROOT", "/host-storage")
        ).resolve()
        configured_host = host_root or os.environ.get("TMD_STORAGE_BROWSE_HOST_ROOT", "")
        self.host_root = (
            Path(configured_host).expanduser().resolve(strict=False) if configured_host else None
        )
        self.display_name = (
            display_name or os.environ.get("TMD_STORAGE_DISPLAY_NAME", "") or "Storage Disk"
        ).strip()

    @property
    def available(self) -> bool:
        return (
            self.host_root is not None
            and self.host_root.is_absolute()
            and self.host_root not in FORBIDDEN_ROOTS
            and self.container_root.is_dir()
        )

    def roots(self) -> list[dict[str, Any]]:
        """Return approved roots themselves; never enumerate their contents."""
        if not self.available:
            return []
        return [self._metadata()]

    def prepare_disk(self, host_path: str) -> dict[str, Any]:
        """Create the managed layout after an exact approved-root selection."""
        if not self.available or self.host_root is None:
            raise ValueError("Selected storage disk is unavailable.")
        if host_path != str(self.host_root):
            raise ValueError("Storage selection must be an approved disk root.")
        if not self.container_root.is_dir() or not os.access(self.container_root, os.W_OK):
            raise ValueError("Selected storage disk is unavailable or not writable.")

        application_root = self.container_root / "telegram-media-downloader"
        downloads = application_root / "downloads"
        incomplete = application_root / "incomplete"
        try:
            for path in (application_root, downloads, incomplete):
                path.mkdir(exist_ok=True)
            for category in MEDIA_CATEGORIES:
                (downloads / category).mkdir(exist_ok=True)
        except OSError as exc:
            raise ValueError("Selected storage disk is unavailable or not writable.") from exc

        host_application_root = self.host_root / "telegram-media-downloader"
        return {
            "display_name": self.display_name,
            "storage_root": str(self.host_root),
            "application_root": str(host_application_root),
            "host_download_dir": str(host_application_root / "downloads"),
            "host_incomplete_dir": str(host_application_root / "incomplete"),
            "download_dir": "/downloads",
            "temp_dir": "/incomplete",
        }

    def _filesystem(self) -> str | None:
        """Read the filesystem type for the container-visible approved mount when available."""
        try:
            target = str(self.container_root)
            best: tuple[int, str] | None = None
            for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
                left, right = line.split(" - ", 1)
                mountpoint = left.split()[4].replace("\\040", " ")
                if target == mountpoint or target.startswith(mountpoint.rstrip("/") + "/"):
                    filesystem = right.split()[0]
                    candidate = (len(mountpoint), filesystem)
                    if best is None or candidate[0] > best[0]:
                        best = candidate
            return best[1] if best else None
        except (OSError, ValueError, IndexError):
            return None

    def _metadata(self) -> dict[str, Any]:
        try:
            usage = shutil.disk_usage(self.container_root)
            writable = os.access(self.container_root, os.W_OK)
        except OSError:
            usage = None
            writable = False
        return {
            "display_name": self.display_name,
            "mount_path": str(self.host_root),
            "total_bytes": usage.total if usage else None,
            "free_bytes": usage.free if usage else None,
            "writable": writable,
            "filesystem": self._filesystem(),
        }
