# Full Report — Dynamic Bitget symbol gate

## What changed
- Replaced static `BITGET_CANARY_SYMBOL` gate with dynamic symbol-verified live dispatch.
- `_bitget_dispatch_preflight` now validates symbols with `^[A-Z0-9]{2,20}$` (Bitget USDT-futures contract shape) via Bitget metadata preflight; rejected symbols log and skip, no order.
- Static single-symbol gate removed from service/graph construction; dispatch now accepts any validated symbol, so `@fattyfatclub` unlimited-pair signals can trigger orders.
- `docker-compose.yml` env var retained only as a no-op placeholder pending a cleaner removal review (not consumed by service construction).

## Files changed
- `src/fatty_trader/service.py`: 12 lines changed (6 insertions, 6 deletions)
  - `_validate_bitget_cutover`: stricter `BITGET_CANARY_SYMBOL` validation (`^[A-Z0-9]{2,20}$`) only when execution is explicitly enabled; trimmed, non-empty, uppercase shape.
  - `_bitget_dispatch_preflight`: removed hardcoded `canary_symbol` equality check; replaced with symbol-shape validation before Bitget preflight.
- `docs/DYNAMIC-SYMBOL-GATE.md`: new decision record.

## Verification
- `uv run pytest -q` → 268 passed
- `uv run ruff check src tests scripts` → clean
- `uv run ruff format --check .` → clean
- `uv run mypy src` → clean on 60 files
- `git diff --check` → clean

## Deployed host state
- `fspmi-hostinger` repo at `~/apps/fatty-multi-exchange-trader`
- Deployed SHA: `82263f6` (dynamic Bitget symbol gate) plus docs commit `47485c2`
- Services: `analyzer dispatcher-bitget intake monitor-bitget notification-sender operator-bot postgres web` — all running
- `docker-compose.yml` diff against base: no runtime-change diff (env var retained as placeholder only)
- Live guard remains closed: `BITGET_EXECUTION_ENABLED=0`; kill switch latched; no canary symbol/approval values provided; no orders submitted
