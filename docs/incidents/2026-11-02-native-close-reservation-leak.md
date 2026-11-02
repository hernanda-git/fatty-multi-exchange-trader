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

