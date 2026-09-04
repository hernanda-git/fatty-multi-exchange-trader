#!/usr/bin/env bash
# Deterministic operator report. Runs on the deployment host.
# Live Bitget telemetry is DB-backed and best-effort: missing data renders
# as N/A or STALE, never as fabricated zeros.
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

# ---- Live trading telemetry (Bitget, DB-backed, best-effort) ----
# Every probe ends in `|| true`: an empty result renders as N/A or STALE
# below, never as a fabricated zero. Table/column names follow
# src/fatty_trader/storage/schema.py LIVE_SCHEMA_SQL exactly.
esc() { printf '%s' "$1" | html_escape; }

live_balance="$(query "SELECT total_balance::text, available_balance::text, equity::text, margin_coin, to_char(captured_at AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI UTC'), floor(extract(epoch FROM (now() - captured_at)))::text FROM balance_snapshots WHERE exchange = 'bitget' ORDER BY captured_at DESC LIMIT 1;" || true)"
live_positions="$(query "SELECT symbol, side, size::text, COALESCE(entry_price::text, 'N/A'), COALESCE(mark_price::text, 'N/A'), COALESCE(liquidation_price::text, 'N/A'), COALESCE(leverage::text, 'N/A'), COALESCE(margin_mode, 'N/A'), unrealized_pnl::text FROM position_snapshots WHERE exchange = 'bitget' AND captured_at = (SELECT max(captured_at) FROM position_snapshots WHERE exchange = 'bitget') ORDER BY symbol LIMIT 20;" || true)"
live_sltp="$(query "SELECT symbol, bool_or(role = 'SL' AND state IN ('requested', 'acknowledged'))::text, bool_or(role = 'TP' AND state IN ('requested', 'acknowledged'))::text FROM live_order_intents WHERE exchange = 'bitget' GROUP BY symbol;" || true)"
live_pending="$(query "SELECT symbol, side, role, requested_qty::text, COALESCE(requested_price::text, 'N/A'), state FROM live_order_intents WHERE exchange = 'bitget' AND state IN ('requested', 'acknowledged') ORDER BY created_at DESC LIMIT 10;" || true)"
live_pnl="$(query "SELECT count(*)::text, COALESCE(sum(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END)::text, 'N/A'), COALESCE(sum(CASE WHEN realized_pnl < 0 THEN realized_pnl ELSE 0 END)::text, 'N/A'), COALESCE(sum(fee)::text, 'N/A'), COALESCE(sum(realized_pnl - fee)::text, 'N/A') FROM fills WHERE exchange = 'bitget';" || true)"
live_unreal="$(query "SELECT COALESCE(sum(unrealized_pnl)::text, 'N/A') FROM position_snapshots WHERE exchange = 'bitget' AND captured_at = (SELECT max(captured_at) FROM position_snapshots WHERE exchange = 'bitget');" || true)"

sltp_for() { awk -F'|' -v sym="$1" -v col="$2" '$1 == sym { print $col; exit }' <<<"$live_sltp"; }

if [[ -n "$live_balance" ]]; then
  IFS='|' read -r bal_total bal_avail bal_equity bal_coin bal_when bal_age <<<"$live_balance"
  bal_flag='LIVE'
  if [[ "${bal_age:-0}" -gt 1800 ]]; then bal_flag='STALE'; fi
  balance_block="Total      $(esc "${bal_total:-N/A}") $(esc "${bal_coin:-N/A}") [$bal_flag]
Available  $(esc "${bal_avail:-N/A}") $(esc "${bal_coin:-N/A}")
Equity     $(esc "${bal_equity:-N/A}") $(esc "${bal_coin:-N/A}")
Updated    $(esc "${bal_when:-N/A}")"
else
  balance_block='N/A (no balance_snapshots for bitget)'
fi

missing_sl=''
if [[ -n "$live_positions" ]]; then
  positions_block='SYMBOL       SIDE  SIZE       ENTRY      MARK       LIQ        LEV  MARGIN   SL   TP   UPNL'
  while IFS='|' read -r psym pside psize pentry pmark pliq plev pmode pupnl; do
    [[ -z "$psym" ]] && continue
    sl="$(sltp_for "$psym" 2)"; tp="$(sltp_for "$psym" 3)"
    if [[ "$sl" == 'true' ]]; then sl='OK'; else sl='MISS'; missing_sl+="$psym "; fi
    if [[ "$tp" == 'true' ]]; then tp='OK'; else tp='--'; fi
    positions_block+=$'\n'"$(printf '%-12s %-5s %-10s %-10s %-10s %-10s %-4s %-8s %-4s %-4s %s' "$(esc "$psym")" "$(esc "$pside")" "$(esc "$psize")" "$(esc "$pentry")" "$(esc "$pmark")" "$(esc "$pliq")" "$(esc "$plev")" "$(esc "$pmode")" "$sl" "$tp" "$(esc "$pupnl")")"
  done <<<"$live_positions"
else
  positions_block='N/A (no position_snapshots for bitget)'
fi

if [[ -n "$live_pending" ]]; then
  orders_block='SYMBOL       SIDE  ROLE  QTY        PRICE      STATE'
  while IFS='|' read -r osym oside orole oqty oprice ostate; do
    [[ -z "$osym" ]] && continue
    orders_block+=$'\n'"$(printf '%-12s %-5s %-5s %-10s %-10s %s' "$(esc "$osym")" "$(esc "$oside")" "$(esc "$orole")" "$(esc "$oqty")" "$(esc "$oprice")" "$(esc "$ostate")")"
  done <<<"$live_pending"
else
  orders_block='N/A (no pending live_order_intents for bitget)'
fi

if [[ -n "$live_pnl" ]]; then
  IFS='|' read -r fill_n pnl_profit pnl_loss pnl_fees pnl_net <<<"$live_pnl"
  if [[ "${fill_n:-0}" == '0' ]]; then
    pnl_block='N/A (no fills for bitget yet)'
  else
    pnl_block="Profit     $(esc "${pnl_profit:-N/A}")  [$(esc "${fill_n:-?}") fills]
Loss       $(esc "${pnl_loss:-N/A}")
Fees       $(esc "${pnl_fees:-N/A}")
Net        $(esc "${pnl_net:-N/A}")
Unrealized $(esc "${live_unreal:-N/A}")  (open positions)"
  fi
else
  pnl_block='N/A (fills unavailable)'
fi

if [[ -n "$live_positions" ]]; then
  modes="$(cut -d'|' -f8 <<<"$live_positions" | sort -u | paste -sd, -)"
  levs="$(cut -d'|' -f7 <<<"$live_positions" | sort -u | paste -sd, -)"
  if [[ "$modes" == 'ISOLATED' ]]; then iso='yes'; elif [[ "$modes" == *'ISOLATED'* ]]; then iso='mixed'; elif [[ -n "$modes" ]]; then iso='no'; else iso='N/A'; fi
  if [[ -z "${missing_sl// }" ]]; then sliq='OK'; else sliq="MISSING ${missing_sl}"; fi
  safety_block="Isolated     $(esc "$iso") [$(esc "$modes")]
Leverage     $(esc "${levs:-N/A}")
SL-before-liq $(esc "$sliq")"
else
  safety_block='Isolated     N/A
Leverage     N/A
SL-before-liq N/A (no position snapshots)'
fi
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

<b>Balance</b> <code>bitget</code>
<pre>$balance_block</pre>

<b>Positions</b> <code>open · bitget</code>
<pre>$positions_block</pre>

<b>Orders</b> <code>pending · bitget</code>
<pre>$orders_block</pre>

<b>PNL</b> <code>bitget · realized + unrealized</code>
<pre>$pnl_block</pre>

<b>Database</b>
<pre>Messages $message_count
Signals  $signal_count
Orders   $total_orders</pre>

<b>Safety</b> <code>PAPER · LIVE DISABLED · REAL ORDERS DISABLED</code>
<pre>$safety_block</pre>"

# Telegram Bot API text limit is 4096 chars; truncate with notice, never split.
if [[ "${#report}" -gt 3800 ]]; then
  report="${report:0:3700}
<i>... (truncated to fit Telegram 4096-char limit)</i>"
fi

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
