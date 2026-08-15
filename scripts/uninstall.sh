#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_DIR="${TMD_INSTALL_DIR:-/opt/telegram-media-downloader}"
TEST_MODE="${TMD_TEST_MODE:-0}"
PURGE=0
if [[ "${1:-}" == "--purge-data" ]]; then PURGE=1; shift; fi
[[ $# -eq 0 ]] || { echo "Usage: uninstall.sh [--purge-data]" >&2; exit 1; }
[[ "$TEST_MODE" == "1" ]] || ((EUID == 0)) || { echo "Run this uninstall as root." >&2; exit 1; }
[[ "$INSTALL_DIR" == /* && "$INSTALL_DIR" != "/" ]] || { echo "Invalid install directory." >&2; exit 1; }
env_file="$INSTALL_DIR/.env"
marker_file="$INSTALL_DIR/.managed-install"
[[ -f "$marker_file" ]] || { echo "Refusing to remove an installation without its management marker." >&2; exit 1; }

read_value() {
  local file="$1" key="$2" fallback="$3" value=""
  [[ -f "$file" ]] && value="$(awk -F= -v key="$key" '$1 == key {print substr($0, index($0, "=") + 1)}' "$file")"
  printf '%s' "${value:-$fallback}"
}
data_dir="$(read_value "$env_file" TMD_DATA_HOST_DIR /var/lib/telegram-media-downloader)"
downloads_dir="$(read_value "$env_file" TMD_DOWNLOAD_HOST_DIR /srv/telegram-media-downloader/downloads)"
marker_data_dir="$(read_value "$marker_file" TMD_DATA_HOST_DIR '')"
marker_downloads_dir="$(read_value "$marker_file" TMD_DOWNLOAD_HOST_DIR '')"
[[ "$data_dir" == /* && "$data_dir" != "/" && "$downloads_dir" == /* && "$downloads_dir" != "/" ]] || {
  echo "Refusing unsafe persistent directory value." >&2; exit 1;
}
[[ "$data_dir" == "$marker_data_dir" && "$downloads_dir" == "$marker_downloads_dir" ]] || {
  echo "Refusing paths that do not match the installer management marker." >&2; exit 1;
}

if [[ "$TEST_MODE" != "1" && -f "$INSTALL_DIR/docker-compose.yml" && -f "$env_file" ]]; then
  docker compose --env-file "$env_file" -f "$INSTALL_DIR/docker-compose.yml" down
fi
if ((PURGE)); then
  printf 'This permanently deletes:\n  %s\n  %s\nType PURGE to continue: ' "$data_dir" "$downloads_dir"
  read -r confirmation
  [[ "$confirmation" == "PURGE" ]] || { echo "Purge cancelled."; exit 1; }
  rm -rf -- "$data_dir" "$downloads_dir"
  echo "Persistent state and downloads were permanently removed."
else
  printf 'Preserved persistent state at %s and downloads at %s.\n' "$data_dir" "$downloads_dir"
fi
rm -rf -- "$INSTALL_DIR"
echo "Application container and release files removed."
