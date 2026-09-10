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

The production topology on `fspmi-hostinger` is **LIVE** on the Bitget lane with a bounded canary. `postgres` is the durable state store; `migrate` must complete before `init`, and all intake/analyzer/venue/operator services wait for `init`. Venue services are separate processes: Binance services receive only Binance credentials (currently disabled), Bitget services receive only Bitget credentials (LIVE), and the analyzer receives no exchange credentials. `WEB_HOST_PORT` defaults to `18081`.

```bash
POSTGRES_PASSWORD='use-a-local-secret-manager-value' docker compose up -d --build
curl http://127.0.0.1:18081/health/telemetry
```

The health payload is sanitized and reports LIVE mode plus configured component states. It never returns environment values or credentials.

## PostgreSQL backup and restore

Backups are custom-format dumps written below `backups/` (gitignored) and pruned by `BACKUP_RETENTION_DAYS` (default 14). Run from the repository root with the database reachable from the host:

```bash
POSTGRES_PASSWORD="$POSTGRES_PASSWORD" ./scripts/backup_postgres.sh
CONFIRM_RESTORE=YES POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  ./scripts/restore_postgres.sh backups/fatty_trader_<timestamp>.dump
```

Restore is intentionally an explicit destructive action. Stop application workers first, verify the dump filename, and keep the password in the environment or a secret manager; never commit an env file.

## Deployment boundary

Deployment to the production host, Telegram listener authorization, Codex OAuth setup, exchange metadata probes, credentials, and all exchange execution are deliberately not run from this workstation. The Bitget lane is LIVE with a bounded canary; manual operator mutations remain disabled.
