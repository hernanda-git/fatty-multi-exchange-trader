# Operations Status

## Local Development

```bash
uv sync --all-groups
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
uv run mypy src
```

## Compose Topology

The production topology on `fspmi-hostinger` is **LIVE** on the Bitget lane with a bounded canary. `postgres` is the durable state store; `migrate` must complete before `init`, and all intake/analyzer/venue/operator services wait for `init`. Venue services are separate processes: Binance services receive only Binance credentials (currently disabled), Bitget services receive only Bitget credentials (LIVE), and the analyzer receives no exchange credentials. `WEB_HOST_PORT` defaults to `18081`.

```bash
POSTGRES_PASSWORD='use-a-local-secret-manager-value' docker compose up -d --build
curl http://127.0.0.1:18081/health/telemetry
```

The health payload is sanitized and reports LIVE mode plus configured component states. It never returns environment values or credentials.

## PostgreSQL Backup and Restore

Backups are custom-format dumps written below `backups/` (gitignored) and pruned by `BACKUP_RETENTION_DAYS` (default 14). Run from the repository root with the database reachable from the host:

```bash
POSTGRES_PASSWORD="$POSTGRES_PASSWORD" ./scripts/backup_postgres.sh
CONFIRM_RESTORE=YES POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  ./scripts/restore_postgres.sh backups/fatty_trader_<timestamp>.dump
```

Restore is intentionally an explicit destructive action. Stop application workers first, verify the dump filename, and keep the password in the environment or a secret manager; never commit an env file.

## Deployment Boundary

Deployment to the production host, Telegram listener authorization, Codex OAuth setup, exchange metadata probes, credentials, and all exchange execution are deliberately not run from this workstation. The Bitget lane is LIVE with a bounded canary; manual operator mutations remain disabled.

## Known Symbol Quirks

Some Bitget symbols reject native SL/TP placement:

- `43011` — `place-pos-tpsl` rejected → bot falls back to mark-price monitoring
- `400172` — `orders-plan-pending` rejected → bot falls back to position-field verification
- `40109` — `order-detail` rejected for filled market orders → bot catches and classifies as FILLED

For these symbols, the bot holds positions and manages TP/SL via market close when thresholds are hit. The operator is alerted via monitor report.

## Hourly Health Report

A cron job (`Bitget Hourly Health Report`) delivers a rich HTML health card to the operator's Telegram channel every 60 minutes. Includes:

- Runtime status (mode, venue, execution enabled)
- Codex usage (plan, 5h/7d windows, reset time)
- Balance (equity, available, unrealized PnL)
- Open positions (entry, mark, current price, leverage, margin, SL/TP status, unrealized PnL)
- Pending orders (symbol, side, role, qty, price, state)
- Realized PnL (fills, gross +/-, fees, net, unrealized)
- Database metrics (messages, signals, open positions, pending orders, total orders)
- Safety check (isolated mode, leverage, SL-before-liq verification)
- Latest source message (ID, timestamp, preview)
