#!/bin/sh
set -eu

umask 002
for directory in /app/database /app/session /app/logs /downloads; do
    if [ ! -r "$directory" ] || [ ! -w "$directory" ]; then
        echo "ERROR: $directory must be readable and writable by container UID $(id -u), GID $(id -g)" >&2
        exit 1
    fi
done
exec "$@"
