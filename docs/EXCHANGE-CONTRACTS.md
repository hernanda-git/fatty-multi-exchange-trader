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

## Bitget PAPER venue skeleton

`fatty_trader.config.bitget.BitgetVenueConfig` and
`fatty_trader.exchanges.bitget.paper.BitgetPaperAdapter` form a strictly fail-closed
PAPER-only selection boundary.

- `mode` accepts only `PAPER`; `LIVE` is rejected during configuration validation.
- The venue is `disabled` when any required credential is missing, empty, or whitespace-only.
  It becomes `paper_ready` only when API key, API secret, and passphrase are all non-blank.
- Credential fields use secret-aware values and are not revealed by configuration `repr` or
  `str` output.
- The adapter exposes only availability state. It has no HTTP client, API request, signing,
  account, or order-submission operation. Adding any such capability requires a separately
  reviewed execution contract.
