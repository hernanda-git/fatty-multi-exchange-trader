#!/usr/bin/env bash
# Deterministic PAPER-only operator report. Runs on the deployment host.
set -euo pipefail

cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
root="$PWD"
cd "$root"

value_from_env() {
  grep -m1 "^$1=" .env | cut -d= -f2-
}
html_escape() {
  sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}
query() {
  docker compose exec -T postgres psql -U fatty_app -d fatty_trader -At -F '|' -c "$1"
}

chat_id="$(value_from_env TELEGRAM_TARGET_CHAT_ID)"
token="$(value_from_env TELEGRAM_BOT_TOKEN)"
if [[ ! "$chat_id" =~ ^-?[0-9]+$ ]] || [[ -z "$token" ]]; then
  echo "ERROR missing Telegram target or bot token" >&2
  exit 2
fi

health="$(curl --max-time 3 --silent --show-error --fail "http://127.0.0.1:${WEB_HOST_PORT:-18081}/health" 2>&1 || true)"
if [[ "$health" == *'"status":"ok"'* ]]; then overall='🟢 ONLINE'; else overall='⚠️ DEGRADED'; fi

services="$(docker compose ps --format '{{.Service}}|{{.State}}|{{.Health}}' | sort)"
intake_id="$(docker compose ps -q intake)"
started="$(docker inspect -f '{{.State.StartedAt}}' "$intake_id" 2>/dev/null || true)"
uptime='N/A'
if [[ -n "$started" ]]; then
  uptime="$(date -u -d "$started" '+%Y-%m-%d %H:%M UTC' 2>/dev/null || printf '%s' "$started")"
fi

metrics="$(query "
SELECT 'telegram_messages', count(*)::text FROM telegram_messages
UNION ALL SELECT 'canonical_signals', count(*)::text FROM canonical_signals
UNION ALL SELECT 'open_positions', count(*)::text FROM positions WHERE closed_at IS NULL
UNION ALL SELECT 'pending_orders', count(*)::text FROM orders WHERE state NOT IN ('FILLED','CANCELLED','REJECTED','CLOSED')
UNION ALL SELECT 'total_orders', count(*)::text FROM orders;
")"
latest="$(query "SELECT channel_id::text, message_id::text, to_char(received_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI UTC'), encode(convert_to(left(raw_text, 1200), 'UTF8'), 'base64') FROM telegram_messages ORDER BY received_at DESC, message_id DESC LIMIT 1;" || true)"

# Codex subscription quota is informational only. Never let this optional probe
# make the operational report fail, and never persist the access token.
usage_cache="${XDG_CACHE_HOME:-$HOME/.cache}/fatty/codex_usage.json"
mkdir -p "$(dirname "$usage_cache")"
IFS='|' read -r codex_usage_status codex_5h codex_7d codex_reset codex_plan codex_refreshed < <(python3 - "$usage_cache" <<'PY'
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

cache_path = sys.argv[1]
def emit(status, five, seven, reset, plan, refreshed):
    print("|".join((status, five, seven, reset, plan, refreshed)))

def fmt_reset(value):
    if value is None:
        return "N/A"
    try:
        seconds = max(0, int(value))
    except (TypeError, ValueError):
        return "N/A"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"

def auth_candidates():
    for path in (os.path.expanduser("~/.pi/agent/auth.json"), os.path.expanduser("~/.codex/auth.json")):
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        def walk(value):
            if isinstance(value, dict):
                token = value.get("access_token") or value.get("accessToken")
                account = value.get("account_id") or value.get("accountId")
                if isinstance(token, str) and token and not token.startswith("sk-"):
                    yield token, account if isinstance(account, str) else None
                for child in value.values():
                    yield from walk(child)
        yield from walk(data)

def fmt_reset(seconds):
    if seconds is None:
        return "N/A"
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"

def from_cache():
    try:
        cached = json.load(open(cache_path, encoding="utf-8"))
        emit("STALE", cached.get("5h", "N/A"), cached.get("7d", "N/A"),
             cached.get("reset", "N/A"), cached.get("plan", "N/A"), cached.get("refreshed", "unknown"))
    except Exception:
        emit("N/A", "N/A", "N/A", "N/A", "N/A", "never")

try:
    token, account_id = next(auth_candidates())
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    if account_id:
        headers["chatgpt-account-id"] = account_id
    req = urllib.request.Request(
        "https://chatgpt.com/backend-api/wham/usage",
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=12) as response:
        data = json.load(response)
    rate = data["rate_limit"]
    primary = rate["primary_window"]
    secondary = rate["secondary_window"]
    now = datetime.now(timezone.utc).strftime("%m-%d %H:%M UTC")
    plan = str(data.get("plan_type") or "N/A")
    fresh = {
        "5h": f"{primary['used_percent']}% used / {100 - primary['used_percent']}% left",
        "7d": f"{secondary['used_percent']}% used / {100 - secondary['used_percent']}% left",
        "reset": f"5h {fmt_reset(primary.get('reset_after_seconds'))}; 7d {fmt_reset(secondary.get('reset_after_seconds'))}",
        "plan": plan,
        "refreshed": now,
        "saved_at": int(time.time()),
    }
    tmp = cache_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(fresh, f)
    os.replace(tmp, cache_path)
    emit("LIVE", fresh["5h"], fresh["7d"], fresh["reset"], fresh["plan"], fresh["refreshed"])
except Exception:
    from_cache()
PY
)

codex_cli_version="$(codex --version 2>/dev/null || printf '%s' 'unavailable')"
codex_model="${CODEX_MODEL:-gpt-5.6-luna}"
codex_reasoning="${CODEX_REASONING_EFFORT:-medium}"

lookup_metric() { awk -F'|' -v key="$1" '$1 == key { print $2; exit }' <<<"$metrics"; }
message_count="$(lookup_metric telegram_messages)"
signal_count="$(lookup_metric canonical_signals)"
open_positions="$(lookup_metric open_positions)"
pending_orders="$(lookup_metric pending_orders)"
total_orders="$(lookup_metric total_orders)"

service_rows=''
while IFS='|' read -r service state health_state; do
  [[ -z "$service" ]] && continue
  icon='✅'
  [[ "$state" == 'running' && "$health_state" == 'healthy' ]] || icon='⚠️'
  service_rows+="$icon <code>$service</code>  $state / ${health_state:-n/a}"$'\n'
done <<<"$services"

if [[ -n "$latest" ]]; then
  IFS='|' read -r source_channel source_message received encoded_text <<<"$latest"
  source_text="$(printf '%s' "$encoded_text" | base64 -d 2>/dev/null || printf '%s' '[unreadable message]')"
  source_text="$(printf '%s' "$source_text" | html_escape)"
  last_signal="Received: <code>$received</code>\nSource: <code>$source_channel</code> · Message <code>$source_message</code>\n<pre>$source_text</pre>"
else
  last_signal='<i>No source message has been stored yet.</i>'
fi

codex_plan="$(printf '%s' "$codex_plan" | html_escape)"
codex_refreshed="$(printf '%s' "$codex_refreshed" | html_escape)"
report="<b>Fatty Signal Relay</b>  <i>Paper Ops</i>

<b>Status</b>
<pre>Overall  $overall
Mode     PAPER
Host     fspmi-hostinger
Uptime   $uptime</pre>

<b>Services</b>
<pre>$(printf '%s' "$services" | while IFS='|' read -r service state health_state; do [[ -z "$service" ]] && continue; icon='✅'; [[ "$state" == 'running' && "$health_state" == 'healthy' ]] || icon='⚠️'; printf '%-20s %s\n' "$service" "$icon ${health_state:-n/a}"; done)</pre>

<b>Codex Usage</b> <code>$codex_usage_status</code>
<pre>Plan     $codex_plan
Window   Used / Left
5h       $codex_5h
7d       $codex_7d
Reset    $codex_reset
Updated  $codex_refreshed</pre>

<b>Latest Signal</b>
$last_signal

<b>Trading</b>
<pre>Balance  N/A (paper ledger)
Open Pos ${open_positions:-0}
Pending  ${pending_orders:-0}
Profit   N/A
Loss     N/A
Fees     N/A
Net P&amp;L  N/A</pre>

<b>Database</b>
<pre>Messages $message_count
Signals  $signal_count
Orders   $total_orders</pre>

<b>Safety</b> <code>PAPER · LIVE DISABLED · REAL ORDERS DISABLED</code>"

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
printf '%b\n' "$report" > "$tmp"
http_code="$(curl --max-time 8 --silent --show-error -o "$tmp.response" -w '%{http_code}' \
  -X POST "https://api.telegram.org/bot${token}/sendMessage" \
  --data-urlencode "chat_id=${chat_id}" \
  --data-urlencode "parse_mode=HTML" \
  --data-urlencode "disable_web_page_preview=true" \
  --data-urlencode "text@${tmp}")"
if [[ "$http_code" != '200' ]] || ! grep -q '"ok":true' "$tmp.response"; then
  echo "ERROR Telegram delivery failed http=$http_code" >&2
  exit 1
fi
printf 'telegram_delivery=ok http=%s messages=%s positions=%s pending_orders=%s\n' \
  "$http_code" "${message_count:-0}" "${open_positions:-0}" "${pending_orders:-0}"
