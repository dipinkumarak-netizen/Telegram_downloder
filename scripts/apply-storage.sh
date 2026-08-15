#!/usr/bin/env bash
set -Eeuo pipefail
INSTALL_DIR="${TMD_INSTALL_DIR:-/opt/telegram-media-downloader}"
[[ "$INSTALL_DIR" == /* && "$INSTALL_DIR" != "/" ]] || exit 1
ENV_FILE="$INSTALL_DIR/.env"; [[ -f "$ENV_FILE" ]] || exit 1
env_value() { awk -F= -v key="$1" '$1 == key {print substr($0, index($0, "=") + 1); exit}' "$ENV_FILE"; }
data_dir="$(env_value TMD_DATA_HOST_DIR)"; data_dir="${data_dir:-/var/lib/telegram-media-downloader}"
root="$(env_value TMD_STORAGE_BROWSE_HOST_ROOT)"; root="${root:-/storage}"
pending="$data_dir/config/pending-download-host-dir"; [[ -f "$pending" ]] || { echo "No pending storage selection." >&2; exit 1; }
selected="$(head -n 1 "$pending")"; root_real="$(realpath -e -- "$root")" || { echo "Selected storage is unavailable. No changes were applied." >&2; exit 1; }; selected_real="$(realpath -e -- "$selected")" || { echo "Selected storage is unavailable. No changes were applied." >&2; exit 1; }
[[ -d "$selected_real" && -w "$selected_real" && "$selected_real" != "/" ]] || { echo "Selected storage is unavailable. No changes were applied." >&2; exit 1; }
case "$selected_real" in "$root_real"|"$root_real"/*) ;; *) echo "Selected storage is outside the approved root." >&2; exit 1 ;; esac
case "$selected_real" in /etc|/proc|/sys|/dev|/run|/var/lib/docker|/home) echo "Invalid selected storage." >&2; exit 1 ;; esac
download_host="$selected_real/telegram-media-downloader/downloads"
incomplete_host="$selected_real/telegram-media-downloader/incomplete"
[[ -d "$download_host" && -d "$incomplete_host" ]] || { echo "Selected storage is unavailable. No changes were applied." >&2; exit 1; }
tmp="$(mktemp "$ENV_FILE.tmp.XXXXXX")"; trap 'rm -f "$tmp"' EXIT
awk -F= -v d="$download_host" -v i="$incomplete_host" 'BEGIN{D=0;I=0} $1=="TMD_DOWNLOAD_HOST_DIR"{if(!D++)print "TMD_DOWNLOAD_HOST_DIR=" d;next} $1=="TMD_INCOMPLETE_HOST_DIR"{if(!I++)print "TMD_INCOMPLETE_HOST_DIR=" i;next} {print} END{if(!D)print "TMD_DOWNLOAD_HOST_DIR=" d;if(!I)print "TMD_INCOMPLETE_HOST_DIR=" i}' "$ENV_FILE" > "$tmp"
chmod 0600 "$tmp"; mv -f -- "$tmp" "$ENV_FILE"; rm -f -- "$pending"
if [[ "${TMD_TEST_MODE:-0}" == "1" ]]; then echo "Storage bind configuration applied (test mode)."; exit 0; fi
compose=(docker compose --env-file "$ENV_FILE" -f "$INSTALL_DIR/docker-compose.yml")
"${compose[@]}" up -d --no-deps --force-recreate telegram-downloader
port="$(env_value TMD_HTTP_PORT)"; port="${port:-8787}"
for attempt in {1..30}; do
  if curl -fsS --max-time 3 "http://127.0.0.1:${port}/health" >/dev/null; then echo "Storage bind applied; existing files remain in their previous location."; exit 0; fi
  ((attempt < 30)) || { echo "Container did not become healthy." >&2; exit 1; }; sleep 2
done
