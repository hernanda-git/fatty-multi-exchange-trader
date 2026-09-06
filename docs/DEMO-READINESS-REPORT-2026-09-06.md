# Bitget DEMO Readiness Report

**Verified:** 2026-09-06 UTC  
**Host:** `fspmi-hostinger`  
**Application:** `/home/valarion/apps/fatty-multi-exchange-trader`  
**Deployed source:** `ec987674d086363f741ebce32739ec62faf4c688`

## Executive status

The Compose stack is healthy and is operating against the funded Bitget DEMO environment. The read-only provider probe, account-mode gate, Codex container text probe, and Codex container image probe pass.

DEMO order submission is intentionally still closed. `BITGET_EXECUTION_ENABLED=0` is a closed-cutover gate, and the persistent Bitget kill switch remains latched with reason `provider-orders-invalid`. No intent, position, or pending notification is present. This is not a LIVE cutover.

## Running topology

| Service | State |
|---|---|
| postgres | healthy |
| migrate | completed successfully |
| init | completed successfully |
| web | healthy |
| intake | healthy |
| analyzer | healthy |
| dispatcher-bitget | healthy |
| monitor-bitget | healthy |
| operator-bot | healthy |
| notification-sender | healthy |

## DEMO environment evidence

```text
BITGET_MODE=DEMO
BITGET_EXECUTION_ENABLED=0
BITGET_CANARY_MAX_ORDERS=0
```

Authenticated read-back:

```text
available=100
usdtEquity=100
marginMode=isolated
posMode=one_way_mode
positions=0
open_orders=0
```

The deployed-image probe passed all read-only checks:

```text
account=PASS
contracts=PASS
fills=PASS
open_orders=PASS
positions=PASS, count=0
server_time=PASS
```

## Why `BITGET_EXECUTION_ENABLED=0`

The variable is the explicit provider-mutation gate. At `0`, the service may run, connect to PostgreSQL, read DEMO account state, resolve contracts, inspect orders/fills/positions, and reconcile state, but it must not submit a provider order.

It is intentionally distinct from `BITGET_MODE=DEMO`:

- `BITGET_MODE=DEMO` selects Bitget DEMO credentials and private-request behavior.
- `BITGET_EXECUTION_ENABLED=0` prevents orders even in DEMO.
- A future bounded DEMO lifecycle must set the execution gate, a positive canary maximum, a valid canary symbol, a non-secret approval reference, and a positive clock-skew limit.

The gate stays closed because the database kill switch is active:

```text
scope=bitget
active=true
reason=provider-orders-invalid
```

Clearing it without reconciling its original provider-order mismatch would bypass the system's safety boundary. Enabling the execution variable while the latch is active would not constitute a valid lifecycle test.

## Codex analyzer evidence

The analyzer image now includes `@openai/codex@0.153.0`. It uses a read-only bind mount for the host authentication file and a separate ignored writable runtime directory for Codex cache/state.

```text
container login status: PASS
controlled text probe: PASS
controlled image probe: PASS
```

No OAuth, tokens, cookies, or auth-file contents were copied into Git, this report, or the repository runtime directory.

## Deployment lineage

| Commit | Change |
|---|---|
| `33993b9` | Inject Bitget DEMO mode into dispatcher instead of hardcoding LIVE. |
| `a029af5` | Add Codex CLI to analyzer image. |
| `8289f5d` | Allow Codex execution outside a Git checkout. |
| `ec98767` | Separate writable Codex runtime state from read-only auth. |

All commits are pushed to `origin/main`.

## Verification gates

```text
pytest: 275 passed
ruff check: passed
ruff format --check: passed
mypy: no issues in 60 source files
Compose config: passed
PostgreSQL schema migrations: 1,2,3,4,5
```

## Persistence, rollback, and telemetry

Latest verified PostgreSQL backup:

```text
backups/fatty_trader_20260906T165912Z.dump
bytes=35,553
restore=CONFIRM_RESTORE=YES scripts/restore_postgres.sh backups/fatty_trader_20260906T165912Z.dump
```

Current durable state:

```text
live_order_intents=0
positions=0
notifications_pending=0
```

## Remaining required work before DEMO order simulation

1. Read the relevant reconciliation and order-history records to establish why `provider-orders-invalid` latched.
2. Reconcile Bitget DEMO account, positions, open orders, plan orders, and fills against local state.
3. Use the authorized recovery mechanism only after that evidence is consistent; do not directly alter the database latch.
4. Set a bounded DEMO canary configuration and execute one lifecycle at a time: intent persistence, smallest permitted entry, provider/fill read-back, native protection, reconciliation, cancel, reduce-only close, and zero-residual verification.
5. Verify durable Telegram outbox delivery for lifecycle events.

## Safety boundary

No LIVE execution was enabled. No DEMO order was submitted during this rollout. The report does not treat healthy services or a green read-only probe as proof that the bounded DEMO mutation lifecycle is complete.
