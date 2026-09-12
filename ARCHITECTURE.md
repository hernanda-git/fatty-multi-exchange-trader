# Architecture

`telegram intake -> canonical analysis -> transactional dispatch fan-out -> isolated exchange workers`

The Bitget lane is **LIVE** with a bounded canary (`BITGET_CANARY_MAX_ORDERS=5`, `BITGET_MAX_CLOCK_SKEW_MS=5000`). The Binance lane remains disabled. Exchange execution adapters are real and provider-verified for Bitget USDⓈ-M Futures.

## Bot-Managed TP/SL Fallback

Some symbols reject native Bitget SL/TP placement (`43011 delegateType is error`). For these symbols:

1. Entry fills normally
2. `place-pos-tpsl` fails with 43011 → bot registers position for fallback monitoring
3. Monitor cycle polls mark price every ~30s
4. When mark price hits TP or SL threshold → market close submitted
5. Close intent reconciled to `filled` immediately

This prevents emergency-closes on otherwise valid positions. Operator is alerted via monitor report.

## Sizing

Position size is calculated from intended allocation (`equity × allocation_pct × leverage`), with exchange minimum as a safety floor only. This ensures each position uses the full 20% allocation target rather than the minimum exchange requirement.
