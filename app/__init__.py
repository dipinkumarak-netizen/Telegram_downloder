"""Telegram Media Downloader."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

try:
    _version_file = Path(__file__).resolve().parents[1] / "VERSION"
    __version__ = _version_file.read_text(encoding="utf-8").strip()
except (OSError, UnicodeError):
    try:
        __version__ = version("telegram-media-downloader")
    except PackageNotFoundError:  # Source checkout before installation.
        __version__ = "0.1.0-rc1"
