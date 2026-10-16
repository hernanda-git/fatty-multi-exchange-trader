#!/usr/bin/env bash
set +x
set -euo pipefail
umask 077

# Online restore drill, never disaster recovery. There is intentionally no
# destination argument: this helper can only create its own disposable database.
backup_file="${1:?usage: verify_restore_postgres.sh backup.dump}"
if [[ "${CONFIRM_ISOLATED_RESTORE:-}" != "YES" ]]; then
  printf 'refusing_restore_without_CONFIRM_ISOLATED_RESTORE=YES\n' >&2
  exit 2
fi
expected_version="${RESTORE_EXPECTED_SCHEMA_VERSION:-}"
if [[ ! "$expected_version" =~ ^[1-9][0-9]*$ ]]; then
  printf 'restore_verification_failed=expected_schema_version_required\n' >&2
  exit 2
fi
if [[ ! -f "$backup_file" ]]; then
  printf 'restore_verification_failed=backup_not_found\n' >&2
  exit 2
fi
compose_bin="${COMPOSE_BIN:-docker compose}"
admin_user="${POSTGRES_ADMIN_USER:-fatty_app}"
# Identifiers contain only this fixed prefix, digits and underscores; neither
# POSTGRES_DB nor operator input can select the target of restore or cleanup.
verify_db="fatty_restore_check_$(date -u +%Y%m%d%H%M%S)_${RANDOM}_${RANDOM}"
if [[ "$verify_db" == "${POSTGRES_DB:-fatty_trader}" || "$verify_db" == postgres ]]; then
  printf 'restore_verification_failed=unsafe_destination\n' >&2
  exit 2
fi
created=0
cleanup() {
  status=$?
  trap - EXIT
  if ((created)); then
    if ! $compose_bin exec -T --user postgres postgres dropdb \
      --username="$admin_user" --maintenance-db=postgres -- "$verify_db" >/dev/null 2>&1; then
      printf 'restore_verification_cleanup_failed=%s\n' "$verify_db" >&2
      status=1
    else
      remaining=""
      if ! remaining="$($compose_bin exec -T --user postgres postgres psql \
        --no-psqlrc --username="$admin_user" --dbname=postgres \
        --tuples-only --no-align --set=ON_ERROR_STOP=1 \
        --command="SELECT count(*) FROM pg_database WHERE datname = '$verify_db'" 2>/dev/null)" \
        || [[ "$remaining" != "0" ]]; then
        printf 'restore_verification_cleanup_unconfirmed=%s\n' "$verify_db" >&2
        status=1
      else
        printf 'restore_verification_cleanup_verified=%s\n' "$verify_db"
      fi
    fi
  fi
  if ((status == 0)); then
    printf 'restore_verified=%s schema_version=%s\n' "$verify_db" "$expected_version"
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if ! $compose_bin exec -T --user postgres postgres pg_restore --list <"$backup_file" >/dev/null 2>&1; then
  printf 'restore_verification_failed=unreadable_dump\n' >&2
  exit 1
fi
# Creation must succeed before cleanup is armed: a name collision never drops
# somebody else's database. template0 avoids unrelated objects from template1.
if ! $compose_bin exec -T --user postgres postgres createdb \
  --username="$admin_user" --maintenance-db=postgres --template=template0 \
  -- "$verify_db" >/dev/null 2>&1; then
  printf 'restore_verification_failed=create_database\n' >&2
  exit 1
fi
created=1
if ! $compose_bin exec -T --user postgres postgres psql \
  --no-psqlrc --username="$admin_user" --dbname=postgres --set=ON_ERROR_STOP=1 \
  --command="REVOKE CONNECT ON DATABASE $verify_db FROM PUBLIC" >/dev/null 2>&1; then
  printf 'restore_verification_failed=isolate_database\n' >&2
  exit 1
fi
# No dumped ACL can grant a runtime role access to this disposable database.
# The restore is one transaction and stops at the first error, including COPY.
if ! $compose_bin exec -T --user postgres postgres pg_restore \
  --username="$admin_user" --dbname="$verify_db" \
  --no-owner --no-acl --exit-on-error --single-transaction <"$backup_file" >/dev/null 2>&1; then
  printf 'restore_verification_failed=restore\n' >&2
  exit 1
fi
if ! actual_version="$($compose_bin exec -T --user postgres postgres psql \
  --no-psqlrc --username="$admin_user" --dbname="$verify_db" \
  --tuples-only --no-align --set=ON_ERROR_STOP=1 \
  --command='SELECT max(version) FROM public.schema_migrations' 2>/dev/null)"; then
  printf 'restore_verification_failed=schema_version_query\n' >&2
  exit 1
fi
if [[ "$actual_version" != "$expected_version" ]]; then
  printf 'restore_verification_failed=schema_version_mismatch\n' >&2
  exit 1
fi
# Actual queries prove that restored schema and data are readable, rather than
# merely trusting a TOC. No row contents, credentials or financial state escape.
if ! counts="$($compose_bin exec -T --user postgres postgres psql \
  --no-psqlrc --username="$admin_user" --dbname="$verify_db" \
  --tuples-only --no-align --set=ON_ERROR_STOP=1 --command="
SELECT 'dispatches', count(*) FROM public.dispatches
UNION ALL SELECT 'dispatch_transitions', count(*) FROM public.dispatch_transitions
UNION ALL SELECT 'positions', count(*) FROM public.positions
UNION ALL SELECT 'orders', count(*) FROM public.orders
UNION ALL SELECT 'fills', count(*) FROM public.fills
UNION ALL SELECT 'live_order_intents', count(*) FROM public.live_order_intents
UNION ALL SELECT 'console_jobs', count(*) FROM public.console_jobs
UNION ALL SELECT 'console_job_steps', count(*) FROM public.console_job_steps
UNION ALL SELECT 'console_snapshots', count(*) FROM public.console_snapshots
UNION ALL SELECT 'console_event_outbox', count(*) FROM public.console_event_outbox
" 2>/dev/null)"; then
  printf 'restore_verification_failed=core_table_queries\n' >&2
  exit 1
fi
printf 'restored_core_table_rows\n%s\n' "$counts"
# EXIT cleanup drops only the successfully created generated database and then
# independently verifies its absence before printing the success marker.
