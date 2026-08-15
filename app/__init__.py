"""Telegram Media Downloader."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("telegram-media-downloader")
except PackageNotFoundError:  # Source checkout before installation.
    __version__ = "0.1.0"
