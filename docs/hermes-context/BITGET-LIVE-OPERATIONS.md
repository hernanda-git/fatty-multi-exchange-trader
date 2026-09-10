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
- Manual operator mutations remain disabled.

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
# Immediate: disable execution
# Edit .env: BITGET_EXECUTION_ENABLED=0
docker compose up -d --force-recreate dispatcher-bitget

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
Manual operator mutations are currently disabled (`BITGET_OPERATOR_MUTATIONS_ENABLED=0`).
