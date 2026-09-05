#!/usr/bin/env bash
set -euo pipefail

compose_bin="${COMPOSE_BIN:-docker compose}"
running_services=(postgres dispatcher-bitget monitor-bitget)
completed_services=(migrate init)

printf 'runtime_sha=%s\n' "$(git rev-parse HEAD)"
# Default invocation includes: docker compose ps.
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
$compose_bin exec -T postgres psql -U fatty_app -d fatty_trader -Atc \
  "SELECT active || ':' || coalesce(reason, 'none') FROM venue_kill_switches WHERE scope = 'bitget';" \
  | sed 's/^/bitget_kill_switch=/'

$compose_bin logs --tail=100 dispatcher-bitget monitor-bitget
$compose_bin exec -T monitor-bitget /app/.venv/bin/python scripts/bitget_api_probe.py --json
printf 'runtime_check=PASS\n'
