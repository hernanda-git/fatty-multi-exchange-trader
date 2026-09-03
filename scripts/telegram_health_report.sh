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

report="<b>Fatty Signal Relay</b>
<i>Paper Trading Operations Report</i>

<b>System Status</b>
Status: $overall
Mode: <code>PAPER</code>
Host: <code>fspmi-hostinger</code>
Intake started: <code>$uptime</code>

<b>Service Matrix</b>
${service_rows}
<b>Latest Telegram Signal</b>
$last_signal

<b>Trading Snapshot</b>
Balance / Equity: <i>N/A — paper ledger is not implemented</i>
Open Positions: <code>${open_positions:-0}</code>
Pending Orders: <code>${pending_orders:-0}</code>

<b>P&amp;L Summary</b>
Realized Profit: <i>N/A — no realized-P&amp;L ledger</i>
Realized Loss: <i>N/A — no realized-P&amp;L ledger</i>
Fees: <i>N/A — no fee ledger</i>
Net P&amp;L: <i>N/A — no P&amp;L ledger</i>

<b>Database</b>
Telegram Messages: <code>${message_count:-0}</code>
Canonical Signals: <code>${signal_count:-0}</code>
Orders: <code>${total_orders:-0}</code>

<b>Safety</b>
Live Execution: <code>DISABLED</code>
Real Orders: <code>DISABLED</code>"

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
