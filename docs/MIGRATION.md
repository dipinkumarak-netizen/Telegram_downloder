# Migrating a legacy deployment

Migration is deliberately manual: the installer never reads, copies, moves, or deletes an
existing `/storage` deployment.

1. Back up the legacy application state and media.
2. Stop the legacy downloader so SQLite and the Telethon session have a single writer.
3. Run the packaged installer with `--no-start`, using temporary or final new paths.
4. Copy the complete config directory, database directory (including `-wal`/`-shm` files),
   session directory (including `.session` files), logs if desired, and downloads into the
   target layout while the downloader remains stopped.
5. Preserve ownership for the `TMD_UID:TMD_GID` configured in `/opt/telegram-media-downloader/.env`.
6. Start with `docker compose --env-file /opt/telegram-media-downloader/.env -f
   /opt/telegram-media-downloader/docker-compose.yml up -d` and verify `/health` and the UI.
7. Retain the backup until Telegram authorization, queue history, and media are verified.

Legacy source paths may include `/storage/appdata/telegram-downloader` and
`/storage/media/telegram`; they appear here only as migration examples. Never run old and new
containers against the same SQLite database or Telegram session. Jellyfin data is outside the
packaged downloader and is not migrated or modified.
