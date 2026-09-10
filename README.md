# Fatty Multi-Exchange Trader

Telegram signal intake, one canonical interpretation, and isolated Binance USDⓈ-M / Bitget USDT futures dispatches. **Bitget LIVE canary is active** (2026-09-08).

## Safety

- Bitget execution is LIVE with a bounded canary cap (`BITGET_EXECUTION_ENABLED=1`, `BITGET_CANARY_MAX_ORDERS=5`).
- Every executable entry requires a geometrically valid stop loss.
- Venue dispatches are independent; a failure on one cannot hide the other.
- Manual operator mutations remain disabled (`BITGET_OPERATOR_MUTATIONS_ENABLED=0`).

See [ARCHITECTURE.md](ARCHITECTURE.md) and [SECURITY.md](SECURITY.md).
