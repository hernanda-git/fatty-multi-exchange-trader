#!/usr/bin/env bash
set -euo pipefail

: "${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in the environment}"
backup_file="${1:?usage: restore_postgres.sh data/backups/fatty_trader_*.dump}"
if [[ ! -f "$backup_file" ]]; then
  printf 'backup_not_found=%s\n' "$backup_file" >&2
  exit 1
fi
if [[ "${CONFIRM_RESTORE:-}" != "YES" ]]; then
  printf 'refusing_restore_without_CONFIRM_RESTORE=YES\n' >&2
  exit 2
fi

PGHOST="${PGHOST:-127.0.0.1}" \
PGPORT="${PGPORT:-5432}" \
PGDATABASE="${PGDATABASE:-fatty_trader}" \
PGUSER="${PGUSER:-fatty_app}" \
PGPASSWORD="$POSTGRES_PASSWORD" \
pg_restore --clean --if-exists --no-owner --dbname="$PGDATABASE" "$backup_file"
printf 'restore_completed=%s\n' "$backup_file"
