from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]


def run_script(script: Path, *args: str, env: dict[str, str], input_text: str | None = None):
    return subprocess.run(
        ["bash", str(script), *args],
        cwd=REPOSITORY,
        env={**os.environ, **env},
        input=input_text,
        text=True,
        check=True,
        capture_output=True,
    )


def _current_uid() -> str:
    return str(os.getuid()) if hasattr(os, "getuid") else "1000"


def _current_gid() -> str:
    return str(os.getgid()) if hasattr(os, "getgid") else "1000"


def read_env(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if line)


def test_release_downloads_have_timeout_retry_and_progress_controls() -> None:
    for script in (REPOSITORY / "install.sh", REPOSITORY / "scripts/update.sh"):
        source = script.read_text()
        assert "--http1.1" in source
        assert "--connect-timeout 10" in source
        assert "--max-time 60" in source
        assert "--retry 3" in source
        assert "--retry-delay 2" in source
        assert "--retry-all-errors" in source
        assert "Downloading %s..." in source


def test_install_rerun_update_and_safe_uninstall(tmp_path: Path) -> None:
    install_dir = tmp_path / "opt" / "app"
    data_dir = tmp_path / "state"
    downloads_dir = tmp_path / "media"
    environment = {
        "TMD_TEST_MODE": "1",
        "TMD_SOURCE_DIR": str(REPOSITORY),
        "TMD_INSTALL_DIR": str(install_dir),
        "TMD_UID": _current_uid(),
        "TMD_GID": _current_gid(),
    }
    run_script(
        REPOSITORY / "install.sh",
        "--port", "9090",
        "--bind-address", "127.0.0.1",
        "--data-dir", str(data_dir),
        "--downloads-dir", str(downloads_dir),
        env=environment,
    )
    values = read_env(install_dir / ".env")
    assert values["TMD_HTTP_PORT"] == "9090"
    assert values["TMD_DATA_HOST_DIR"] == str(data_dir)
    assert values["TMD_DOWNLOAD_HOST_DIR"] == str(downloads_dir)
    assert "TELEGRAM" not in "\n".join(values)
    assert (data_dir / "config").is_dir()
    assert (data_dir / "database").is_dir()
    assert (data_dir / "session").is_dir()
    assert (data_dir / "logs").is_dir()
    assert (data_dir / "tmp").is_dir()

    sentinel = data_dir / "config" / "settings.json"
    sentinel.write_text("preserve-me")
    run_script(REPOSITORY / "install.sh", "--port", "9191", env=environment)
    assert read_env(install_dir / ".env") == values
    assert sentinel.read_text() == "preserve-me"

    run_script(install_dir / "update.sh", "--no-start", env=environment)
    assert sentinel.read_text() == "preserve-me"
    run_script(install_dir / "uninstall.sh", env=environment)
    assert not install_dir.exists()
    assert sentinel.read_text() == "preserve-me"
    assert downloads_dir.is_dir()


def test_installer_rejects_unsafe_arguments(tmp_path: Path) -> None:
    environment = {
        "TMD_TEST_MODE": "1",
        "TMD_SOURCE_DIR": str(REPOSITORY),
        "TMD_INSTALL_DIR": str(tmp_path / "install"),
        "TMD_UID": _current_uid(),
        "TMD_GID": _current_gid(),
    }
    for args in (
        ("--port", "abc"),
        ("--port", "0"),
        ("--port", "70000"),
        ("--data-dir", "/"),
        ("--downloads-dir", "/"),
        ("--data-dir", "relative/path"),
        ("--data-dir", str(tmp_path / "has space")),
        ("--data-dir", str(tmp_path / "bad;command")),
        ("--data-dir", ""),
    ):
        result = subprocess.run(
            ["bash", str(REPOSITORY / "install.sh"), *args],
            cwd=REPOSITORY,
            env={**os.environ, **environment},
            text=True,
            capture_output=True,
        )
        assert result.returncode != 0, args


def test_purge_requires_confirmation_and_exact_marker(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    data_dir = tmp_path / "state"
    downloads_dir = tmp_path / "downloads"
    environment = {
        "TMD_TEST_MODE": "1",
        "TMD_SOURCE_DIR": str(REPOSITORY),
        "TMD_INSTALL_DIR": str(install_dir),
        "TMD_UID": _current_uid(),
        "TMD_GID": _current_gid(),
    }
    run_script(
        REPOSITORY / "install.sh",
        "--data-dir", str(data_dir),
        "--downloads-dir", str(downloads_dir),
        env=environment,
    )
    (data_dir / "database" / "downloads.db").write_text("test-db")
    (downloads_dir / "sample.bin").write_text("test-media")

    cancelled = subprocess.run(
        ["bash", str(install_dir / "uninstall.sh"), "--purge-data"],
        cwd=REPOSITORY,
        env={**os.environ, **environment},
        input="NO\n",
        text=True,
        capture_output=True,
    )
    assert cancelled.returncode != 0
    assert (data_dir / "database" / "downloads.db").exists()
    assert (downloads_dir / "sample.bin").exists()

    run_script(
        install_dir / "uninstall.sh",
        "--purge-data",
        env=environment,
        input_text="PURGE\n",
    )
    assert not data_dir.exists()
    assert not downloads_dir.exists()
