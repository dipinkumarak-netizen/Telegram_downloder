# Packaged installation

Supported release targets are Ubuntu 24.04 and Debian 12 on amd64 and arm64. The published
image is multi-architecture; Docker must be able to run the selected architecture.

## One-command Ubuntu/Debian install

Review `install.sh` before running it, then install from `main`:

```bash
curl -fsSL https://raw.githubusercontent.com/dipinkumarak-netizen/Telegram_downloder/main/install.sh | sudo bash
```

For a reproducible release, replace `main` in the URL and set `TMD_RELEASE_REF` to the same
signed release tag. The installer accepts:

```text
--port PORT
--bind-address ADDRESS
--data-dir ABSOLUTE_PATH
--downloads-dir ABSOLUTE_PATH
--no-start
```

Arguments can be passed through a pipe, for example:

```bash
curl -fsSL https://raw.githubusercontent.com/dipinkumarak-netizen/Telegram_downloder/main/install.sh | \
  sudo bash -s -- --port 9090 --data-dir /var/lib/tmd --downloads-dir /srv/media/telegram
```

The installer supports Ubuntu and Debian, requires root only for host provisioning, installs
Docker Engine from Docker's official apt repository only when Docker is absent, and installs
the Compose plugin when needed. Files are downloaded to a temporary directory before they
replace release files. Existing `.env`, state, sessions, database, logs, and downloads are
preserved on rerun; existing deployment paths and port win over new arguments.

The release files live in `/opt/telegram-media-downloader`. State is under
`/var/lib/telegram-media-downloader/{config,database,session,logs,tmp}`, and media is under
`/srv/telegram-media-downloader/downloads`. Inside the container these are `/data/...` and
`/downloads`. No Telegram or Jellyfin credential is written by the installer. Open
`http://SERVER-IP:8787` (or the custom port) and complete the browser setup wizard.

## Images and versions

GitHub Actions publishes `ghcr.io/dipinkumarak-netizen/telegram-downloder` for amd64 and
arm64. `latest` tracks `main`; every build also has an immutable `sha-<short-sha>` tag.
`vX.Y.Z` Git tags additionally publish `X.Y.Z` and `X.Y`. The application version comes from
`VERSION` and is returned by `/health`. Pin `TMD_IMAGE` in the installation `.env` when an
immutable deployment is required.

## Updates

```bash
sudo /opt/telegram-media-downloader/update.sh
```

The updater refreshes Compose and maintenance scripts, pulls the configured image, recreates
only the application service, and waits for `/health`. Bind-mounted state and downloads are
not removed. Use `--no-start` to refresh release files without Docker activity.

## Uninstall

```bash
sudo /opt/telegram-media-downloader/uninstall.sh
```

This removes the container and packaged release files but preserves data and downloads.
`--purge-data` requires typing `PURGE` and deletes only the exact paths recorded by the
installer's management marker. Back up the session, database, settings, and media first.

The installer prepares each bind-mounted directory with the configured UID/GID. A manually
written Compose file must do the same; an empty root-owned bind mount will correctly fail the
least-privilege container startup check. The default published port is 8787, bound to all
host interfaces; use Tailscale or an HTTPS reverse proxy for remote access. The installer does
not change firewall or router rules. Initial HTTP access is intended for local/LAN setup only.
