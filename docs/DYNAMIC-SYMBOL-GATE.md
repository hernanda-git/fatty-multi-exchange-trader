# Dynamic Bitget symbol gate

- Static `BITGET_CANARY_SYMBOL` was a single hardcoded canary. `@fattyfatclub` signals have unlimited pairs, so the gate must be dynamic.
- Implemented: `_bitget_dispatch_preflight` validates symbols with `^[A-Z0-9]{2,20}$` (Bitget USDT-futures contract shape) via Bitget metadata preflight; rejected symbols log and skip, no order.
- Static `BITGET_CANARY_SYMBOL` removed from service/graph construction; `docker-compose.yml` env var retained only as a no-op placeholder pending a cleaner removal review.
- Deployed: `82263f6` fast-forwarded to `main`; `fspmi-hostinger` rebuilt `dispatcher-bitget`; all services running.
- Live remains closed: `BITGET_EXECUTION_ENABLED=0`; kill switch latched; no canary symbol/approval values provided by user yet.
