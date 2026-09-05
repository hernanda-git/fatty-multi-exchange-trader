# Bitget USDT-Futures Live Operations

This document captures the runbook for operating the Bitget USDT-M futures live venue
end-to-end: from credential wiring to a bounded first-production canary.

---

## 1. Credential wiring (never commit)

Bitget live credentials are **required** and read from the server environment. They are
never stored in source control.

| Variable | Source | Notes |
|---|---|---|
| `BITGET_API_KEY` | Bitget API management | readonly + trade + read-only orders initially |
| `BITGET_API_SECRET` | Bitget API management | |
| `BITGET_API_PASSPHRASE` | Bitget API management | distinct from account password |
| `BITGET_MODE` | compose / env | must be `LIVE` for the Bitget lane |
| `TRADER_MODE` | compose / env | global flag — **remains `PAPER`** for the rest of the topology |

Inject via `.env` on the deployment host **or** via the orchestrator secret mechanism
(`fly secret set` / compose `environment:`). After rotating any credential, restart the
`dispatcher-bitget` container only.

### Runtime boundary (current implementation)

`TRADER_MODE` is the global topology mode and remains `PAPER`. The Bitget lane
has a separate `BITGET_MODE` value, so `BITGET_MODE=LIVE` identifies the
intended venue without promoting Binance, intake, analyzer, or operator
services. Until the real dispatcher and monitor lifecycle is wired and its
preflight gates pass, the Bitget workers must report `state=heartbeat-only` and
must not submit provider mutations.

Validation that credentials + product + margin mode are configured correctly:

```bash
docker compose exec dispatcher-bitget \
  /app/.venv/bin/python -m fatty_trader.service --service dispatcher-bitget --check
```

---

## 2. Demo / testnet end-to-end verification

Before touching mainnet, run the full lifecycle against Bitget demo endpoints with the
smallest permitted notional. The demo client is protocol-injected — no network required —
and walks:

1. Account identity, product type (`USDT-FUTURES`), margin coin (`USDT`), isolated mode,
   and position mode (one-way / hedge).
2. Public symbol metadata + current price.
3. `/price` and `/balance` operator commands.
4. One tiny **market** entry with explicit SL/TP, with read-back of:
   - provider order id
   - fill qty + average price + fee
   - protection SL/TP orders
   - resulting position
   - liquidation estimate
5. Pending limit order → `/cancel` → read-back of zero pending.
6. `/close` → read-back of zero position.
7. Invalid symbol, bad direction, and insufficient-margin inputs — all rejected with an
   alert (never a silent skip).
8. Every action produces exactly one alert; no alert ever contains a secret, signature,
   or raw auth header.

```bash
uv run pytest tests/e2e/test_bitget_demo_live_cycle.py -v
```

---

## 3. Go-live gate (run before the first mainnet order)

Full suite green, no warnings, no shortcuts:

```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check .
uv run mypy src
git diff --check
docker compose config --quiet
```

Execution is closed unless **all** server-side gates are intentionally set:
`BITGET_EXECUTION_ENABLED=1`, a positive `BITGET_CANARY_MAX_ORDERS`, one
uppercase `BITGET_CANARY_SYMBOL`, a non-secret `BITGET_APPROVAL_REFERENCE`, and
a positive `BITGET_MAX_CLOCK_SKEW_MS`. These values are deployment metadata, not
credentials; no approval token belongs in tracked configuration.

Evidence to record **before** enabling mainnet:

- [ ] Credentialed Bitget account read probe passes (balance + positions).
- [ ] Public contract fixtures for the canary symbol cached locally.
- [ ] Protection + reconciliation health verified in demo.
- [ ] Kill switch tested (stale data / wrong margin mode / missing protection).
- [ ] Emergency reduce-only close path tested.
- [ ] Fresh Postgres backup taken (`scripts/backup_postgres.sh`).
- [ ] Explicit human approval logged with timestamp.

### Runtime evidence and backup

With execution still disabled, collect the exact deployed SHA, Compose topology,
migration ledger, kill-switch state, recent monitor/dispatcher cycles, and the
sanitized authenticated GET probe:

```bash
scripts/verify_bitget_runtime.sh
scripts/backup_postgres.sh
```

The backup command runs `pg_dump` inside the Compose PostgreSQL service, writes a
timestamped custom dump outside Git, rejects an empty dump, and prints a restore
command. Keep the reported path with the rollout record; do not paste secrets into
terminal history or reports.

### Canary constraints

- **One venue, one symbol** (default `BTCUSDT`).
- **Smallest permitted notional** (min-order amount × current price × 1.02).
- **Ten signals only** — no auto-scaling, no all-in fallback.
- Verify the complete lifecycle (entry → protect → reconcile → close) before allowing
  additional signals.

---

## 4. Cutover

Performed as a **separate explicit action** once the gate above is green.

```bash
# 1. Confirm current state
git log -1 --format='%H %s'
docker compose ps

# 2. Record rollback command for this cutover
echo "ROLLBACK: git checkout <prior_sha> && docker compose up -d --build dispatcher-bitget"

# 3. Enable live config on the server (example with Fly secrets)
fly secrets set BITGET_API_KEY=... BITGET_API_SECRET=... BITGET_API_PASSPHRASE=...

# 4. Restart only the Bitget lane
docker compose up -d --build dispatcher-bitget monitor-bitget

# 5. Verify
docker compose logs -f dispatcher-bitget
```

Record: deployed commit SHA, timestamp, operator, rollback command.

---

## 5. Rollback

If the canary misbehaves or any gate regresses:

```bash
# Immediate: kill switch via operator bot
# /close all        ← confirms with a token
# /cancel all       ← confirms with a token
# /positions        ← verify zero

# Full revert to prior commit
git checkout <prior_sha>
docker compose up -d --build dispatcher-bitget monitor-bitget

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
