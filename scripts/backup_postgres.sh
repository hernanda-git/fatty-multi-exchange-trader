#!/usr/bin/env bash
set -euo pipefail

# Compose-operated backup: credentials remain inside the postgres container.
backup_dir="${BACKUP_DIR:-backups}"
compose_bin="${COMPOSE_BIN:-docker compose}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
file="$backup_dir/fatty_trader_${timestamp}.dump"
retain_days="${BACKUP_RETAIN_DAYS:-14}"

mkdir -p "$backup_dir"
# Default invocation: docker compose exec -T postgres pg_dump (COMPOSE_BIN is a test override).
# Docker exec runs as root by default, which is not a PostgreSQL role in this image.
$compose_bin exec -T postgres pg_dump \
  --username="${POSTGRES_USER:-fatty_app}" \
  --format=custom \
  --no-owner \
  --dbname="${POSTGRES_DB:-fatty_trader}" >"$file"
if ! test -s "$file"; then
  rm -f "$file"
  printf 'backup_failed=empty_dump\n' >&2
  exit 1
fi

# Integrity gate: an unverifiable dump is worthless in an incident, so prove the
# archive is readable instead of merely non-empty.
if ! $compose_bin exec -T postgres pg_restore --list <"$file" >/dev/null 2>&1; then
  rm -f "$file"
  printf 'backup_failed=unreadable_dump\n' >&2
  exit 1
fi

# Retention: keep the newest BACKUP_RETAIN_DAYS dumps (default 14) so the backup
# directory cannot grow without bound on a small host. Set 0 to keep everything.
if [[ "$retain_days" =~ ^[0-9]+$ ]] && ((retain_days > 0)); then
  find "$backup_dir" -maxdepth 1 -type f -name 'fatty_trader_*.dump' \
    -mtime "+${retain_days}" -print -delete || true
fi

printf 'backup_created=%s bytes=%s\n' "$file" "$(wc -c <"$file")"
printf 'restore_command=CONFIRM_RESTORE=YES scripts/restore_postgres.sh %s\n' "$file"
