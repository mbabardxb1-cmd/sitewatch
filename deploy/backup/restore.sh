#!/bin/sh
# Restore a backup made by backup.sh. Stop the application first:
#   docker compose stop api worker worker-audit beat web
#   docker compose --profile ops run --rm -e RESTORE_CONFIRM=yes restore /backups/sitewatch-<STAMP>
#   docker compose up -d
set -eu
SRC="${1:?usage: restore.sh /backups/sitewatch-<STAMP>}"
[ "${RESTORE_CONFIRM:-}" = "yes" ] || { echo "Refusing to overwrite data: set RESTORE_CONFIRM=yes" >&2; exit 2; }
cd "$SRC"
sha256sum -c SHA256SUMS
echo "Restoring database from $SRC/database.dump"
PGPASSWORD="$POSTGRES_PASSWORD" pg_restore -h "${POSTGRES_HOST:-db}" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    --clean --if-exists --no-owner --single-transaction database.dump
echo "Restoring files into /data"
find /data -mindepth 1 -delete
tar -C /data -xzf files.tar.gz
echo "Restore complete. Start the application and check /api/readyz."
