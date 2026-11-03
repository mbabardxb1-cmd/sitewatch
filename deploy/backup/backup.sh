#!/bin/sh
# Back up the PostgreSQL database and the file storage (report PDFs, logo).
# Run:  docker compose --profile ops run --rm backup
set -eu
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="/backups/sitewatch-$STAMP"
mkdir -p "$OUT"
echo "Backing up database to $OUT/database.dump"
PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h "${POSTGRES_HOST:-db}" -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    --format=custom --no-owner --file "$OUT/database.dump"
echo "Backing up files to $OUT/files.tar.gz"
tar -C /data -czf "$OUT/files.tar.gz" .
(cd "$OUT" && sha256sum database.dump files.tar.gz > SHA256SUMS)
# Keep the last N days of backups (default 14). Copy backups off the server as well!
find /backups -maxdepth 1 -type d -name 'sitewatch-*' -mtime +"${BACKUP_KEEP_DAYS:-14}" -exec rm -rf {} +
echo "Backup complete: $OUT"
ls -l "$OUT"
