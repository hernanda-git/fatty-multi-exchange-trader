# Hermes Context Bundle

Use `AGENTIC-OPS-FASTPATH.md` as the first-read current operational source of truth, then use the handoff for current evidence.

## Reading order

1. `AGENTIC-OPS-FASTPATH.md` — one-pass snapshot command, source map, cap semantics, and rollout contract.
2. `HERMES-FSPMI-HOSTINGER-DEMO-CONTEXT.md` — current remote evidence, safety rules, blockers, and next commands.
3. `BITGET-LIVE-OPERATIONS.md` — historical cutover runbook; current values require fastpath verification.
4. `EXCHANGE-CONTRACTS.md` — exchange and metadata contracts.
5. `GO-LIVE.md` and `OPERATIONS.md` — historical deployment/cutover/rollback context.
6. `IMPLEMENTATION-STATUS.md` — historical implementation snapshot.
7. `.hermes/plans/2026-09-06_145742-bitget-demo-go-live-readiness-snapshot.md` — original plan; never treat its runtime claims as current evidence.
8. `HERMES-FRESH-SESSION-PROMPT-FSPMI-HOSTINGER.md` — historical pre-LIVE DEMO prompt; do not use its execution gate or account claims.

## Mandatory skill loading

Before touching code, deployment, provider credentials, or order lifecycle, load:

- `fatty-bitget-ops`
- `crypto-auto-trader-reliability`
- `trading-bot-deploy-ops`
- `agentic-trading-bot-stewardship`
- `deployed-app-debugging`
- `project-documentation`
- `hermes-agent`

## Current short status

- **Bitget LIVE canary active**; last verified 2026-09-13.
- `TRADER_MODE=LIVE`, `BITGET_MODE=LIVE`, `BITGET_EXECUTION_ENABLED=1`.
- `BITGET_CANARY_MAX_ORDERS=5`, clock-skew limit `5000ms`.
- Repository HEAD and origin are expected to match; deployed runtime image: `5a2e2ebd82accdf29e04d1f36b811c836e2e736e`.
- Runtime probe: PASS (787 contracts, 0 positions, 0 open orders).
- Account: equity/available `8.91215461 USDT`, unrealized PnL `0`.
- Manual operator and fallback mutations disabled.
- Effective reservations: `3/5`; raw historical reservations: `5`.
- Kill switch state: released; verify current DB row before relying on it.
