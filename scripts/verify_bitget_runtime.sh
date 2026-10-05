#!/usr/bin/env bash
set -euo pipefail

compose_bin="${COMPOSE_BIN:-docker compose}"
running_services=(postgres dispatcher-bitget monitor-bitget)
completed_services=(migrate init)

printf 'runtime_sha=%s\n' "$(git rev-parse HEAD)"
# Default invocation includes: docker compose ps.
bash "$(dirname "${BASH_SOURCE[0]}")/check_production_live_policy.sh"
# A valid host rendering is not proof of the environment baked into containers.
for service in dispatcher-bitget monitor-bitget operator-bot source-management; do
  runtime_policy="$($compose_bin exec -T "$service" sh -lc 'printf "%s|%s|%s" "$TRADER_MODE" "$BITGET_MODE" "$BITGET_EXECUTION_ENABLED"')"
  if test "$runtime_policy" != 'LIVE|LIVE|1'; then
    printf 'runtime_blocked=production_live_policy service=%s require=LIVE/LIVE/1\n' "$service" >&2
    exit 1
  fi
done
$compose_bin config --quiet
$compose_bin ps

for service in "${running_services[@]}"; do
  $compose_bin ps --status running "$service" | grep -q "$service" || {
    printf 'runtime_blocked=service_not_running service=%s\n' "$service" >&2
    exit 1
  }
done

for service in "${completed_services[@]}"; do
  container_id="$($compose_bin ps -aq "$service")"
  test -n "$container_id" || {
    printf 'runtime_blocked=service_missing service=%s\n' "$service" >&2
    exit 1
  }
  state="$(docker inspect --format '{{.State.Status}}:{{.State.ExitCode}}' "$container_id")"
  test "$state" = "exited:0" || {
    printf 'runtime_blocked=service_incomplete service=%s state=%s\n' "$service" "$state" >&2
    exit 1
  }
done

$compose_bin exec -T postgres psql -U fatty_app -d fatty_trader -Atc \
  "SELECT string_agg(version::text, ',' ORDER BY version) FROM schema_migrations;" \
  | sed 's/^/schema_migrations=/'
kill_state="$($compose_bin exec -T postgres psql -U fatty_app -d fatty_trader -Atc \
  "SELECT active || ':' || coalesce(reason, 'none') FROM venue_kill_switches WHERE scope IN ('global', 'bitget');")"
printf '%s\n' "$kill_state" | sed 's/^/entry_kill_switch=/'
if printf '%s\n' "$kill_state" | grep -Eq '^(true|t):'; then
  printf 'runtime_blocked=kill_switch_active action=preserve_latch\n' >&2
  exit 1
fi

# Compare copied Python source, not just the host Git SHA or an optional image label.
source_probe='import hashlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
files = sorted(root.rglob("*.py"))
if not files:
    sys.exit("source tree missing")
h = hashlib.sha256()
for path in files:
    h.update(str(path.relative_to(root)).encode() + b"\0" + path.read_bytes() + b"\0")
print(h.hexdigest())'
source_digest="$(python3 -c "$source_probe" "$(pwd)/src")"
for service in dispatcher-bitget monitor-bitget operator-bot source-management web; do
  deployed_digest="$($compose_bin exec -T "$service" /app/.venv/bin/python -c "$source_probe" /app/src)"
  if test "$deployed_digest" != "$source_digest"; then
    printf 'runtime_blocked=source_lineage_mismatch service=%s\n' "$service" >&2
    exit 1
  fi
done
printf 'runtime_source_digest=%s\n' "$source_digest"

$compose_bin logs --tail=100 dispatcher-bitget monitor-bitget
$compose_bin exec -T monitor-bitget /app/.venv/bin/python scripts/bitget_api_probe.py --json
printf 'runtime_check=PASS\n'
