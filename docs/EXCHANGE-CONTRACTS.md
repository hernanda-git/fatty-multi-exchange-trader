> **HISTORICAL DOCUMENT** — This report reflects the state before the LIVE cutover (2026-09-08). The Bitget lane is now LIVE with a bounded canary. See `FULL-REPORT-BITGET-LIVE-20260908.md` for current state.

# Exchange Contracts

## Binance USD-M Futures TESTNET public market data

`fatty_trader.exchanges.binance.public_market_data.BinanceFuturesTestnetPublicMarketData`
is a deliberately read-only adapter for `https://testnet.binancefuture.com`.

- It accepts either an injected `httpx.AsyncClient` or an injected async transport. An
  injected client remains caller-owned and is never closed by the adapter.
- `get_btcusdt_metadata()` sends only `GET /fapi/v1/exchangeInfo` and returns frozen,
  typed BTCUSDT metadata: contract type/status, precisions, lot step/minimum, and
  minimum notional.
- It accepts BTCUSDT only when it is `PERPETUAL` and `TRADING`. Missing BTCUSDT,
  missing sizing filters, or malformed/invalid fields raise `BinanceMarketDataError`;
  no partial metadata is returned.
- `get_server_time()` sends only `GET /fapi/v1/time` and returns a positive typed
  `server_time_ms`; malformed or missing `serverTime` raises `BinanceMarketDataError`.
- The adapter exposes no account, credential, signing, or order-submission operation.
  It must remain public-market-data-only until a separately reviewed execution contract
  is introduced.

## Bitget LIVE venue

`fatty_trader.config.bitget.BitgetVenueConfig` and the Bitget execution adapters form a
**LIVE-capate** venue with bounded canary controls.

- `mode` accepts `DEMO` or `LIVE`; `LIVE` is the active production mode.
- The venue is `disabled` when any required credential is missing, empty, or whitespace-only.
- Credential fields use secret-aware values and are not revealed by configuration `repr` or
  `str` output.
- Execution requires all four cutover gates: `BITGET_EXECUTION_ENABLED=1`,
  `BITGET_CANARY_MAX_ORDERS > 0`, `BITGET_APPROVAL_REFERENCE` non-empty,
  `BITGET_MAX_CLOCK_SKEW_MS > 0`.
- Native SL/TP are placed only from confirmed fill quantity; protection mismatch latches degraded.
- Emergency close uses a deterministic client OID with at-most-once submit.
