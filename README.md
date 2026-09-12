# Fatty Multi-Exchange Trader

Telegram signal intake, one canonical interpretation, and isolated Binance USDⓈ-M / Bitget USDT futures dispatches. **Bitget LIVE canary is active** (since 2026-09-08).

## What's New (2026-09-11)

- **Bot-managed TP/SL fallback** — symbols that reject native SL/SL (Bitget 43011) are now held and monitored; the bot closes when mark price hits TP/SL
- **Sizing fix** — positions now use intended allocation (20% of equity) instead of exchange minimum
- **Emergency close reconciliation** — market close intents now reconcile to `filled` immediately
- **Cron config pinning** — hourly health report pinned to current provider/model

## Safety

- Bitget execution is LIVE with a bounded canary cap (`BITGET_EXECUTION_ENABLED=1`, `BITGET_CANARY_MAX_ORDERS=5`).
- Every executable entry requires a geometrically valid stop loss.
- Venue dispatches are independent; a failure on one cannot hide the other.
- Manual operator mutations remain disabled (`BITGET_OPERATOR_MUTATIONS_ENABLED=0`).
- Symbols that reject native SL/TP (43011) are held and monitored by the bot; operator is alerted.

See [ARCHITECTURE.md](ARCHITECTURE.md), [SECURITY.md](SECURITY.md), and [docs/OPERATIONS.md](docs/OPERATIONS.md).
