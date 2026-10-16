#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077

# Only the container-local bootstrap administrator performs maintenance. The
# runtime worker role deliberately cannot back up or restore the whole database.
backup_dir="${BACKUP_DIR:-backups}"
compose_bin="${COMPOSE_BIN:-docker compose}"
admin_user="${POSTGRES_ADMIN_USER:-fatty_app}"
retain_days="${BACKUP_RETAIN_DAYS:-14}"
if [[ ! "$retain_days" =~ ^[0-9]{1,5}$ ]]; then
  printf 'backup_failed=invalid_retention_days\n' >&2
  exit 2
fi
retain_days=$((10#$retain_days))
if [[ -L "$backup_dir" ]]; then
  printf 'backup_failed=symlink_directory\n' >&2
  exit 2
fi
mkdir -p -- "$backup_dir"
if [[ ! -O "$backup_dir" ]]; then
  printf 'backup_failed=directory_not_owned\n' >&2
  exit 2
fi
chmod 700 -- "$backup_dir"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
partial="$(mktemp "$backup_dir/.fatty_trader_${timestamp}.XXXXXX.partial")"
cleanup() {
  rm -f -- "$partial"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
suffix="${partial%.partial}"
suffix="${suffix##*.}"
file="$backup_dir/fatty_trader_${timestamp}_${suffix}.dump"

# No password or connection URI crosses the container boundary or enters logs.
if ! $compose_bin exec -T --user postgres postgres pg_dump \
  --username="$admin_user" \
  --format=custom \
  --no-owner \
  --dbname="${POSTGRES_DB:-fatty_trader}" >"$partial" 2>/dev/null; then
  printf 'backup_failed=dump_failed\n' >&2
  exit 1
fi
if [[ ! -s "$partial" ]]; then
  printf 'backup_failed=empty_dump\n' >&2
  exit 1
fi
if ! $compose_bin exec -T --user postgres postgres pg_restore --list <"$partial" >/dev/null 2>&1; then
  printf 'backup_failed=unreadable_dump\n' >&2
  exit 1
fi

# Same-filesystem hard-link publication is atomic and never overwrites an
# existing backup, including simultaneous invocations within the same second.
chmod 600 -- "$partial"
ln -- "$partial" "$file"
rm -f -- "$partial"

# Age-based retention preserves the original find -mtime +N semantics (0 keeps
# everything). Only this script's exact current or legacy dump names qualify;
# symlinks, nested files, partials and unrelated archives are never removed.
if ((retain_days > 0)); then
  find "$backup_dir" -regextype posix-extended -maxdepth 1 -type f \
    -regex '.*/fatty_trader_[0-9]{8}T[0-9]{6}Z(_[A-Za-z0-9]{6})?\.dump' \
    -mtime "+${retain_days}" -print -delete
fi

printf 'backup_created=%s bytes=%s\n' "$file" "$(wc -c <"$file")"
printf 'isolated_restore_command=CONFIRM_ISOLATED_RESTORE=YES RESTORE_EXPECTED_SCHEMA_VERSION=<version> bash scripts/verify_restore_postgres.sh %q\n' "$file"
