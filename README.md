# Fatty Multi-Exchange Trader

PAPER-first Telegram signal intake, one canonical interpretation, and isolated Binance USDⓈ-M / Bitget USDT futures dispatches.

## Safety

- No live orders by default.
- Every executable entry requires a geometrically valid stop loss.
- Venue dispatches are independent; a failure on one cannot hide the other.
- The current development runtime uses fakes only and sends no network order.

See [ARCHITECTURE.md](ARCHITECTURE.md) and [SECURITY.md](SECURITY.md).
