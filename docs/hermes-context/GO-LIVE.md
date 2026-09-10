# Go-live gate

Bitget is **LIVE** with a bounded canary (2026-09-08). This document is now a historical reference for the gate sequence that was completed.

## Current state

- `BITGET_EXECUTION_ENABLED=1`
- `BITGET_CANARY_MAX_ORDERS=5`
- `BITGET_APPROVAL_REFERENCE=hernanda-approved-live-20260908`
- `BITGET_MAX_CLOCK_SKEW_MS=5000`
- Kill switch: released (`hernanda-approved-live-20260908-historical-reconciled`)
- Runtime: PASS (780 contracts, 0 positions, 0 open orders)

## Gate sequence (completed)

- [x] Credentialed Bitget account read probe passes (balance + positions).
- [x] Public contract fixtures cached locally (780 contracts).
- [x] Protection + reconciliation health verified.
- [x] Kill switch tested and released after reconciliation.
- [x] Emergency reduce-only close path tested (NOTUSDT incident 2026-09-08 03:46 UTC).
- [x] Fresh Postgres backup taken.
- [x] Explicit human approval logged (2026-09-08 17:01 UTC).
- [x] `BITGET_EXECUTION_ENABLED=1` enabled.
- [x] Runtime verified PASS after cutover.

## Historical gate (before 2026-09-08)

Before the cutover, the gate required: credentialed read probe, public contract fixtures, protection/reconciliation health, tested halt and close path, fresh backup, and explicit human approval. All were satisfied before the flip.
