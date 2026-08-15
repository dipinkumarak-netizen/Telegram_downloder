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
cd /path/to/telegram-media-downloader
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

## Portable Configuration

All application settings are centralized in `app/config.py`. Precedence is an explicit
environment variable, a path derived from its application root, then a portable default.
New deployments use these container paths:

```text
/data/db/downloads.db          SQLite queue and history
/data/session/downloader.session
/data/config                   reserved persistent configuration/state
/data/logs                     rotating application logs
/downloads                     completed category directories
/downloads/incomplete          resumable .part files
```

The main portable variables are `TMD_DATA_DIR`, `TMD_DATABASE_PATH`, `TMD_SESSION_DIR`,
`TMD_SESSION_NAME`, `TMD_CONFIG_DIR`, `TMD_LOG_DIR`, `TMD_DOWNLOAD_DIR`, `TMD_TEMP_DIR`,
`TMD_WEB_HOST`, and `TMD_WEB_PORT`. Compose host paths are independently controlled by
`TMD_DATABASE_HOST_DIR`, `TMD_SESSION_HOST_DIR`, `TMD_CONFIG_HOST_DIR`,
`TMD_LOG_HOST_DIR`, and `TMD_DOWNLOAD_HOST_DIR`; host publication uses
`TMD_WEB_BIND_ADDRESS`. See `.env.example` for the complete interface.

Legacy `DATABASE_PATH`, `TELEGRAM_SESSION_PATH`, `LOG_DIR`, `DOWNLOAD_ROOT`,
`DASHBOARD_HOST`, `DASHBOARD_PORT`, and `DASHBOARD_BIND_IP` remain supported. Compose keeps
transitional `/app/database`, `/app/session`, and `/app/logs` mounts so the current `.env`
continues to resolve existing state. The `/storage` Compose defaults intentionally retain
the current deployment; fresh installations should copy `.env.example`, whose host mount
defaults are project-relative.

Telethon receives a session basename and appends `.session`. Preserve that exact file and
mount its directory persistently. SQLite's database directory, including any WAL/SHM files,
and the entire media directory must also remain persistent across upgrades. Never run two
downloader instances against the same database or Telegram session.

The first-run browser setup wizard and browser-based Telegram authentication are planned but
are separate features. The complete first-run setup wizard is not implemented yet.

## Browser-based Telegram Login

Telegram API credentials must currently be supplied through `TELEGRAM_API_ID` and
`TELEGRAM_API_HASH`; the browser cannot create or edit them yet. Configure a non-empty
`DASHBOARD_USERNAME` and `DASHBOARD_PASSWORD`, start the application, and open the dashboard's
**Telegram account** panel.

1. Enter the account phone number in international format and select **Send Code**.
2. Enter the OTP delivered by Telegram and select **Verify Code**.
3. If Telegram two-step verification is enabled, enter its password in the password field.
4. After authorization, the panel displays the account name, username, masked phone number,
   and authorized session status.

The OTP, two-step password, API hash, and Telegram phone-code hash are never returned by the
API or persisted as application settings. Pending browser login state remains server-side,
allows only one flow at a time, and expires after ten minutes. Successful authorization is
written by Telethon to the configured persistent session file, which the downloader then
reopens for normal operation. The terminal `scripts/telegram_login.py` remains available as
an administrative fallback.

The full first-run browser setup wizard, including API credential entry, will be implemented
in a later phase.

## 3. Build and Telegram login

Build before login so credentials never need to be installed on the host:

```bash
docker compose build telegram-downloader
docker compose run --rm --entrypoint python telegram-downloader scripts/telegram_login.py
```

The script interactively requests phone (unless `TELEGRAM_PHONE` is configured), OTP, and
(when enabled) 2FA password. OTP/password input is hidden and never logged. With the portable
example, the session persists at `/data/session/downloader.session` in the container. The
current compatibility mount retains
`/storage/appdata/telegram-downloader/session/downloader.session` on the host.

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
cd /path/to/telegram-media-downloader
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
