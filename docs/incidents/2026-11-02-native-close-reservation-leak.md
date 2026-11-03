# Native-close margin reservation reconciliation fix

**Date:** 2026-11-02  
**Incident:** native-close-reservation-leak-2026-11-02  
**Severity:** P0  

## Problem
For native Bitget TP/SL closes, the fill occurs on the venue before the bot creates the synthetic `*-native-close` intent. The verified-close reconciliation rejected fills where `filled_at < close_intent.created_at`, causing `bitget_margin_reservations` rows to remain `consumed` and block new entries with `position-cap-exceeded`.

## Fix
In `src/fatty_trader/storage/balance_reservations.py`:
- Detect native closes via `close_client_order_id.endswith("-native-close")`
- Relax close-intent timing checks for native closes (intent created after discovery)
- Allow `filled_at < close_intent.created_at` only for native closes when `filled_at >= entry.created_at` and `filled_at >= reservation.created_at` and within `observed_at` window
- All other invariants remain enforced (binding, side/trade_side, quantities, fills provenance, flat position)

## Emergency action
Released 5 orphaned consumed reservations with reason `manual-unblock:native-close-reconciliation-bug-2026-11-02`.

## Verification
- Slots freed, account flat
- Services restarted (dispatcher-bitget, monitor-bitget, source-management, console-worker, operator-bot)
- No changes to non-native close paths

## Deployment (2026-11-03)

The fix is live. Gated rollout `scripts/rollout.py --branch main` at
`2026-11-03T02:12:21Z` -> complete `2026-11-03T02:15:07Z` for revision
`2b188b2461528acec7bd61d340b17e9dd9478040` (gate overridden with an explicit
reason: 13 historical filled ENTRY intents, venue flat, 0 reservations,
0 in-flight dispatches, 0 open entry windows).

Observed readback after the rollout:

- every service image is labelled `org.opencontainers.image.revision=2b188b2...`;
- the running dispatcher's `storage/balance_reservations.py` is md5
  `c03746b7b2b3e86120a0dd5ff3f084c2` (post-fix) and contains the
  `is_native_close` branch;
- `/health` readiness ready, 10/10 containers healthy, 0 restarts;
- authenticated venue probe: equity 12.25853632 USDT, no positions, no pending
  orders; `bitget_margin_reservations` has 0 rows in reserved/unknown/consumed;
- both kill switches off; maintenance window closed with no missed signals.

Next native TP/SL close is the acceptance event: it must release its reservation
without `position-cap-exceeded` on the following signal.
