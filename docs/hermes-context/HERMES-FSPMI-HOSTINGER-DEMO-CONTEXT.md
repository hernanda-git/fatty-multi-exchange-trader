# Hermes Context: Fatty Bitget LIVE on `fspmi-hostinger`

**Purpose:** canonical handoff for Hermes sessions running on the deployment host.
This document contains operational context and evidence, never credentials.

**First read for new sessions:** `AGENTIC-OPS-FASTPATH.md`.

**Last verified:** 2026-09-13
**Remote host:** `fspmi-hostinger`
**Remote application:** `/home/valarion/apps/fatty-multi-exchange-trader`

## Non-negotiable safety rules

- Never print, copy into documentation, commit, or log API keys, secrets, passphrases, Telegram tokens, database passwords, or Codex auth.
- `BITGET_EXECUTION_ENABLED=1` — **LIVE canary active**. Do not disable without operator authorization.
- Do not clear the Bitget kill switch without reconciliation evidence and explicit authorization.
- Manual operator mutations remain disabled (`BITGET_OPERATOR_MUTATIONS_ENABLED=0`).
- Preserve the existing Compose project, PostgreSQL volume, data, and rollback path.
- Take a PostgreSQL backup before any deployment/rebuild/restart that changes the running stack.
- Treat the running remote image and the local worktree as separate evidence tracks.

## Current LIVE canary state

```text
TRADER_MODE=LIVE
BITGET_MODE=LIVE
BITGET_EXECUTION_ENABLED=1
BITGET_CANARY_MAX_ORDERS=5
BITGET_MAX_CLOCK_SKEW_MS=5000
BITGET_OPERATOR_MUTATIONS_ENABLED=0
BITGET_FALLBACK_MUTATIONS_ENABLED=0
```

- Repository HEAD: `74fa80ba27295c15563541856a332fbd6a4e1b0d`.
- Deployed runtime image: `5a2e2ebd82accdf29e04d1f36b811c836e2e736e`.
- Later commits contain only docs/host snapshot tooling; runtime source is unchanged.
- All expected long-running services running; healthchecked services healthy
- `migrate` and `init` completed successfully
- 787 contracts, 0 provider positions, 0 provider open orders
- Account read: equity/available `8.91215461 USDT`, unrealized PnL `0`
- Database: 0 active entry intents, 3 effective nonterminal reservations, 5 raw reservation rows
- Database: 0 queued dispatches, 0 submitting dispatches, 0 open DB positions
- Kill switch: released; read the current DB row, never copy a historical approval reference
- Monitor: `state=ok`, `reasons=none`
- Dispatcher: `mode=LIVE venue_mode=LIVE state=idle`

## Historical context

The Bitget lane was DEMO-only until 2026-09-08. The cutover to LIVE was performed after:
- Reconciling the historical NOTUSDT incident (entry + emergency close, 2026-09-08 03:46 UTC)
- Releasing the kill switch (`hernanda-approved-live-20260908-historical-reconciled`)
- Setting all four cutover gates
- Flipping `BITGET_EXECUTION_ENABLED=0` → `1`
- Verifying runtime PASS

## Production invariants to re-check after any rollout

```text
BITGET_EXECUTION_ENABLED=1
BITGET_CANARY_MAX_ORDERS=5
operator mutations=0
fallback mutations=0
PostgreSQL volume/data preserved
kill switch state unchanged
provider account/positions/orders/fills read PASS
running container source matches tested commit
```

## Documentation source map

The `docs/hermes-context/` directory on the remote host contains:
- `AGENTIC-OPS-FASTPATH.md` — first-read current source map, one-pass snapshot, and rollout contract
- this handoff
- the full project readiness report (historical)
- Bitget operations
- exchange contracts
- go-live and operations docs
- implementation status

## Related Hermes skills installed for this workspace

- `fatty-bitget-ops` — project-specific Bitget audit, deployment, safety, telemetry, and lifecycle workflow
- `fatty-bitget-ops/references/signal-audit-rollout.md` — source-to-provider audit and closed-gate rollout
- `crypto-auto-trader-reliability` — exchange filters, sizing, idempotency, protection, reconciliation
- `trading-bot-deploy-ops` — safe deployment and rollback practices
- `agentic-trading-bot-stewardship` — human-in-the-loop and no-autonomous-live rules
- `deployed-app-debugging` — remote logs, auth probes, and evidence discipline
- `project-documentation` — evidence-based documentation and secret hygiene
- `hermes-agent` — Hermes remote skill/configuration conventions

Hermes must load the relevant skill before modifying the remote deployment or execution path.
