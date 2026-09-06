# Hermes Context Bundle

Use `HERMES-FSPMI-HOSTINGER-DEMO-CONTEXT.md` as the current operational source of truth.

## Reading order

1. `HERMES-FSPMI-HOSTINGER-DEMO-CONTEXT.md` — current remote evidence, safety rules, blockers, and next commands.
2. `FULL-REPORT-fatty-multi-exchange-trader-20260906.md` — complete historical architecture/readiness report; historical claims are labeled in the handoff.
3. `BITGET-LIVE-OPERATIONS.md` — operating runbook and lifecycle gates.
4. `EXCHANGE-CONTRACTS.md` — exchange and metadata contracts.
5. `GO-LIVE.md` and `OPERATIONS.md` — deployment/cutover/rollback context.
6. `IMPLEMENTATION-STATUS.md` — implementation status snapshot.
7. `.hermes/plans/2026-09-06_145742-bitget-demo-go-live-readiness-snapshot.md` — original detailed plan; do not treat stale historical statements as current evidence.

## Mandatory skill loading

Before touching code, deployment, provider credentials, or order lifecycle, load:

- `fatty-bitget-live`
- `crypto-auto-trader-reliability`
- `trading-bot-deploy-ops`
- `agentic-trading-bot-stewardship`
- `deployed-app-debugging`
- `project-documentation`
- `hermes-agent`

## Current short status

- DEMO credential file is installed remotely with mode `0600`.
- DEMO account read from `fspmi-hostinger`: `100 USDT`, no known position/order mutation.
- Account mode remains `crossed` + `hedge_mode`; required is `isolated` + `one_way`.
- Running remote image is older than the local `DEMO`/`LIVE` mode implementation.
- `BITGET_EXECUTION_ENABLED=0`; no LIVE cutover is authorized.
