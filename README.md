# Telegram Media Downloader

## Packaged release candidate

Release candidate `0.1.0-rc1` supports Ubuntu 24.04 and Debian 12 on amd64 and arm64.
Install with:

```bash
curl -fsSL https://raw.githubusercontent.com/dipinkumarak-netizen/Telegram_downloder/main/install.sh | sudo bash
```

The installer needs internet access and a browser that can reach the server. It installs
Docker when supported and does not alter firewall or router rules. Open `http://SERVER-IP:8787`
for the first-run wizard. Use Tailscale or an HTTPS reverse proxy for remote access; direct
HTTP is intended only for local/LAN setup.

Packaged state is stored in `/var/lib/telegram-media-downloader` and downloads in
`/srv/telegram-media-downloader/downloads`. Back up the complete state directory, especially
`config/settings.json`, `database/downloads.db` plus WAL/SHM files, and the session directory.
Update with `sudo /opt/telegram-media-downloader/update.sh`; uninstall with
`sudo /opt/telegram-media-downloader/uninstall.sh`; use `--purge-data` only after a verified
backup and explicit `PURGE` confirmation.

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

All application settings are centralized in `app/config.py`; browser-managed settings are
stored atomically in `/data/config/settings.json`. New deployments use these container paths:

```text
/data/database/downloads.db    SQLite queue and history
/data/session/downloader.session
/data/config                   browser-managed configuration/state
/data/logs                     rotating application logs
/downloads                     completed category directories
/data/tmp                      resumable .part files
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

## First-run Setup

On a fresh installation, open the application URL. The dashboard redirects to `/setup` until
setup is completed:

1. Create the single administrator account.
2. Enter the Telegram API ID and API hash from my.telegram.org.
3. Configure writable media and incomplete-download directories.
4. Optionally enable Jellyfin, save its URL/API key, and test the connection.
5. Enter the Telegram phone number, verify the OTP, and provide the Telegram 2FA password
   when required.
6. Review the non-secret summary and validate all required checks.
7. Complete setup; later visits use the normal dashboard and `/settings` page.

The data/config mount must be persistent. The wizard does not configure Docker bind mounts or
install host packages. Packaged Ubuntu/Debian instructions are in `docs/INSTALLATION.md`.

### Configuration precedence

The effective precedence is:

1. Explicit process environment or `.env` values.
2. Persisted browser-managed settings in `/data/config/settings.json`.
3. Application defaults and paths derived from `/data` and `/downloads`.

The wizard records values even when an environment variable overrides them, but reports the
override and never silently replaces an explicit environment value. Database and Telegram
session paths are deployment-managed and cannot be changed in the browser. Changing Telegram
API credentials while the listener is connected reports `restart_required=true`; the server
is never restarted automatically.

## Browser-based Telegram Login

Telegram API credentials may be supplied through the first-run wizard or environment
variables. Open the setup wizard's **Telegram Login** step or the dashboard's
**Telegram account** panel.

1. Enter the account phone number in international format and select **Send Code**.
2. Enter the OTP delivered by Telegram and select **Verify Code**.
3. If Telegram two-step verification is enabled, enter its password in the password field.
4. After authorization, the panel displays the account name, username, masked phone number,
   and authorized session status.

The OTP, two-step password, and Telegram phone-code hash are never returned or persisted.
The API hash is never returned and is protected in the owner-readable persistent settings
file. Pending browser login state remains server-side, allows only one flow at a time, and
expires after ten minutes. Successful authorization is written by Telethon to the configured
persistent session file, which the downloader then reopens for normal operation. The terminal
`scripts/telegram_login.py` remains an administrative fallback.

### Telegram Download Sources

After Telegram login, open the **Telegram Sources** step in setup (or **Settings**) and
refresh the available dialogs. Select one or more channels, groups, or supergroups, then
save. Only selected sources are monitored; private one-to-one chats and Telegram service
dialogs are excluded from discovery. If no Telegram source is selected, the downloader
remains idle. Source IDs, rather than mutable display names, are persisted in the web-managed
settings file. Existing explicit `ALLOWED_CHAT_IDS` values remain a compatibility fallback
when no browser-managed source selection exists.

### Existing deployment compatibility

An installation with explicit Telegram API credentials plus existing dashboard Basic Auth is
treated as already configured even when `settings.json` does not exist. Its environment paths,
SQLite database, Telethon session, logs, media directory, and Jellyfin settings continue to
win over browser settings. No production data is migrated or copied automatically.

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
Do not publish the dashboard to the public internet without HTTPS. Health is available at
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

Packaged installations update with `sudo /opt/telegram-media-downloader/update.sh`; see
[`docs/INSTALLATION.md`](docs/INSTALLATION.md). Source deployments may continue to use a
fast-forward Git pull and local Compose build.

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

Wizard administrator passwords are hashed with Argon2 and never stored as plaintext. Login
creates an expiring HttpOnly, SameSite=Strict session cookie; browser state changes require a
per-session CSRF token. Login failures are rate-limited in memory. Set `TMD_COOKIE_SECURE=true`
when the application is served exclusively through HTTPS; it defaults to false so direct
local HTTP remains usable during initial setup.

Telegram API hashes and Jellyfin keys are masked in API responses and stored only in the
owner-readable (`0600`) persistent settings file when browser-managed. OTPs and Telegram 2FA
passwords are never persisted. Keep `.env`, settings, sessions, databases, and backups private;
prefer an HTTPS reverse proxy or Tailscale and never expose port 8787 directly to the public
internet. The image runs as the configured non-root UID/GID with `no-new-privileges`.

## 11. Uninstall

Packaged installations use `sudo /opt/telegram-media-downloader/uninstall.sh`. Application
state and downloads are preserved unless the separately confirmed `--purge-data` option is
used. See [`docs/INSTALLATION.md`](docs/INSTALLATION.md) and [`docs/MIGRATION.md`](docs/MIGRATION.md).
Source deployments can continue to use `docker compose down`.

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
