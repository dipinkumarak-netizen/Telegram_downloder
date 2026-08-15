#!/usr/bin/env bash
set -euo pipefail

PUID="${PUID:-$(id -u)}"
PGID="${PGID:-$(id -g)}"
APPDATA="${TMD_DATA_HOST_DIR:-./data}"
MEDIA="${TMD_DOWNLOAD_HOST_DIR:-./downloads}"

if [[ "$EUID" -ne 0 ]]; then
  echo "Run with sudo: sudo PUID=$PUID PGID=$PGID ./scripts/setup.sh" >&2
  exit 1
fi
if [[ ! "$PUID" =~ ^[0-9]+$ || ! "$PGID" =~ ^[0-9]+$ ]]; then
  echo "PUID and PGID must be numeric." >&2
  exit 1
fi

install -d -m 0775 -o "$PUID" -g "$PGID" \
  "$APPDATA/db" "$APPDATA/session" "$APPDATA/config" "$APPDATA/logs" \
  "$MEDIA/movies" "$MEDIA/tv" "$MEDIA/videos" "$MEDIA/audio" \
  "$MEDIA/images" "$MEDIA/documents" "$MEDIA/archives" "$MEDIA/other" \
  "$MEDIA/incomplete"
echo "Directories are ready for UID=$PUID GID=$PGID. Existing files were not removed."
