#!/bin/sh
set -eu

umask 002
for directory in \
    "${TMD_DATABASE_PATH:-${DATABASE_PATH:-/data/database/downloads.db}}" \
    "${TELEGRAM_SESSION_PATH:-${TMD_SESSION_DIR:-/data/session}/${TMD_SESSION_NAME:-downloader}}" \
    "${TMD_CONFIG_DIR:-${TMD_DATA_DIR:-/data}/config}" \
    "${TMD_LOG_DIR:-${LOG_DIR:-/data/logs}}" \
    "${TMD_DOWNLOAD_DIR:-${DOWNLOAD_ROOT:-/downloads}}" \
    "${TMD_TEMP_DIR:-${TMD_DATA_DIR:-/data}/tmp}"; do
    if [ ! -d "$directory" ]; then
        directory=$(dirname "$directory")
    fi
    if [ ! -r "$directory" ] || [ ! -w "$directory" ]; then
        echo "ERROR: $directory must be readable and writable by container UID $(id -u), GID $(id -g)" >&2
        exit 1
    fi
done
exec "$@"
