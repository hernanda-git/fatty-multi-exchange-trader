# Operations Status

Current release acceptance: [remediation gates](remediation-verification.md).
Historical LIVE mode and configuration claims below are not current gate,
progress, account-state or protection evidence. During remediation, entry and
fallback/stream mutation remain closed; read back effective per-service gates.

## Local Development

```bash
uv sync --all-groups
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
uv run mypy src
```

## Compose Topology

The production topology on `fspmi-hostinger` is configured for the **LIVE** Bitget account. New-entry and fallback/stream mutation gates remain closed during remediation; configured account mode is not trading authorization. `postgres` is the durable state store; `migrate` must complete before `init`, and all intake/analyzer/venue/operator services wait for `init`. Venue services are separate processes: Binance services receive only Binance credentials (currently disabled), Bitget services receive only Bitget credentials (LIVE), and the analyzer receives no exchange credentials. `WEB_HOST_PORT` defaults to `18081`.

```bash
POSTGRES_PASSWORD='use-a-local-secret-manager-value' docker compose up -d --build
curl http://127.0.0.1:18081/health/telemetry
```

The health payload is sanitized. Static mode/configuration, HTTP liveness and
actual worker/dependency readiness are separate fields. Unknown, missing or
stale worker evidence is not READY. It never returns environment credentials.
The dashboard is published on loopback only and has no application login; never
expose its port through an unauthenticated external proxy. Its container probe
checks the health JSON readiness verdict, not only HTTP 200.

The runtime verifier requires running, healthy PostgreSQL, intake, analyzer,
dispatcher, monitor, operator, notification-sender, source-management and web
containers. Running without a healthcheck is not healthy evidence.

Business-event notifications use token-fenced durable claims, a ten-second total
send bound, and leases of at least fifteen seconds (default thirty). Delivery is
at-least-once across a crash after Telegram acceptance; a stale owner cannot
report a successful database acknowledgement. Terminal failed outbox rows remain
audit evidence, not proof of delivery.

## PostgreSQL Backup and Restore

Backups are custom-format dumps written below `backups/` (gitignored) and pruned by `BACKUP_RETENTION_DAYS` (default 14). Run from the repository root with the database reachable from the host:

```bash
POSTGRES_PASSWORD="$POSTGRES_PASSWORD" ./scripts/backup_postgres.sh
CONFIRM_RESTORE=YES POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  ./scripts/restore_postgres.sh backups/fatty_trader_<timestamp>.dump
```

Restore is intentionally an explicit destructive action. Stop application workers first, verify the dump filename, and keep the password in the environment or a secret manager; never commit an env file.

## Deployment Boundary

Deployment to the production host, Telegram listener authorization, Codex OAuth setup, exchange metadata probes, credentials, and all exchange execution are deliberately not run from this workstation. The Bitget lane is LIVE with a bounded canary; manual operator (Telegram) mutations remain disabled for `operator-bot`, while `source-management` deliberately runs with its mutation gate enabled so source TP1/SL/CLOSE actions are applied automatically (gates are per-service — see `BITGET-LIVE-OPERATIONS.md`).

## Known Symbol Quirks

Some Bitget symbols reject native SL/TP placement:

- `43011` — parameter validation is FAILED/DEGRADED, not native-unsupported
  capability. Omitted market execute-price fields are a documented compatibility
  option only after strict validation; never use blind retry or positive limits.
- `400172` — pending-plan reads may be unavailable; only independently sufficient,
  exact provider evidence may establish protection. Otherwise report UNKNOWN.
- `40109` — a missing order detail requires matching order/fill reconciliation.
  It is not by itself proof of FILLED and must not cause an entry replay.

Fallback registration alone is not enforcing loss protection. Require explicit
mutation gate, verified owned position epoch/environment, fresh mark evidence,
atomic close identity and provider fill/readback. Malformed/failed reads cannot
prove flatness; submission alone cannot prove a filled close.

## Six-hour Health Report

The `fatty-health-report.timer` systemd unit runs `scripts/health_report.py` every six hours, with a persistent timer and a bounded service timeout. It delivers a provider-first HTML health card directly to the operator's Telegram channel. This periodic direct-send report is separate from the durable trading-event notification outbox. Includes:

- Runtime status (mode, venue, execution enabled)
- Codex usage (plan, 5h/7d windows, reset time)
- Balance (equity, available, unrealized PnL)
- Open positions (entry, mark, current price, leverage, margin, SL/TP status, unrealized PnL)
- Pending orders (symbol, side, role, qty, price, state)
- Realized PnL (fills, gross +/-, fees, net, unrealized)
- Database metrics (messages, signals, open positions, pending orders, total orders)
- Safety check (isolated mode, leverage, SL-before-liq verification)
- Latest source message (ID, timestamp, preview)
