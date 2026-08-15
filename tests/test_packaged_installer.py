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


def read_env(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if line)


def test_install_rerun_update_and_safe_uninstall(tmp_path: Path) -> None:
    install_dir = tmp_path / "opt" / "app"
    data_dir = tmp_path / "state"
    downloads_dir = tmp_path / "media"
    environment = {
        "TMD_TEST_MODE": "1",
        "TMD_SOURCE_DIR": str(REPOSITORY),
        "TMD_INSTALL_DIR": str(install_dir),
        "TMD_UID": str(os.getuid()),
        "TMD_GID": str(os.getgid()),
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
