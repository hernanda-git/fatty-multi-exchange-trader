# Implementation Status

## Implemented in `b3ff0e9`

- DEMO-only Python package and locked development toolchain.
- Immutable canonical signal geometry validation and durable dispatch transition guard.
- Decimal minimum-notional sizing with leverage-first escalation, margin caps, headroom, and rounded-exposure recheck.
- Fail-closed deterministic text fallback and one-signal/two-independent-venue in-memory fan-out model.
- Exact operator-ID/private-chat authorization, strict manual-trade grammar, and read-only DEMO dashboard health endpoint.
- Portable Compose topology with PostgreSQL bind mount and loopback dashboard; no named volumes.
- Literal Codex capability probe and explicit current blocker documentation.

## Not implemented yet

PostgreSQL/Alembic persistence, Telethon intake, literal Codex queue worker, exchange REST/WebSocket adapters, fill/protection/reconciliation workers, Telegram poller/commands, dashboard projections/UI, host deployment, DEMO soak, and every LIVE gate remain unimplemented. No exchange request or live/DEMO order has been attempted.

## Verified locally

`uv run pytest -q` → 12 passed; `ruff format --check`, `ruff check`, `mypy src`, `docker compose config --quiet`, and `git diff --check` passed before commit.

## Container and venue verification update (2026-09-03)

- Docker Desktop Linux engine is running; `docker build -t fatty-multi-exchange-trader:local .` passed and a direct container `/health` smoke test returned the expected DEMO-only payload.
- Compose schema validation passed and the `postgres` service reached `healthy`.
- The Compose `web` service now binds configurable `WEB_HOST_PORT` (default `18081`). PostgreSQL is healthy, web is running, and `http://127.0.0.1:18081/health` returned HTTP 200 with the expected DEMO-only payload.
- Binance Futures testnet public clock and BTCUSDT `TRADING`/`PERPETUAL` metadata probes passed. No credentials and no order endpoint were used.
- Codex Phase-0 proof remains blocked because `codex` is absent from PATH.
