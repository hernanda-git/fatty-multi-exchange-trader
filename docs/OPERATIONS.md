# Operations status

## Local development

```bash
uv sync --all-groups
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
uv run mypy src
```

## Compose topology

The local topology is PAPER-only and binds the web service to loopback. `postgres` is the durable state store; `migrate` must complete before `init`, and all intake/analyzer/venue/operator services wait for `init`. Venue services are separate processes: Binance services receive only Binance credentials, Bitget services receive only Bitget credentials, and the analyzer receives no exchange credentials. `WEB_HOST_PORT` defaults to `18081`.

```bash
POSTGRES_PASSWORD='use-a-local-secret-manager-value' docker compose up -d --build
curl http://127.0.0.1:18081/health/telemetry
```

The health payload is sanitized and reports PAPER mode plus configured component states. It never returns environment values or credentials. The worker commands are deliberately queue-safe stubs until their domain consumers are implemented; they provide independent restart/health boundaries without placing orders.

## PostgreSQL backup and restore

Backups are custom-format dumps written below `data/backups` (gitignored) and pruned by `BACKUP_RETENTION_DAYS` (default 14). Run from the repository root with the database reachable from the host:

```bash
POSTGRES_PASSWORD="$POSTGRES_PASSWORD" ./scripts/backup_postgres.sh
CONFIRM_RESTORE=YES POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  ./scripts/restore_postgres.sh data/backups/fatty_trader_<timestamp>.dump
```

Restore is intentionally an explicit destructive action. Stop application workers first, verify the dump filename, and keep the password in the environment or a secret manager; never commit an env file.

## Deployment boundary

Deployment to the production host, Telegram listener authorization, Codex OAuth setup, exchange metadata probes, credentials, and all exchange/PAPER execution are deliberately not run from this workstation. A live order is explicitly out of scope until the independent go-live gates in the implementation plan are satisfied and a human operator approves a venue.
