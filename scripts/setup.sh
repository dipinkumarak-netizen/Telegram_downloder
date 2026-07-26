#!/usr/bin/env bash
set -euo pipefail

PUID="${PUID:-$(id -u)}"
PGID="${PGID:-$(id -g)}"
APPDATA="/storage/appdata/telegram-downloader"
MEDIA="/storage/media/telegram"

if [[ "$EUID" -ne 0 ]]; then
  echo "Run with sudo: sudo PUID=$PUID PGID=$PGID ./scripts/setup.sh" >&2
  exit 1
fi
if [[ ! "$PUID" =~ ^[0-9]+$ || ! "$PGID" =~ ^[0-9]+$ ]]; then
  echo "PUID and PGID must be numeric." >&2
  exit 1
fi

install -d -m 0775 -o "$PUID" -g "$PGID" \
  "$APPDATA/database" "$APPDATA/session" "$APPDATA/logs" \
  "$MEDIA/movies" "$MEDIA/tv" "$MEDIA/videos" "$MEDIA/audio" \
  "$MEDIA/images" "$MEDIA/documents" "$MEDIA/archives" "$MEDIA/other" \
  "$MEDIA/incomplete"
echo "Directories are ready for UID=$PUID GID=$PGID. Existing files were not removed."
