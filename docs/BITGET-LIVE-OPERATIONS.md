# Bitget USDT-Futures Live Operations

This document captures the runbook for operating the Bitget USDT-M futures live venue
end-to-end: from credential wiring to the active bounded canary.

**Status: LIVE** (canary active since 2026-09-08)

---

## 1. Credential wiring (never commit)

Bitget live credentials are **required** and read from the server environment. They are
never stored in source control.

| Variable | Source | Notes |
|---|---|---|
| `BITGET_API_KEY` | Bitget API management | trade + read-only orders |
| `BITGET_API_SECRET` | Bitget API management | |
| `BITGET_API_PASSPHRASE` | Bitget API management | distinct from account password |
| `BITGET_MODE` | compose / env | `LIVE` |
| `TRADER_MODE` | compose / env | `LIVE` globally |

Inject via `.env` on the deployment host. After rotating any credential, recreate the
`dispatcher-bitget` container:

```bash
docker compose up -d --force-recreate dispatcher-bitget
```

Validation that credentials + product + margin mode are configured correctly:

```bash
docker compose exec dispatcher-bitget \
  /app/.venv/bin/python -m fatty_trader.service --service dispatcher-bitget --check
```

---

## 2. Live canary configuration

Current active canary values:

```text
BITGET_EXECUTION_ENABLED=1
BITGET_CANARY_MAX_ORDERS=5
BITGET_APPROVAL_REFERENCE=hernanda-approved-live-20260908
BITGET_MAX_CLOCK_SKEW_MS=5000
BITGET_OPERATOR_MUTATIONS_ENABLED=0
```

Canary constraints:
- **Bounded entries**: max 5 live orders.
- **Smallest permitted notional** (min-order amount × current price × 1.02).
- **Dynamic symbols**: any provider-listed pair passing metadata preflight.
- Manual operator (Telegram) mutations remain disabled — see the per-service note below.

### Mutation gates are per-service, not global

`BITGET_OPERATOR_MUTATIONS_ENABLED` is read by two different services with two different
intended values, so a single "disabled" claim is wrong:

| Service | Value | What it gates |
|---|---|---|
| `operator-bot` | `0` (`.env`) | Manual `/open`, `/close`, protection commands from Telegram. Closed. |
| `source-management` | `1` (hard-coded in `docker-compose.yml`) | Automatic copy-trade management from source messages: TP1 booked (reduce-only 50% then SL to entry), explicit stop moves, stop-to-entry, and source CLOSE. Intentionally open. |

### Source edits

The source edits its own posts. The **latest text-changing revision is the signal**:
older revisions are stored as `superseded-source-revision` and can never execute.
A same-text re-publication (chart/formatting only) is audit-only and does not
supersede the retained revision, so a formatting edit cannot duplicate a
management action. Legacy `edited-source-message` rows remain inert; the policy
applies to revisions ingested after this change.

### Source-management actions

| Action | Mutation | Guard |
|---|---|---|
| `TP1_BOOKED` | reduce-only half close, then native SL to entry | fresh position read, exact size, native readback |
| `TP_BOOKED` | cancel owned pending entries only | no position mutation |
| `SL_TO_ENTRY` | native SL to the position entry | single owner, native readback |
| `SL_MOVE` | native SL to the price pinned by the source message | **tighten-only**: a target that would widen the current venue stop, sit beyond the mark, or lack a provable baseline stop is refused before any POST |
| `CLOSE` | reduce-only full close | fresh position read, fill-evidence readback |

Stop instructions (`SL_MOVE`, `SL_TO_ENTRY`) use a separate 24-hour bound instead of
the close/booking window: the target price is absolute, so a late delivery is still
risk-reducing, and the tighten-only guard is what makes it safe. Close/booking
instructions keep `BITGET_MANAGEMENT_MAX_SOURCE_AGE_SECONDS` (default `1800`). Every
terminal outcome carries its reason code in `last_error` and one operator card in the
outbox.

Anyone auditing "are mutations closed?" must check the service, not just `.env`.

---

## 3. Go-live gate (completed 2026-09-08)

Full suite green, no warnings, no shortcuts:

```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check .
uv run mypy src
git diff --check
docker compose config --quiet
```

Evidence recorded before enabling mainnet:
- [x] Credentialed Bitget account read probe passes (balance + positions).
- [x] 780 public contract fixtures cached locally.
- [x] Protection + reconciliation health verified.
- [x] Kill switch tested and released after reconciliation.
- [x] Emergency reduce-only close path tested (NOTUSDT incident 2026-09-08 03:46 UTC).
- [x] Fresh Postgres backup taken.
- [x] Explicit human approval logged (2026-09-08 17:01 UTC).

---

## 4. Runtime evidence and backup

Collect the exact deployed SHA, Compose topology,
migration ledger, kill-switch state, recent monitor/dispatcher cycles, and the
sanitized authenticated GET probe:

```bash
scripts/verify_bitget_runtime.sh
scripts/backup_postgres.sh
```

Current runtime state:
- Runtime SHA: `6b34e25d496f740a15ea19802ebd4e1ec7e20a85`
- All 8 services healthy
- 780 contracts, 0 positions, 0 open orders
- Kill switch: released (`hernanda-approved-live-20260908-historical-reconciled`)
- Monitor: `state=ok`, `reasons=none`
- Dispatcher: `mode=LIVE venue_mode=LIVE state=idle`

---

## 5. Rollback

If the canary misbehaves or any gate regresses:

```bash
# Incident: use established audited kill-switch/containment procedures.
# Preserve LIVE/LIVE/1; do not disable execution or bypass protection.
bash scripts/check_production_live_policy.sh

# Full revert to prior commit
git checkout <prior_sha>
docker compose up -d --force-recreate dispatcher-bitget monitor-bitget

# Confirm
uv run pytest -q
docker compose ps
```

---

## 6. Operator commands

| Command | Effect | Confirmation required |
|---|---|---|
| `/price SYM` | Current mark price | no |
| `/balance` | Available USDT | no |
| `/positions` | Open positions | no |
| `/orders` | Pending orders | no |
| `/open SYM LONG\|SHORT margin=auto\|AMT leverage=N entry=market\|limit:PRICE sl=auto\|PRICE tp=auto\|PRICE[,..]` | New isolated position | no |
| /cancel all | Cancel all pending USDT-Futures | yes |
| /cancel SYM / order_id=ID | Cancel one | no |
| /close all | Reduce-only close every position | yes |
| /close SYM / position_id=ID | Close one | no |

All commands require a **private, non-forwarded** chat from the configured operator id.
Manual operator mutations are currently disabled for `operator-bot`
(`BITGET_OPERATOR_MUTATIONS_ENABLED=0`). The value is per-service: `source-management`
runs with `=1` on purpose so source TP1/SL/CLOSE actions are applied automatically.

## Alerting and silent-failure guards (2026-11-19)

- **Recovery alerts re-arm every six hours.** Key shape:
  `dispatcher-recovery-blocked:<issues>:<YYYY-MM-DDTHH>`. A lasting block pages the
  owner four times a day, and its card states the queued-signal count and the oldest
  wait. A position the bot does not own is a **warning**
  (`dispatcher-recovery-warning`): the bot keeps trading other symbols, and that
  symbol's entries stay refused by `active-symbol-position`.
- **Backups are watched, not assumed.** `fatty-backup.service` allows 30 minutes
  (`TimeoutStartSec=1800`). `scripts/live_watchdog.py` raises `backup-stale` when the
  newest `fatty_trader_*.dump` is older than 26 h, `backup-empty` for a zero-byte
  dump, and `backup-missing` when the directory cannot be read.
- **Vision is observable.** The analyzer logs `codex_auth=ok|expired` at startup
  (`codex login status`, offline, no tokens). Each analyzed image persists
  `telegram_messages.image_status`, and a failure emits at most one
  `vision-failed:<hour>` card per hour.
- **Re-login** — the server owns its session; never copy `auth.json` between
  machines (rotating refresh tokens make the older copy die):
  `ssh fspmi-hostinger '/home/valarion/.local/bin/codex login --device-auth'`,
  approve the printed URL, then `docker compose up -d --force-recreate analyzer`.
- **Scorecard** (read-only):
  `docker compose exec -T dispatcher-bitget /app/.venv/bin/python /app/scripts/signal_scorecard.py`
  — `SCORECARD_WINDOW` defaults to `30 days`, `SCORECARD_JSON=1` adds a summary line.
- **Selectivity is off by default.** `BITGET_POLICY_MIN_REWARD_RISK=2` rejects entries
  whose best target is below that reward:risk; `BITGET_POLICY_REQUIRE_TARGET=1`
  rejects target-less signals. Every rejection is recorded as `policy-*` so the
  scorecard can audit whether the rule earned its keep.
