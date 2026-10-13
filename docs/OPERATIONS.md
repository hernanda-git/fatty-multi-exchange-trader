# Operations Status

Current execution and progress: [Bitget reliability remediation](REMEDIATION-PLAN-20261013.md).
Earlier release acceptance is recorded in [historical remediation gates](remediation-verification.md).
Source verification, deployed readiness, and live lifecycle evidence are separate.
Read effective per-service gates; documentation is not authorization to change them.

## Local Development

```bash
uv sync --all-groups
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
uv run mypy src
```

## Compose Topology

The production topology on `fspmi-hostinger` requires `TRADER_MODE=LIVE`, `BITGET_MODE=LIVE`, and `BITGET_EXECUTION_ENABLED=1` under the [production policy](PRODUCTION-LIVE-POLICY.md). Admission remains independently fail-closed behind safety latches, source freshness, ownership, and risk constraints. `postgres` is the durable state store; `migrate` must complete before `init`, and application services wait for `init`. Binance services are currently disabled; Bitget services receive Bitget credentials, while the analyzer receives no exchange credentials. `WEB_HOST_PORT` defaults to `18081`.

```bash
bash scripts/check_production_live_policy.sh
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

Local implementation and offline verification do not authorize production deployment, consumer shutdown, Telegram sends, safety-latch release, or account mutations. Obtain the applicable scoped approval before those operations. Preserve LIVE/LIVE/1 during maintenance; do not replace containment with DEMO or execution=0. Operator, source-management, fallback, and stream mutation gates are independent, so inspect effective per-service values rather than inferring them from execution being enabled. Protect existing exposure and preserve close recovery throughout approved maintenance.

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

The audited deployment uses the enabled user-level `fatty-health-report.timer`, invoking `scripts/health_report.py` every six hours with a persistent timer and bounded service timeout. Inspect it with `systemctl --user status fatty-health-report.timer fatty-health-report.service`; a missing system-level unit does not mean the user timer is absent. Each delivered card is an immutable point-in-time snapshot. The cron report and Telegram `/health` share normalized core ledger loading and one renderer; host-only service/Codex probes remain explicitly unprobed in the operator container. The legacy DB-only shell report is removed from the local source. This periodic direct-send report is separate from the durable trading-event notification outbox. Includes:

- Runtime status (mode, venue, execution enabled)
- Codex usage (plan, 5h/7d windows, reset time)
- Balance (equity, available, unrealized PnL)
- Open positions (entry, mark, current price, leverage, margin, SL/TP status, unrealized PnL)
- Pending orders (symbol, side, role, qty, price, state)
- Realized PnL (fills, gross +/-, fees, net, unrealized)
- Database metrics (messages, signals, open positions, pending orders, total orders)
- Safety check (isolated mode, leverage, SL-before-liq verification)
- Latest source message (ID, timestamp, preview)
- Account source, environment, futures product/margin coin, and successful-read observation time
- Last admission balance evidence capture time/age, independently of current provider account data
- Historical ledger PnL/fees and reservations; unavailable sections remain UNKNOWN
- Durable latch scope/reason/timestamps, distinct from current monitor/stream observations and approved recovery proof

For no-send inspection, use `scripts/health_report.py`'s `load_report_data()` with the canonical formatter; invoking the script's main sends a Telegram report. Reporting neither refreshes nor overwrites immutable admission snapshots. Fallback rows/native levels indicate registrations, not proof of worker enforcement or confirmed protection execution.

## Sizing, contract eligibility, and clock evidence

The service selects a route once per preflight observation and carries the split prices into live sizing. Exact 25/75 sizing uses a total quantum of four venue quantity steps; full-market sizing retains one step. Each child must satisfy its own planned-price minimum notional/quantity and market or LIMIT maximum. Final admitted margin/notional are recomputed after quantization under the existing 1 USDT production cap. An infeasible split is rejected before reservation; no approximate split or full-market fallback is invented.

Contract metadata is resolved before symbol account/position reads and margin preparation. A missing contract is unsupported; a failed/malformed contract lookup is provider-unavailable. `AsyncBitgetVenue.preflight()` can prepare margin mode for supported contracts and is not a GET-only diagnostic tool.

Clock offset uses a single-attempt request midpoint, with monotonic duration checks. A sample requires RTT at most 1000 ms and wall/monotonic elapsed disagreement at most 50 ms. Slow, failed, invalid, or wall-clock-jump samples are unavailable evidence, never zero skew or fabricated clock drift. General GET retries and POST ambiguity rules are unchanged. Existing monitor/preflight clock limits remain unchanged.

The protection stream's synchronous symbol lookup (including lazy iteration) and ordered capability persistence run off the shared asyncio loop. Database work must not postpone socket heartbeat handling or an in-flight clock response. Each standalone PostgreSQL capability read/write closes its owned connection on success and failure. Active fallback reads rely on the supported migration prerequisite instead of repeatedly running schema DDL. These scheduling/resource changes do not widen clock bounds, weaken native/fallback identity checks, or clear admission latches automatically.

## Approved flat-baseline latch recovery

Use `scripts/recover_bitget_live_latches.py` only under its existing scoped approval and flat-baseline contract. All account mutation consumers must remain stopped throughout proof and apply; a venue admission latch alone does not prove fallback/source/operator mutations are stopped. Do not stop exposure protection to run a flat-account recovery on a nonflat account.

The proof requires full authenticated account UID/baseline binding, eligible exact latch reasons, ready ledger, fresh flat inventory across product families, authenticated public/private V2 stream and both-leg pongs. Review dry-run `recovery_digest` before apply; changed rows, global/unknown reasons, stale proof, or unresolved ownership refuse release. Resume consumers safely, read back released rows, and run the full runtime verifier. Reporting/deployment checks never clear latches. See the remediation plan for ordered deployment/recovery/rollback gates; never restore an old database backup automatically after provider mutations.

The default exact venue reason remains `clock-skew-exceeded`. An owner who explicitly approves recovery of `clock-skew-unavailable` must pass `--clock-latch-reason clock-skew-unavailable` on both dry-run and apply. That mode additionally requires a successful fresh bounded midpoint clock sample within the unchanged production `BITGET_MAX_CLOCK_SKEW_MS` limit (monitor default 10000 ms). The selected reason and limit are digest-bound; changing either invalidates apply. Clock evidence is recorded in the returned proof and release audit. Failed/unreliable current clock evidence, other venue reasons, and global/unknown latches still refuse release. This option neither disables ongoing fault detection nor adds automatic recovery.

## Close ownership and transaction lifetime

Verified close release validates the requested close identity, state, timestamps, and exact provider fill economics before calculating residual exposure. The residual calculation uses the caller's existing locked transaction; it must not create independent readers while the release/binding transaction holds ownership locks.

`OwnedQuantityEvidenceError` denotes contradictory durable ownership/fill evidence. Release returns a rejected admission for that explicit evidence failure; new close binding refuses it without recording permission to mutate. SQL/connection failures are not converted to evidence rejection: they roll back and propagate. Reservations and fill economics remain unchanged on rejection or transaction failure.

Standalone `PostgresLiveIntentStore.get()` and `remaining_owned_quantity()` close the read connection on success, absent records, decoding/validation errors, and SQL failures. A cursor supplied to residual calculation remains caller-owned: the helper does not commit, roll back, or close its transaction. Full-suite regression evidence and real-PostgreSQL smoke results are recorded in the remediation tracker.
