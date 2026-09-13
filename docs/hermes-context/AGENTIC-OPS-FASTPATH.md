# Agentic Ops Fast Path — Fatty Bitget

**Purpose:** first-read handoff for any new Hermes/agent session operating
`fatty-multi-exchange-trader`. It replaces repeated broad reading, grep, and
search with a source map and one-pass evidence commands. It contains no
credentials and is not a substitute for live provider read-back.

**Last verified:** 2026-09-13 after the `5a2e2eb` Compose rollout.

## Non-negotiable rules

- Keep provider mutations closed while diagnosing or replacing images.
- Never print, persist, or send API keys, secrets, passphrases, Telegram tokens,
  database passwords, or Codex auth.
- Treat local source, deployed container source, database state, and provider
  state as separate evidence tracks.
- Do not replay a rejected signal, clear a kill switch, cancel an order, or submit
  a smoke order unless the user explicitly requests that exact mutation.
- Preserve unrelated dirty worktree edits. Stage only intended project paths.
- Back up PostgreSQL before any rollout that changes running containers.
- `ANALYZED`, `runtime_sha`, a heartbeat, or a green unit suite is not provider
  execution proof.

## First action in a new session

Run the one-pass snapshot through the `terminal` tool from the project root:

```bash
python3 scripts/agentic_ops_snapshot.py
```

For a named signal, add the provider symbol:

```bash
python3 scripts/agentic_ops_snapshot.py --symbol PONSUSDT
```

The snapshot reports git divergence/dirty paths, Compose state, redacted runtime
gates, the deployed canary SQL booleans, database capacity and queue counts,
authenticated Bitget probe results, account summary, and optional source-to-ledger
rows for the symbol. Read its JSON once before opening individual files.

## Current verified runtime baseline

- `HEAD == origin/main == 5a2e2ebd82accdf29e04d1f36b811c836e2e736e`.
- `TRADER_MODE=LIVE`, `BITGET_MODE=LIVE`, `BITGET_EXECUTION_ENABLED=1`.
- `BITGET_CANARY_MAX_ORDERS=5`, `BITGET_MAX_CLOCK_SKEW_MS=5000`.
- Manual operator mutations are disabled; fallback mutations are `0`.
- Authenticated Bitget probe: PASS; `787` contracts; `0` provider positions;
  `0` provider open orders; `0` provider fills in the probe response.
- Account read: equity/available `8.91215461 USDT`, unrealized PnL `0`.
- Database at last verification: `0` active entry intents, `3` effective
  nonterminal reservations, `5` raw reservation rows, `0` queued dispatches,
  `0` submitting dispatches, `0` open DB positions.
- All expected long-running services were running; healthchecked services were
  healthy; `migrate` and `init` exited successfully.
- Kill switch remained released. Do not infer its authorization reference from
  historical documents; read the current DB row through the snapshot/verifier.

## Source map — read exact paths first

Use `read_file` on these paths before using `search_files`:

- Intake and parser:
  - `src/fatty_trader/analyzer/deterministic_parser.py`
  - `src/fatty_trader/analyzer/trade_management.py`
  - `src/fatty_trader/analyzer/integration.py`
  - `src/fatty_trader/analyzer/postgres_worker.py`
- Durable dispatch and cap:
  - `src/fatty_trader/execution/bitget_dispatch_repository.py`
  - `src/fatty_trader/execution/bitget_dispatcher.py`
  - `src/fatty_trader/execution/bitget_dispatch_execution.py`
- Provider execution/read-back:
  - `src/fatty_trader/exchanges/bitget/client.py`
  - `src/fatty_trader/exchanges/bitget/async_execution.py`
  - `src/fatty_trader/exchanges/bitget/live.py`
  - `src/fatty_trader/exchanges/bitget/reconciliation.py`
- Protection and monitoring:
  - `src/fatty_trader/execution/bitget_fallback_protection.py`
  - `src/fatty_trader/execution/bitget_monitor.py`
  - `src/fatty_trader/service.py`
- Persistence:
  - `src/fatty_trader/storage/schema.py`
  - `src/fatty_trader/storage/live_intents.py`
  - `src/fatty_trader/storage/reconciliation.py`
- Runtime contract:
  - `docker-compose.yml`
  - `Dockerfile`
  - `scripts/agentic_ops_snapshot.py`
  - `scripts/backup_postgres.sh`
  - `scripts/verify_bitget_runtime.sh`

## Signal audit decision chain

For one source signal, correlate in this order:

```text
telegram_messages
  → canonical_signals
  → dispatches + dispatch_transitions
  → live_order_intents
  → provider order/fills
  → fills
  → positions + protection_states
  → reconciliation + notifications_outbox
```

Classify explicitly: no source row; analyzed/no canonical; canonical/no dispatch;
preflight/cutover/kill-switch/canary rejection; unknown provider result;
provider filled with incomplete ledger; or reconciled/flat execution.

For a signal rejected as `canary-order-cap-reached`, compare active intents,
effective nonterminal reservations, raw reservations, cap, and deployed SQL. The
old runtime query counted all reservation rows; the current query joins
`dispatches` and excludes `FILLED`, `REJECTED`, `CANCELLED`, and `RECONCILED`.

## Cap semantics

Do not conflate two controls:

1. **Position cap:** maximum simultaneous provider-backed nonzero positions.
2. **Reservation fence:** one slot for each in-flight entry whose position is not
   visible yet; `UNKNOWN` remains reserved until provider read-back completes.

Never count a terminal reservation. Never count the same dispatch once as an
intent and again as a reservation. The `max_normal_positions` policy exists in
`BitgetLiveRiskConfig`/`live_policy.py`, but the durable dispatcher must be
verified to receive an authoritative active-position count before claiming that
a global position cap is enforced.

## Safe rollout sequence

1. Run the snapshot and inspect dirty paths; preserve unrelated edits.
2. Run local verification:
   `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && git diff --check && docker compose config --quiet`.
3. Back up: `BACKUP_DIR=backups /bin/bash scripts/backup_postgres.sh`.
4. Build `migrate init analyzer dispatcher-bitget intake monitor-bitget
   operator-bot notification-sender source-management web`.
5. Run `docker compose run --rm --no-deps migrate` and then `init`.
6. Recreate long-running services with
   `BITGET_EXECUTION_ENABLED=0 docker compose up -d --no-deps --force-recreate ...`.
7. Wait for healthchecks. Read the deployed canary source inside the container;
   do not trust only local SHA or the host verifier's `runtime_sha`.
8. Re-run authenticated provider reads and check no unexpected queued,
   submitting, intent, fill, order, or position mutation occurred.
9. If execution was already enabled, restore the prior env state by recreating
   `dispatcher-bitget`, `monitor-bitget`, and `operator-bot`, then verify env and
   provider state again.
10. Restore any pre-existing local edit and report LOCAL_TESTS,
    DEPLOYED_IMAGE, SERVICE_RUNTIME, LIVE_ACCOUNT_READ, and LIVE_ORDER_SMOKE
    separately.

The detailed copy-paste workflow is in the `fatty-bitget-ops` skill reference
`references/signal-audit-rollout.md`; load it with `skill_view` only when rollout
or signal-level correlation is needed.

## Historical-document rule

Files under `docs/hermes-context/` with older dates, old runtime SHAs, or the
pre-LIVE `BITGET_EXECUTION_ENABLED=0` state are preserved historical evidence.
Use this fast path and the current handoff first; never merge historical counts
or gates into current runtime claims.
