# Implementation status

This file describes repository state, not the state of a running provider account.
A deployment claim requires the post-deploy evidence in
[`BITGET-PROTECTION-OPERATIONS.md`](BITGET-PROTECTION-OPERATIONS.md).

## Completed in this hardening branch

- Native Bitget Classic V2 position SL/TP request contract and strict provider
  read-back validation.
- Confirmed filled-quantity protection sizing, provider response-array
  normalization, and preserved plan IDs/client OIDs.
- Symbol/environment-local protection capability records and admission policy.
- Additive, idempotent migration 11 for protection capabilities.
- Classic Bitget WebSocket login, mark-price/private event normalization,
  per-symbol freshness, text heartbeat, reconnect/resubscribe, and stale state.
- Observe-only stream runtime and REST watchdog with provider-first,
  symbol-local fail-closed checks.
- Atomic entry and fallback-close intent claims; deterministic close identity;
  reduce-only quantity clamping; unknown-result reconciliation.
- Liquidation buffer policy with direction, gap, tick, and latency/slippage
  allowances.
- Provider-only/system liquidation normalization and deduplication by provider
  fill ID.
- Additive, idempotent migration 12 for provider reconciliation events.
- Service/Compose wiring with closed-by-default execution, capability, stream,
  fallback, and operator mutation gates.
- Full repository documentation for architecture, contracts, flags, migrations,
  deployment, rollback, incident response, and canary acceptance.

## Explicit limitations

- The production WebSocket endpoint/channel compatibility has offline coverage
  only; no live connection is enabled by this code deployment.
- The stream threshold engine has a tested close callback seam, but the service
  runtime remains observe-only until fallback-close wiring is independently
  authorized and verified.
- No LIVE canary, provider order/plan mutation, cancel, close, or historical WLD
  replay is part of this implementation/deployment.
- Migration 11/12 deployment state must be read from the target database; local
  migration SQL is not proof of production schema state.
- Local tests and mocked fixtures do not prove provider account state or live
  read-back.

## Local verification battery

The required commands are documented in the operations runbook. Do not replace
full-suite/static output with a previous checkpoint:

```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run python -m compileall -q src tests
git diff --check
docker compose config --quiet
```

## Deployment state

- Branch: `feat/bitget-protection-ws-hardening`
- Deployment: pending controlled rollout and post-deploy read-only verification.
- Provider mutation: not authorized by this document.
- Runtime gates: preserve the target's existing approved state; keep all new
  protection mutation flags disabled unless separately approved.
