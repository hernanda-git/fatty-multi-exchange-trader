#!/usr/bin/env bash
set -euo pipefail

: "${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in the environment}"
backup_dir="${BACKUP_DIR:-data/backups}"
retention_days="${BACKUP_RETENTION_DAYS:-14}"
mkdir -p "$backup_dir"
file="$backup_dir/fatty_trader_$(date -u +%Y%m%dT%H%M%SZ).dump"

PGHOST="${PGHOST:-127.0.0.1}" \
PGPORT="${PGPORT:-5432}" \
PGDATABASE="${PGDATABASE:-fatty_trader}" \
PGUSER="${PGUSER:-fatty_app}" \
PGPASSWORD="$POSTGRES_PASSWORD" \
pg_dump --format=custom --no-owner --file="$file"

find "$backup_dir" -type f -name 'fatty_trader_*.dump' -mtime "+$retention_days" -delete
printf 'backup_created=%s\n' "$file"
