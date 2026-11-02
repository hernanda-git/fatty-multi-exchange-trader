# ZROUSDT GET-only provider reconciliation — t_b5b8bb3f

Date (UTC): 2026-10-31T01:20Z
Task: t_b5b8bb3f
Scope: GET-only. No provider POST, no replay, no mutation, no financial change, no safety-latch change, no quota/auth reset.

## Provider position
- Symbol: ZROUSDT
- Side: long
- Quantity: 12 (available 12, locked 0)
- Avg open price: 1.70705
- Margin size: 1.02423 USDT
- Leverage: 20x
- Margin mode: isolated
- Position object takeProfit/stopLoss: null (venue stores native plans separately)
- Mark price: 1.6967
- Unrealized PnL: -0.1242
- Pending entry orders: none

## Entry identities (provider readback)
- Market leg client oid `live-bitget-ZROUSDT-df9c7dbbe78b5e5a` -> provider order `1500225875427868673`: BUY 3 @1.7072, state filled
- Limit leg client oid `live-bitget-ZROUSDT-df9c7dbbe78b5e5a-limit` -> provider order `1500225885993320449`: BUY 9 @1.707, state filled
- Fills: 2 provider fills (trade ids 1500225875645038592 for 3 @1.7072; 1500227039325323265 for 9 @1.707)

## Native protection plans (provider readback)
- TP plan provider order `1500225879321075712`, client oid `live-bitget-ZROUSDT-df9c7dbbe78b5e5a-tp`: pos_profit, trigger 1.909, planStatus live, side sell, triggerType mark_price, size 0 (full position)
- SL plan provider order `1500225879371407360`, client oid `live-bitget-ZROUSDT-df9c7dbbe78b5e5a-sl`: pos_loss, trigger 1.6605, planStatus live, side sell, triggerType mark_price, size 0 (full position)
- Both plans are live and attached by client oid/provider id. `/mix/order/detail` returns 40109 for both plan ids, which is expected for native plan orders on this endpoint.

## Durable intent reconciliation
- ENTRY market intent: state filled, provider order id matches
- ENTRY limit intent: state filled, provider order id matches
- TP intent (`...-tp`): state unknown, provider_order_id `1500225879321075712` matches live TP plan, requested_qty 3, filled_qty 0
- SL intent (`...-sl`): state unknown, provider_order_id `1500225879371407360` matches live SL plan, requested_qty 3, filled_qty 0
- `reconcile_unknown_intent` cannot settle these while the position is open and plans are live; `reconcile_unknown_intents.py` skips non-flat symbols by design. Durable states therefore remain unknown and were not mutated.

## Findings
1. Provider identities reconcile for ZROUSDT: position size 12, both entry orders filled, both native TP/SL plans live at the expected triggers (1.909 / 1.6605).
2. Native TP/SL protection is live and attached; no repair was run.
3. Durable TP/SL intent states remain `unknown` because the available GET-only reconciler requires a flat position/no pending plan and does not mark live protection plans as terminal.
4. Discrepancy/wall: durable TP/SL requested_qty is 3 while the actual bot position is 12 and venue plan size is 0 (full position). This is an accounting/representation gap requiring owner-approved investigation before any mutation. No repair, POST, or auto-correction was performed.

## Conclusion
Read-only reconciliation confirms the live ZROUSDT position and both native TP/SL plans at their expected levels. Original effect identities are preserved. No live recovery is claimed; durable TP/SL intent states remain unknown pending an owner-approved reconciliation/repair path (for example `scripts/_place_protection.py --confirm` with a valid approval reference if a missing-plan condition is later proven).
