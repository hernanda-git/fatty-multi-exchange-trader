#!/usr/bin/env bash
set -euo pipefail

backup_file="${1:?usage: restore_postgres.sh data/backups/fatty_trader_*.dump}"
compose_bin="${COMPOSE_BIN:-docker compose}"
if [[ ! -f "$backup_file" ]]; then
  printf 'backup_not_found=%s\n' "$backup_file" >&2
  exit 1
fi
if [[ "${CONFIRM_RESTORE:-}" != "YES" ]]; then
  printf 'refusing_restore_without_CONFIRM_RESTORE=YES\n' >&2
  exit 2
fi

# The postgres service publishes no host port, so the restore runs inside the
# container and is fed the dump on stdin — the same boundary the backup uses.
# Credentials never leave the container, so no host-side password is required.
if ! $compose_bin exec -T postgres pg_restore --list <"$backup_file" >/dev/null; then
  printf 'backup_unreadable=%s\n' "$backup_file" >&2
  exit 1
fi

running="$($compose_bin ps --status running --services | grep -vx 'postgres' || true)"
if [[ -n "$running" ]]; then
  printf 'warning=app_services_running:%s\n' "$(printf '%s' "$running" | tr '\n' ',')" >&2
fi

$compose_bin exec -T postgres pg_restore \
  --clean --if-exists --no-owner \
  --username="${POSTGRES_USER:-fatty_app}" \
  --dbname="${POSTGRES_DB:-fatty_trader}" <"$backup_file"
printf 'restore_completed=%s\n' "$backup_file"
