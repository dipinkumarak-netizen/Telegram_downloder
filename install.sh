#!/usr/bin/env bash
set -Eeuo pipefail

readonly REPOSITORY="dipinkumarak-netizen/Telegram_downloder"
readonly RAW_BASE="https://raw.githubusercontent.com/${REPOSITORY}"
INSTALL_DIR="${TMD_INSTALL_DIR:-/opt/telegram-media-downloader}"
DATA_DIR="/var/lib/telegram-media-downloader"
DOWNLOADS_DIR="/srv/telegram-media-downloader/downloads"
HTTP_PORT="8787"
BIND_ADDRESS="0.0.0.0"
RELEASE_REF="${TMD_RELEASE_REF:-main}"
NO_START=0
TEST_MODE="${TMD_TEST_MODE:-0}"
SOURCE_DIR="${TMD_SOURCE_DIR:-}"

usage() {
  cat <<'HELP'
Usage: install.sh [options]

Install or safely update Telegram Media Downloader.

  --port PORT             Published HTTP port (default: 8787)
  --data-dir PATH         Persistent state directory
  --downloads-dir PATH    Downloaded media directory
  --bind-address ADDRESS  Published bind address (default: 0.0.0.0)
  --no-start              Prepare files without pulling or starting a container
  --help                  Show this help
HELP
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

validate_absolute_path() {
  local label="$1" value="$2"
  [[ "$value" =~ ^/[A-Za-z0-9._/@+,-]+$ && "$value" != "/" ]] ||
    die "$label must be an absolute path containing only safe filename characters."
}

while (($#)); do
  case "$1" in
    --port) (($# >= 2)) || die "--port requires a value"; HTTP_PORT="$2"; shift 2 ;;
    --data-dir) (($# >= 2)) || die "--data-dir requires a value"; DATA_DIR="$2"; shift 2 ;;
    --downloads-dir) (($# >= 2)) || die "--downloads-dir requires a value"; DOWNLOADS_DIR="$2"; shift 2 ;;
    --bind-address) (($# >= 2)) || die "--bind-address requires a value"; BIND_ADDRESS="$2"; shift 2 ;;
    --no-start) NO_START=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
done

existing_env="$INSTALL_DIR/.env"
if [[ -f "$existing_env" ]]; then
  read_existing() {
    local key="$1" fallback="$2" value
    value="$(awk -F= -v key="$key" '$1 == key {print substr($0, index($0, "=") + 1)}' "$existing_env")"
    printf '%s' "${value:-$fallback}"
  }
  HTTP_PORT="$(read_existing TMD_HTTP_PORT "$HTTP_PORT")"
  BIND_ADDRESS="$(read_existing TMD_BIND_ADDRESS "$BIND_ADDRESS")"
  DATA_DIR="$(read_existing TMD_DATA_HOST_DIR "$DATA_DIR")"
  DOWNLOADS_DIR="$(read_existing TMD_DOWNLOAD_HOST_DIR "$DOWNLOADS_DIR")"
fi

[[ "$HTTP_PORT" =~ ^[0-9]+$ ]] && ((HTTP_PORT >= 1 && HTTP_PORT <= 65535)) ||
  die "Port must be between 1 and 65535."
[[ "$BIND_ADDRESS" =~ ^[A-Za-z0-9.-]+$ ]] || die "Invalid bind address."
validate_absolute_path "Install directory" "$INSTALL_DIR"
validate_absolute_path "Data directory" "$DATA_DIR"
validate_absolute_path "Downloads directory" "$DOWNLOADS_DIR"

if [[ "$TEST_MODE" != "1" ]]; then
  [[ "$(uname -s)" == "Linux" ]] || die "Only Linux is supported."
  ((EUID == 0)) || die "Run this installer as root (for example: curl ... | sudo bash)."
  [[ -r /etc/os-release ]] || die "Cannot identify this Linux distribution."
  # shellcheck disable=SC1091
  . /etc/os-release
  [[ "${ID:-}" == "ubuntu" || "${ID:-}" == "debian" ]] ||
    die "Only Ubuntu and Debian are supported."
fi

install_docker_repository() {
  local distro="$1" codename="$2" architecture
  architecture="$(dpkg --print-architecture)"
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/${distro}/gpg" -o /etc/apt/keyrings/docker.asc
  chmod 0644 /etc/apt/keyrings/docker.asc
  cat > /etc/apt/sources.list.d/docker.sources <<REPO
Types: deb
URIs: https://download.docker.com/linux/${distro}
Suites: ${codename}
Components: stable
Architectures: ${architecture}
Signed-By: /etc/apt/keyrings/docker.asc
REPO
  apt-get update
}

ensure_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    install_docker_repository "$ID" "${UBUNTU_CODENAME:-$VERSION_CODENAME}"
    DEBIAN_FRONTEND=noninteractive apt-get install -y \
      docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  elif ! docker compose version >/dev/null 2>&1; then
    if ! DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-plugin; then
      install_docker_repository "$ID" "${UBUNTU_CODENAME:-$VERSION_CODENAME}"
      DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-plugin
    fi
  fi
  docker compose version >/dev/null 2>&1 || die "Docker Compose plugin is unavailable."
  if command -v systemctl >/dev/null 2>&1; then
    systemctl enable --now docker
  fi
  docker info >/dev/null 2>&1 || die "Docker daemon is not available."
}

runtime_uid="${TMD_UID:-${SUDO_UID:-1000}}"
runtime_gid="${TMD_GID:-${SUDO_GID:-1000}}"
[[ "$runtime_uid" =~ ^[0-9]+$ && "$runtime_gid" =~ ^[0-9]+$ ]] ||
  die "TMD_UID and TMD_GID must be numeric."

install -d -m 0755 "$INSTALL_DIR"
install -d -m 0750 -o "$runtime_uid" -g "$runtime_gid" \
  "$DATA_DIR" "$DATA_DIR/config" "$DATA_DIR/database" "$DATA_DIR/session" \
  "$DATA_DIR/logs" "$DATA_DIR/tmp" "$DOWNLOADS_DIR"

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
      -fsSL "${RAW_BASE}/${RELEASE_REF}/${relative}" \
      -o "$destination"
  fi
}

fetch_file deploy/docker-compose.yml "$temporary_dir/docker-compose.yml"
fetch_file VERSION "$temporary_dir/VERSION"
fetch_file scripts/update.sh "$temporary_dir/update.sh"
fetch_file scripts/uninstall.sh "$temporary_dir/uninstall.sh"
install -m 0644 "$temporary_dir/docker-compose.yml" "$INSTALL_DIR/docker-compose.yml"
install -m 0644 "$temporary_dir/VERSION" "$INSTALL_DIR/VERSION"
install -m 0755 "$temporary_dir/update.sh" "$INSTALL_DIR/update.sh"
install -m 0755 "$temporary_dir/uninstall.sh" "$INSTALL_DIR/uninstall.sh"

marker_file="$INSTALL_DIR/.managed-install"
umask 077
{
  printf 'TMD_DATA_HOST_DIR=%s\n' "$DATA_DIR"
  printf 'TMD_DOWNLOAD_HOST_DIR=%s\n' "$DOWNLOADS_DIR"
} > "$marker_file"
chmod 0600 "$marker_file"

env_file="$INSTALL_DIR/.env"
if [[ ! -e "$env_file" ]]; then
  umask 077
  {
    printf 'TMD_IMAGE=ghcr.io/dipinkumarak-netizen/telegram-downloder:latest\n'
    printf 'TMD_HTTP_PORT=%s\n' "$HTTP_PORT"
    printf 'TMD_BIND_ADDRESS=%s\n' "$BIND_ADDRESS"
    printf 'TMD_DATA_HOST_DIR=%s\n' "$DATA_DIR"
    printf 'TMD_DOWNLOAD_HOST_DIR=%s\n' "$DOWNLOADS_DIR"
    printf 'TMD_UID=%s\nTMD_GID=%s\n' "$runtime_uid" "$runtime_gid"
    printf 'TZ=%s\n' "${TZ:-Etc/UTC}"
    printf 'TMD_COOKIE_SECURE=false\n'
  } > "$env_file"
  chmod 0600 "$env_file"
  printf 'Created deployment configuration: %s\n' "$env_file"
else
  printf 'Preserved existing deployment configuration: %s\n' "$env_file"
fi

if [[ -d /storage/appdata/telegram-downloader || -d /storage/media/telegram ]]; then
  printf 'NOTICE: A legacy /storage deployment may exist; it was not modified or migrated.\n'
fi

if ((NO_START)) || [[ "$TEST_MODE" == "1" ]]; then
  printf 'Installation files prepared without starting Docker.\n'
  exit 0
fi

ensure_docker
compose=(docker compose --env-file "$env_file" -f "$INSTALL_DIR/docker-compose.yml")
"${compose[@]}" pull telegram-downloader
"${compose[@]}" up -d --no-deps telegram-downloader

for attempt in {1..30}; do
  if curl -fsS --max-time 3 "http://127.0.0.1:${HTTP_PORT}/health" >/dev/null; then
    break
  fi
  ((attempt < 30)) || die "Application did not become healthy; inspect Docker Compose logs."
  sleep 2
done

server_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
printf 'Telegram Media Downloader %s is ready.\n' "$(tr -d '\n' < "$INSTALL_DIR/VERSION")"
printf 'Open http://%s:%s\n' "${server_ip:-SERVER-IP}" "$HTTP_PORT"
