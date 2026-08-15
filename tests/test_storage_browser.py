from pathlib import Path

import pytest

from app.services.storage_browser import StorageBrowser


def test_browse_validate_and_create_folder(tmp_path: Path):
    root = tmp_path / "mounted"
    root.mkdir()
    (root / "Movies").mkdir()
    browser = StorageBrowser(root, "/storage")
    listing = browser.browse()
    assert listing["folders"][0]["name"] == "Movies"
    assert browser.validate("")["writable"]
    created = browser.create_folder("", "Telegram")
    assert created["host_path"] == "/storage/Telegram"


def test_traversal_and_symlink_escape_are_rejected(tmp_path: Path):
    root = tmp_path / "mounted"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    browser = StorageBrowser(root, "/storage")
    with pytest.raises(ValueError):
        browser.browse("../outside")
    with pytest.raises(ValueError):
        browser.browse("escape")


def test_unconfigured_root_is_safe():
    browser = StorageBrowser("/does-not-exist", None)
    assert not browser.available
    assert browser.roots() == []
