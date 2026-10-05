#!/usr/bin/env bash
# Read-only rendered deployment admission; no credentials are printed.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
compose_bin="${COMPOSE_BIN:-docker compose}"
$compose_bin config --format json | PYTHONPATH="$root/src" python3 -c '
import json, sys
from fatty_trader.production_policy import PRODUCTION_BITGET_SERVICES, validate_production_live_policy
try:
    services = json.load(sys.stdin)["services"]
    for name in PRODUCTION_BITGET_SERVICES:
        validate_production_live_policy(services[name]["environment"])
except (ValueError, KeyError, TypeError):
    sys.exit("production LIVE-only policy rejected rendered deployment; require LIVE/LIVE, execution=1 and pinned policy marker for every Bitget service")
print("production_live_policy=PASS")
'
