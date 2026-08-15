#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_DIR="${TMD_INSTALL_DIR:-/opt/telegram-media-downloader}"
RELEASE_REF="${TMD_RELEASE_REF:-main}"
RAW_BASE="https://raw.githubusercontent.com/dipinkumarak-netizen/Telegram_downloder/${RELEASE_REF}"
SOURCE_DIR="${TMD_SOURCE_DIR:-}"
TEST_MODE="${TMD_TEST_MODE:-0}"
NO_START=0
if [[ "${1:-}" == "--no-start" ]]; then NO_START=1; shift; fi
[[ $# -eq 0 ]] || { echo "Usage: update.sh [--no-start]" >&2; exit 1; }
[[ "$INSTALL_DIR" == /* && "$INSTALL_DIR" != "/" ]] || { echo "Invalid install directory." >&2; exit 1; }
[[ "$TEST_MODE" == "1" ]] || ((EUID == 0)) || { echo "Run this update as root." >&2; exit 1; }
[[ -f "$INSTALL_DIR/.managed-install" && -f "$INSTALL_DIR/.env" && -f "$INSTALL_DIR/docker-compose.yml" ]] || {
  echo "Packaged installation not found at $INSTALL_DIR." >&2; exit 1;
}

temporary_dir="$(mktemp -d)"
trap 'rm -rf "$temporary_dir"' EXIT
fetch_file() {
  local relative="$1" destination="$2"
  if [[ -n "$SOURCE_DIR" ]]; then
    install -m 0644 "$SOURCE_DIR/$relative" "$destination"
  else
    printf 'Downloading %s...\n' "$relative"
    curl --http1.1 \
      --connect-timeout 10 \
      --max-time 60 \
      --retry 3 \
      --retry-delay 2 \
      --retry-all-errors \
      -fsSL "$RAW_BASE/$relative" \
      -o "$destination"
  fi
}
for relative in deploy/docker-compose.yml VERSION scripts/update.sh scripts/uninstall.sh; do
  fetch_file "$relative" "$temporary_dir/$(basename "$relative")"
done
install -m 0644 "$temporary_dir/docker-compose.yml" "$INSTALL_DIR/docker-compose.yml"
install -m 0644 "$temporary_dir/VERSION" "$INSTALL_DIR/VERSION"
install -m 0755 "$temporary_dir/update.sh" "$INSTALL_DIR/update.sh"
install -m 0755 "$temporary_dir/uninstall.sh" "$INSTALL_DIR/uninstall.sh"

if ! grep -q '^TMD_STORAGE_BROWSE_HOST_ROOT=' "$INSTALL_DIR/.env"; then
  printf '\nTMD_STORAGE_BROWSE_HOST_ROOT=/storage\n' >> "$INSTALL_DIR/.env"
  chmod 0600 "$INSTALL_DIR/.env"
fi

if ((NO_START)) || [[ "$TEST_MODE" == "1" ]]; then
  echo "Release files updated without starting Docker."
  exit 0
fi

compose=(docker compose --env-file "$INSTALL_DIR/.env" -f "$INSTALL_DIR/docker-compose.yml")
"${compose[@]}" pull telegram-downloader
"${compose[@]}" up -d --no-deps telegram-downloader
port="$(awk -F= '$1 == "TMD_HTTP_PORT" {print substr($0, index($0, "=") + 1)}' "$INSTALL_DIR/.env")"
port="${port:-8787}"
for attempt in {1..30}; do
  if curl -fsS --max-time 3 "http://127.0.0.1:${port}/health" >/dev/null; then
    printf 'Update complete; persistent state and downloads were preserved.\n'
    exit 0
  fi
  ((attempt < 30)) || { echo "Updated container did not become healthy." >&2; exit 1; }
  sleep 2
done
