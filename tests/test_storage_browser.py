from pathlib import Path

import pytest

from app.services.storage_browser import StorageBrowser


def test_only_approved_root_is_returned_with_capacity_metadata(tmp_path: Path):
    root = tmp_path / "mounted"
    root.mkdir()
    (root / "Movies").mkdir()
    browser = StorageBrowser(root, "/storage", "External HDD")
    assert browser.roots() == [
        {
            "display_name": "External HDD",
            "mount_path": "/storage",
            "total_bytes": browser.roots()[0]["total_bytes"],
            "free_bytes": browser.roots()[0]["free_bytes"],
            "writable": True,
            "filesystem": browser.roots()[0]["filesystem"],
        }
    ]
    assert browser.roots()[0]["total_bytes"] > 0
    assert browser.roots()[0]["free_bytes"] > 0
    assert "Movies" not in str(browser.roots())


def test_subdirectories_and_arbitrary_paths_cannot_be_selected(tmp_path: Path):
    root = tmp_path / "mounted"
    root.mkdir()
    browser = StorageBrowser(root, "/storage")
    for path in ("/storage/media", "/etc", "/home", "/root", "/var/lib/docker", ""):
        with pytest.raises(ValueError, match="approved disk root"):
            browser.prepare_disk(path)


def test_unconfigured_root_is_safe():
    browser = StorageBrowser("/does-not-exist", None)
    assert not browser.available
    assert browser.roots() == []


@pytest.mark.parametrize(
    "forbidden", ["/", "/etc", "/home", "/root", "/proc", "/sys", "/dev", "/run"]
)
def test_forbidden_roots_are_never_available(tmp_path: Path, forbidden: str):
    browser = StorageBrowser(tmp_path, forbidden)
    assert not browser.available
    assert browser.roots() == []


def test_selecting_disk_creates_managed_layout_idempotently_and_preserves_media(
    tmp_path: Path,
):
    root = tmp_path / "mounted"
    root.mkdir()
    existing = root / "telegram-media-downloader/downloads/movies/existing.mkv"
    existing.parent.mkdir(parents=True)
    existing.write_text("media")
    browser = StorageBrowser(root, "/storage")
    result = browser.prepare_disk("/storage")
    repeated = browser.prepare_disk("/storage")
    assert result == repeated
    assert result["application_root"] == "/storage/telegram-media-downloader"
    assert result["host_download_dir"] == "/storage/telegram-media-downloader/downloads"
    assert result["host_incomplete_dir"] == "/storage/telegram-media-downloader/incomplete"
    assert result["download_dir"] == "/downloads"
    assert result["temp_dir"] == "/incomplete"
    assert (root / "telegram-media-downloader/downloads/movies").is_dir()
    assert (root / "telegram-media-downloader/incomplete").is_dir()
    assert existing.read_text() == "media"


def test_missing_disk_is_rejected_without_local_fallback(tmp_path: Path):
    root = tmp_path / "missing"
    browser = StorageBrowser(root, "/storage")
    with pytest.raises(ValueError, match="unavailable"):
        browser.prepare_disk("/storage")
    assert not root.exists()
