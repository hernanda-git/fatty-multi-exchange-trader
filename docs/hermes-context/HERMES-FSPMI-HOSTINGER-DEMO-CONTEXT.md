# Hermes Context: Fatty Bitget LIVE on `fspmi-hostinger`

**Purpose:** canonical handoff for Hermes sessions running on the deployment host.
This document contains operational context and evidence, never credentials.

**Last verified:** 2026-09-08
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
BITGET_EXECUTION_ENABLED=1
BITGET_CANARY_MAX_ORDERS=5
BITGET_APPROVAL_REFERENCE=hernanda-approved-live-20260908
BITGET_MAX_CLOCK_SKEW_MS=5000
BITGET_OPERATOR_MUTATIONS_ENABLED=0
```

- Runtime SHA: `6b34e25d496f740a15ea19802ebd4e1ec7e20a85`
- All 8 services healthy
- 780 contracts, 0 positions, 0 open orders
- Kill switch: released (`hernanda-approved-live-20260908-historical-reconciled`)
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
live order intents=3 (all terminal: filled, reconciled, rejected)
production .env unchanged except execution flag
PostgreSQL volume/data preserved
kill switch released
Telegram heartbeat remains 21600 seconds
```

## Documentation source map

The `docs/hermes-context/` directory on the remote host contains snapshots of:
- this handoff
- the full project readiness report (historical)
- Bitget operations
- exchange contracts
- go-live and operations docs
- implementation status

## Related Hermes skills installed for this workspace

- `fatty-bitget-live` — project-specific Bitget deployment, safety, telemetry, and lifecycle workflow (UPDATED for LIVE)
- `crypto-auto-trader-reliability` — exchange filters, sizing, idempotency, protection, reconciliation
- `trading-bot-deploy-ops` — safe deployment and rollback practices
- `agentic-trading-bot-stewardship` — human-in-the-loop and no-autonomous-live rules
- `deployed-app-debugging` — remote logs, auth probes, and evidence discipline
- `project-documentation` — evidence-based documentation and secret hygiene
- `hermes-agent` — Hermes remote skill/configuration conventions

Hermes must load the relevant skill before modifying the remote deployment or execution path.
