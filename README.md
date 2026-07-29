# Telegram Media Downloader

A production-oriented Telegram MTProto downloader for Ubuntu Server, Docker Compose,
Portainer, and Jellyfin. It watches only configured chats, records every job in SQLite,
streams files to an `incomplete` directory, atomically moves successful downloads into
media libraries, and exposes a small operational dashboard.

## Why Telethon instead of the Bot API?

The Bot API is excellent for bot workflows, but its file download endpoint has a file-size
ceiling and bots can only see messages/chats permitted by bot membership and privacy rules.
Large movies and arbitrary forwarded files make those constraints unsuitable here. Telethon
uses Telegram's MTProto client API as your user account, supports large media, private
channels/groups that the account can access, and Saved Messages. The account must comply
with Telegram's Terms; use a dedicated account if practical.

## Architecture

`TelegramService` filters new file messages by numeric chat allow-list. SQLite in WAL mode
provides the durable queue and uniqueness constraints. A configurable worker pool retrieves
the original message, streams it with Telethon to `/downloads/incomplete/*.part`, validates
that it is non-empty, then uses an atomic rename. Classification is based on extension,
MIME type, year patterns, and `SxxExx`. FastAPI serves status/control APIs and the responsive
dashboard. Jellyfin refresh is best-effort and never changes a successful download to failed.

The suggested structure was retained, with the dashboard routes under `app/api`; templates,
deployment scripts, tests, and runtime modules remain separate.

## 1. Telegram API credentials

1. Sign into <https://my.telegram.org>.
2. Open **API development tools**, create an application, and copy `api_id` and `api_hash`.
3. Never post or commit either the API hash or the generated `.session` file.

To discover chat IDs after login, run:

```bash
docker compose run --rm --entrypoint python telegram-downloader -c \
  "from app.config import get_settings; from telethon.sync import TelegramClient; s=get_settings(); c=TelegramClient(str(s.telegram_session_path),s.telegram_api_id,s.telegram_api_hash.get_secret_value()); c.start(); [print(d.id, d.name) for d in c.get_dialogs()]; c.disconnect()"
```

The command still requires an authorized session. Supergroups/channels normally appear as
`-100...`. Always copy the exact ID printed by Telethon.

## 2. Host setup

```bash
cd /home/dipin/projects/telegram-media-downloader
id
cp .env.example .env
nano .env
chmod 600 .env
chmod +x scripts/setup.sh docker-entrypoint.sh
sudo PUID="$(id -u)" PGID="$(id -g)" ./scripts/setup.sh
```

Set the same `PUID` and `PGID` values in `.env`. The setup script uses `install -d`; it does
not delete existing data. Essential settings are:

- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`
- `ALLOWED_CHAT_IDS` as comma-separated integers
- `DASHBOARD_BIND_IP`: use the server Tailscale IP, or `127.0.0.1` for SSH forwarding
- non-empty `DASHBOARD_USERNAME` and a strong `DASHBOARD_PASSWORD` if other hosts can connect
- optional size, free-space, concurrency, rate, Telegram replies, and Jellyfin settings

`BANDWIDTH_LIMIT_MBPS=0` is unlimited. The limiter is approximate and applies per download.
`MAX_FILE_SIZE_GB=0` means unlimited.

## 3. Build and Telegram login

Build before login so credentials never need to be installed on the host:

```bash
docker compose build telegram-downloader
docker compose run --rm --entrypoint python telegram-downloader scripts/telegram_login.py
```

The script interactively requests phone, OTP, and (when enabled) 2FA password. OTP/password
input is hidden and never logged. The resulting session persists at
`/storage/appdata/telegram-downloader/session/downloader.session`.

## 4. Start and operate

```bash
docker compose up -d telegram-downloader
docker compose ps
docker compose logs -f --tail=200 telegram-downloader
```

Open `http://SERVER_TAILSCALE_IP:8787` (or the bind address/port configured in `.env`).
Do not publish this unauthenticated dashboard to the public internet. Health is available at
`/health`; application logs also rotate under
`/storage/appdata/telegram-downloader/logs`.

The dashboard shows Telegram connectivity, durable queue, current progress/speed, totals,
disk space, failures, events, and safe retry/cancel controls. Cancellation removes only the
temporary `.part`, never completed media.

## 5. Portainer Stack

Portainer's Web editor cannot build reliably without the project source as build context.
Use a Git repository deployment or upload this directory:

1. **Stacks → Add stack → Repository**.
2. Enter your private repository URL and Compose path `docker-compose.yml`.
3. Add the `.env` variables in Portainer's environment-variable UI (do not commit `.env`).
4. Confirm `/storage` exists on the Docker host and deploy.
5. Run the one-off login command once from an SSH terminal using the exact project directory.

For a Web editor deployment, first build `telegram-media-downloader:local` over SSH, paste
the Compose YAML, remove its `build:` block, and retain `image:
telegram-media-downloader:local`.

## 6. Jellyfin

Either use an existing Jellyfin container and add these read-only mounts, or start the
included optional profile:

```bash
mkdir -p /storage/appdata/jellyfin/{config,cache}
docker compose --profile jellyfin up -d
```

In Jellyfin Dashboard → Libraries, add:

- Movies → `/media/movies`
- Shows → `/media/tv`
- Other Videos → `/media/videos`
- Music → `/media/audio`

Jellyfin's real-time monitoring can detect changes. Alternatively create an API key in
Jellyfin Dashboard → API Keys, store it only as `JELLYFIN_API_KEY` in `.env`, and enable
`JELLYFIN_REFRESH_ENABLED=true`. If Jellyfin is unavailable, a warning is logged and the
file remains completed.

## 7. Updating

```bash
cd /home/dipin/projects/telegram-media-downloader
git pull --ff-only
docker compose build --pull telegram-downloader
docker compose up -d telegram-downloader
```

Do not use `latest` blindly for a critical deployment; pin the application commit and,
if using the bundled Jellyfin service, pin a tested Jellyfin image tag.

## 8. Backup and restore

Stop the downloader for a fully consistent simple backup:

```bash
docker compose stop telegram-downloader
sudo tar -C /storage/appdata -czf telegram-downloader-backup.tgz telegram-downloader
docker compose start telegram-downloader
```

Media may be backed up separately. To restore, stop the service, restore the directory with
the same UID/GID, then start it. The session file is account access material: encrypt backups.

## 9. Troubleshooting

- **Session unauthorized:** rerun the login command; confirm the session mount is writable.
- **Permission denied:** compare `id` with `.env` PUID/PGID, rerun `setup.sh`, then rebuild.
- **No messages detected:** confirm the user account belongs to the chat and the exact signed
  chat ID is in `ALLOWED_CHAT_IDS`. Empty allow-list is intentionally unsafe; configure it.
- **Paused / disk space:** free space or lower `MIN_FREE_SPACE_GB`; restart currently retries
  paused jobs.
- **FloodWait/network errors:** jobs return to the queue with exponential or Telegram-directed
  delay, up to `MAX_RETRIES`.
- **Jellyfin misses files:** verify read-only mounts and library paths, then scan the library.
- **Inspect DB:** stop the app before manual changes; normal inspection can use `sqlite3` on
  `/storage/appdata/telegram-downloader/database/downloads.db`.

## 10. Security

Keep `.env` mode `0600`, session/database backups private, use Tailscale ACLs, and never
expose port 8787 publicly without TLS authentication. The image runs as the configured
non-root UID/GID and enables `no-new-privileges`. Telegram status replies may reveal
filenames; leave them disabled in sensitive chats. Jellyfin keys are read from `.env` only.

## 11. Uninstall

```bash
docker compose down
```

This intentionally preserves all appdata and media. After verifying backups, remove only
the exact directories yourself if desired; the project supplies no destructive uninstall
script.

## Known limitations and next steps

- Interrupted downloads resume from the existing `.part` size when Telegram still serves the
  same media; invalid oversize partials are discarded and restarted from zero automatically.
- Classification is deliberately basic; naming tools such as FileBot/Radarr/Sonarr are not
  integrated.
- Status replies are queued/completed/duplicate/error oriented; dashboard progress is the
  primary live UI. A future release can persist and rate-limit editable Telegram status
  message IDs.
- Approximate bandwidth limiting is per worker, not a global token bucket.
- SQLite schema initialization is idempotent; future schema changes should add numbered
  migrations before changing deployed tables.

Run local validation with:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/ruff check .
docker compose config
```
