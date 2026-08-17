from __future__ import annotations

import re
import unicodedata
from pathlib import Path

VIDEO = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm", ".ts", ".wmv"}
AUDIO = {".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".wma"}
IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".heic"}
ARCHIVES = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"}
DOCUMENTS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".txt",
    ".csv",
    ".epub",
    ".mobi",
    ".odt",
    ".ods",
}
SERIES_RE = re.compile(r"(?i)(?:^|[\s._-])S\d{1,2}E\d{1,3}(?:[\s._-]|$)")
YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
INVALID_RE = re.compile(r"[\x00-\x1f\x7f/\\:*?\"<>|]+")


def sanitize_filename(name: str, fallback: str = "telegram-file") -> str:
    name = unicodedata.normalize("NFKC", name)
    name = INVALID_RE.sub("_", name).strip(" .")
    if not name or name in {".", ".."}:
        name = fallback
    stem, suffix = Path(name).stem[:180], Path(name).suffix[:20]
    return f"{stem}{suffix}"[:220]


def is_series_filename(filename: str) -> bool:
    return bool(SERIES_RE.search(filename))


def classify_file(filename: str, mime_type: str | None = None) -> str:
    suffix = Path(filename).suffix.lower()
    mime = (mime_type or "").lower()
    if suffix in AUDIO or mime.startswith("audio/"):
        return "audio"
    if suffix in IMAGES or mime.startswith("image/"):
        return "images"
    if suffix in ARCHIVES or mime in {
        "application/zip",
        "application/x-rar-compressed",
        "application/x-7z-compressed",
    }:
        return "archives"
    if suffix in DOCUMENTS:
        return "documents"
    if suffix in VIDEO or mime.startswith("video/"):
        if is_series_filename(filename):
            return "tv"
        if YEAR_RE.search(filename):
            return "movies"
        return "videos"
    return "other"


def unique_destination(directory: Path, filename: str) -> Path:
    candidate = directory / sanitize_filename(filename)
    if not candidate.exists() and not candidate.with_suffix(candidate.suffix + ".part").exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 1
    while True:
        candidate = directory / f"{stem} ({counter}){suffix}"
        if (
            not candidate.exists()
            and not candidate.with_suffix(candidate.suffix + ".part").exists()
        ):
            return candidate
        counter += 1
