# Architecture

`telegram intake -> canonical analysis -> transactional dispatch fan-out -> isolated exchange workers`

The Bitget lane is **LIVE** with a bounded canary (`BITGET_CANARY_MAX_ORDERS=5`, `BITGET_MAX_CLOCK_SKEW_MS=5000`). The Binance lane remains disabled. Exchange execution adapters are real and provider-verified for Bitget USDⓈ-M Futures.
