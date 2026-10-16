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
# Existing database must be running; keep .env private.
PYTHONPATH=src python scripts/provision_worker_database.py --confirm
docker compose up -d --build
curl http://127.0.0.1:18081/health/telemetry
```

The health payload is sanitized. Static mode/configuration, HTTP liveness and
actual worker/dependency readiness are separate fields. Unknown, missing or
stale worker evidence is not READY. It never returns environment credentials.
The host port is published on loopback only. With `CONSOLE_ENABLED=1`, the console
APIs require authenticated, scoped sessions behind the configured HTTPS origin.
Sanitized health routes remain public; do not treat them as an authorization
boundary. The container probe checks readiness verdicts, not only HTTP 200.

The runtime verifier requires running, healthy PostgreSQL, intake, analyzer,
dispatcher, monitor, operator, notification-sender, source-management and web
containers. Running without a healthcheck is not healthy evidence.

Business-event notifications use token-fenced durable claims, a ten-second total
send bound, and leases of at least fifteen seconds (default thirty). Delivery is
at-least-once across a crash after Telegram acceptance; a stale owner cannot
report a successful database acknowledgement. Terminal failed outbox rows remain
audit evidence, not proof of delivery.

## PostgreSQL Backup and Restore

Backups use the container-local PostgreSQL bootstrap administrator, not a runtime
worker or web password. The database service must be running. Backup directories
are owner-only `0700`; custom-format dumps are `0600`, validated before atomic,
unique publication. `BACKUP_RETAIN_DAYS` defaults to 14 and prunes only expired,
exactly named backup files, never arbitrary files or partial dumps.

```bash
BACKUP_DIR=/home/valarion/backups/fatty-trader bash scripts/backup_postgres.sh
CONFIRM_ISOLATED_RESTORE=YES RESTORE_EXPECTED_SCHEMA_VERSION=31 \
  bash scripts/verify_restore_postgres.sh /absolute/path/to/backup.dump
```

The drill restores into its own generated, PUBLIC-inaccessible database using
`--no-owner --no-acl`, validates schema version and ten durable tables, and verifies
database removal. Require both `restore_verification_cleanup_verified=` and
`restore_verified=` success receipts. It does not overwrite production or prove
that external credentials, PostgreSQL roles, Telegram or exchange access can be
recovered from the database dump alone.

An actual restore is destructive and requires stopped application workers:

```bash
CONFIRM_RESTORE=YES bash scripts/restore_postgres.sh /absolute/path/to/backup.dump
```

Bootstrap required database roles before restoring ACLs into a new cluster;
re-run worker provisioning after restore even if migration 30 is already recorded.
Preserve the private environment separately. The deployed backup timer is a
**user** unit: install `deploy/systemd/fatty-backup.service` under
`~/.config/systemd/user/`, run `systemctl --user daemon-reload`, and inspect
`systemctl --user status fatty-backup.timer fatty-backup.service`. Its service uses
`UMask=0077`.

## Runtime database and telemetry boundaries

`fatty_app` remains the PostgreSQL bootstrap/migration administrator. All execution,
intake, analyzer, console-worker and host operations workers use `fatty_worker`
with the separate private `WORKER_DB_PASSWORD`; web uses `fatty_console_web`.
Provision the worker with `scripts/provision_worker_database.py --confirm` before
recreating services. The provisioner writes a strong distinct secret atomically
to private `.env` without printing it and is repeatable. Runtime workers have
table DML and sequence access, but no superuser, role/database creation, schema
creation, temporary-table, table-truncation or migration-history mutation rights.
Startup no longer performs runtime schema DDL. Future migrations must run through
the administrative `migrate` service, whose default privileges cover new tables.
Host-operation children do not inherit bootstrap/web credentials.

On a fresh installation, initialize PostgreSQL first with distinct private admin,
web and worker secrets so Compose interpolation can succeed, then provision,
migrate and start applications. On an existing installation, provision against the
running database before using the new worker-dependent Compose configuration.
Never run role-mutating integration fixtures against production; use a disposable
PostgreSQL **instance**, not merely a schema.

Console telemetry cleanup defaults:

- `CONSOLE_SNAPSHOT_RETENTION_DAYS=7`; latest and latest-good samples per
  kind/account/environment remain even when older.
- `CONSOLE_EVENT_RETENTION_HOURS=48`, minimum 24; preserve replay validity and a
  high-water event for expired scopes so reconnects can reset correctly.
- `CONSOLE_RETENTION_BATCH_SIZE=1000` per table, maximum 5000;
  `CONSOLE_RETENTION_INTERVAL_SECONDS=60`.

Cleanup is transaction-fenced, skips locked rows, and has bounded lock/statement
timeouts. Invalid configuration disables deletion; cleanup failure does not stop
financial polling. This retention never deletes trading intents, dispatches,
transition evidence, reservations, fills, jobs, notifications or audit history.
Create and restore-check a current backup before enabling cleanup.

Every Compose service uses `json-file` logs with `max-size=10m`, `max-file=5`;
existing containers require recreation to apply this. The runtime verifier checks
actual log settings and database identities, not just configuration text.
Hypervisor CPU steal is an external capacity risk: application health or reduced
query cost cannot prove that the hosting provider has fixed contention.

## HTTPS and protection display

With the trusted `CONSOLE_ORIGIN` configured as HTTPS, all application responses
include `Strict-Transport-Security: max-age=31536000`. Local HTTP does not enable
HSTS, and forwarded request headers cannot select this policy. No preload or
subdomain commitment is made. Responses also set `nosniff` and `no-referrer`.

Native protection is verified only from a current `VENUE_PROTECTED` observation,
not a registration or missing field. Missing/stale evidence is displayed as
unverified/stale. Historical fallback rows lacking environment, owned epoch or
position identity remain visible as ineligible legacy registrations; do not
rewrite their historical state to pretend cancellation or protection. The
protection table's repair action is explicitly labeled **Review native repair**.

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

The service selects a route once per preflight observation and carries the split prices into live sizing. Exact 25/75 sizing uses a total quantum of four venue quantity steps; full-market sizing retains one step. Each child must satisfy its own planned-price minimum notional/quantity and market or LIMIT maximum. Final admitted margin/notional are recomputed after quantization under the existing 1.1 USDT production cap. An infeasible split is rejected before reservation; no approximate split or full-market fallback is invented.

Contract metadata is resolved before symbol account/position reads and margin preparation. A missing contract is unsupported; a failed/malformed contract lookup is provider-unavailable. `AsyncBitgetVenue.preflight()` can prepare margin mode for supported contracts and is not a GET-only diagnostic tool.

Clock offset uses a single-attempt request midpoint, with monotonic duration checks. A sample requires RTT at most 1000 ms and wall/monotonic elapsed disagreement at most 50 ms. Slow, failed, invalid, or wall-clock-jump samples are unavailable evidence, never zero skew or fabricated clock drift. General GET retries and POST ambiguity rules are unchanged. Existing monitor/preflight clock limits remain unchanged.

The protection stream's synchronous symbol lookup (including lazy iteration) and ordered capability persistence run off the shared asyncio loop. Database work must not postpone socket heartbeat handling or an in-flight clock response. Each standalone PostgreSQL capability read/write closes its owned connection on success and failure. Active fallback reads rely on the supported migration prerequisite instead of repeatedly running schema DDL. These scheduling/resource changes do not widen clock bounds, weaken native/fallback identity checks, or clear admission latches automatically.

Monitor reconciliation, watchdog capability/latch persistence, and verified-close storage also run off the shared loop. The shared storage boundary drains an in-flight operation before acknowledging cancellation or a cycle timeout; cancellation must not start a replacement cycle over unfinished writes. Owned reconciliation connections close on success and failure. This does not alter the independently gated fallback mutation lifecycle.

The final ENTRY fence rechecks global, Bitget, and protection-stream latches after awaited leverage preparation, including the LIMIT remainder. A split's protected status requires observed coverage equal to actual aggregate filled quantity. Source cancellation recovery with `reconciliation_only=True` reads back only that management update's existing cancellation claims; it never creates claims or sends cancellation POSTs.

## Approved flat-baseline latch recovery

Use `scripts/recover_bitget_live_latches.py` only under its existing scoped approval and flat-baseline contract. All account mutation consumers must remain stopped throughout proof and apply; a venue admission latch alone does not prove fallback/source/operator mutations are stopped. Do not stop exposure protection to run a flat-account recovery on a nonflat account.

The proof requires full authenticated account UID/baseline binding, eligible exact latch reasons, ready ledger, fresh flat inventory across product families, authenticated public/private V2 stream and both-leg pongs. Review dry-run `recovery_digest` before apply; changed rows, global/unknown reasons, stale proof, or unresolved ownership refuse release. Resume consumers safely, read back released rows, and run the full runtime verifier. Reporting/deployment checks never clear latches. See the remediation plan for ordered deployment/recovery/rollback gates; never restore an old database backup automatically after provider mutations.

The default exact venue reason remains `clock-skew-exceeded`. An owner who explicitly approves recovery of `clock-skew-unavailable` must pass `--clock-latch-reason clock-skew-unavailable` on both dry-run and apply. That mode additionally requires a successful fresh bounded midpoint clock sample within the unchanged production `BITGET_MAX_CLOCK_SKEW_MS` limit (monitor default 10000 ms). The selected reason and limit are digest-bound; changing either invalidates apply. Clock evidence is recorded in the returned proof and release audit. Failed/unreliable current clock evidence, other venue reasons, and global/unknown latches still refuse release. This option neither disables ongoing fault detection nor adds automatic recovery.

## Close ownership and transaction lifetime

Verified close release validates the requested close identity, state, timestamps, and exact provider fill economics before calculating residual exposure. The residual calculation uses the caller's existing locked transaction; it must not create independent readers while the release/binding transaction holds ownership locks.

`OwnedQuantityEvidenceError` denotes contradictory durable ownership/fill evidence. Release returns a rejected admission for that explicit evidence failure; new close binding refuses it without recording permission to mutate. SQL/connection failures are not converted to evidence rejection: they roll back and propagate. Reservations and fill economics remain unchanged on rejection or transaction failure.

Standalone `PostgresLiveIntentStore.get()` and `remaining_owned_quantity()` close the read connection on success, absent records, decoding/validation errors, and SQL failures. A cursor supplied to residual calculation remains caller-owned: the helper does not commit, roll back, or close its transaction. Full-suite regression evidence and real-PostgreSQL smoke results are recorded in the remediation tracker.

## Console host operations boundary

Authentication now uses only the owner-configured console password, not passkeys
or enrollment tokens. To set/rotate it through trusted SSH, run
`runtime/console-ops-venv/bin/python scripts/provision_console.py --confirm --prepare-only --account-id VERIFIED_ACCOUNT_ID --password-stdin`
and pass one password line on stdin; never place it in arguments or shell history.
The private 0640 `runtime/console-password.hash` is the only stored verifier and is
mounted read-only into web. Restart web after rotation. Quiesce console/Telegram
mutation admission and revoke existing console sessions, invalidating their bound
consent without rewriting immutable grant records; do not clear incident latches.
The hash/scope-derived principal revision additionally rejects stale password
epochs. Old enrollment/challenge tables are retained as inert history, not login
routes. No password or verifier belongs in exports, config drafts or logs.

Public access is `https://trade.warga-digital.com`, a proxied Cloudflare A record
(`trade.warga-digital.com -> 46.202.155.192`, record id
`eeaadd59fcc530156940525ac1f8b0b1`) terminated by the host OpenLiteSpeed vhost
`/usr/local/lsws/conf/vhosts/trade.warga-digital.com/vhost.conf` (a `respBuffer 0`
proxy to `127.0.0.1:18081`, mapped in the `Default`, `SSL`, and `SSL IPv6`
listeners of `/usr/local/lsws/conf/httpd_config.conf`). The host `.env` sets
`CONSOLE_ORIGIN="https://trade.warga-digital.com"`; recreate `web`,
`operator-bot`, and `console-worker` together after changing it. The web port
stays loopback-only. Login and mutations require that exact origin, so the former
`ssh -N -L 18081:127.0.0.1:18081 fspmi-hostinger` tunnel no longer establishes a
session; it can still serve unauthenticated reads such as `/health/telemetry`.

Browser push subscriptions are registered from the authenticated console
(`POST /api/v1/push/subscriptions`, exact origin + CSRF + audit) and delivered by
`notification-sender` from the durable outbox; each `(notification, subscription)`
pair has its own retry ledger and subscriptions the push service reports gone
(404/410) are disabled automatically. The VAPID keypair lives under `runtime/`
(`web-push-private.pem` mounted read-only into the sender, `web-push-public.txt`
into web); rotating it invalidates existing subscriptions. Migration 27 adds the
two additive web-push tables. See
[push notifications](BOT-CONSOLE-PUSH-NOTIFICATIONS.md).


The web process has no Docker socket, exchange credentials, private secret key,
or host command runner. `deploy/systemd/fatty-console-ops-worker.service` runs
`scripts/console_ops_worker.py` in a dedicated host virtual environment.
The template is a user unit using `%h/apps/fatty-multi-exchange-trader` and a
dedicated `runtime/console-ops-venv`; it does not install into unrelated app
environments. Docker group membership is root-equivalent host authority and
belongs only to the trusted host executor, never the web UID. The unit sets
no-new-privileges and umask 0077; filesystem isolation is enforced by dedicated
directories and runner path allowlists, not assumed user namespaces. No other
Compose project or host service is in its allowlist.

Host environment requires `CONSOLE_OPS_WORKER=1`, `CONSOLE_ENABLED=1`,
`CONSOLE_REPOSITORY_ROOT`, `CONSOLE_OPS_STATE_DIR`, `CONSOLE_BACKUP_DIR`,
`CONSOLE_COMPOSE_PROJECT`, and the existing approved LIVE/LIVE/1 and provider
credentials. `CONSOLE_COMPOSE_NETWORK` can select the exact approved private
network; otherwise it is `<project>_default`. The launcher resolves only the
project's PostgreSQL container IP and maps `POSTGRES_PASSWORD` into private
`PGHOST/PGPORT/PGUSER/PGDATABASE/PGPASSWORD`; it never publishes a database port.

Secret replacement uses `CONSOLE_SECRET_STAGE_DIR`,
`CONSOLE_SECRET_PUBLIC_KEY_FILE`, and host-only
`CONSOLE_SECRET_PRIVATE_KEY_FILE`. Generate a standard RSA key of at least
3072 bits. Private key and host state must be mode 0600/0700. The web mounts only
the public key and encrypted spool, not the host runtime override. For a shared
spool, configure the exact trusted `CONSOLE_SECRET_STAGE_GID`; the spool must
be setgid 2770 with no other-user access and encrypted stage files are 0640.
Without shared-group configuration the spool is 0700 and files are 0600.
RSA-OAEP SHA256 seals a per-stage Fernet key. Stages expire after 15 minutes,
bind actor/account/kind, and contain no database plaintext. Supported kinds are
`BITGET_API_KEY`, `BITGET_API_SECRET`, `BITGET_API_PASSPHRASE`, `TG_BOT_TOKEN`.

Config drafts are immutable typed revisions. Only poll/delivery intervals,
notification attempt count, evidenced live-source channel IDs and bounded intake
channels (negative IDs or `@username`) are editable; risk caps, gate activation,
margin/leverage, LIVE policy and execution are policy-pinned and cannot be changed
by generic config. `source.configure` creates a draft, not admission, and applies
intake and analyzer configuration only to their exact consumers.
Approved `config.apply` atomically persists a private Compose JSON override,
recreates only affected consumers after quiescence and fresh authenticated
flat proof, and separately observes exact-container nonsecret configuration.
Desired and effective versions remain distinct if readback fails. All future
Compose commands must preserve `COMPOSE_FILE` in order:
`docker-compose.yml`, existing `runtime.compose.json`, existing
`release.compose.json` under the private ops state directory. These override
files are not web mounts and must remain 0600.

`maintenance.prepare` produces a scoped one-hour reviewed no-replay plan.
Service jobs accept only enumerated application services. Disruptive operations
require fresh authenticated flat inventory and refuse unknown/nonflat state;
they never stop existing position protection to make maintenance possible.
Release deploy/rollback accepts only an approved catalog artifact with immutable
OCI image digest, source digest, matching Compose digest and exact schema
version. No branch/ref, shell command, provider URL or filesystem path is a
command argument. Rollback never down-migrates the database.

Register a verified current or rollback artifact through authenticated SSH, using
the host worker's approved environment and dedicated virtual environment:
`python scripts/console_ops_worker.py --register-release RELEASE_ID --image
sha256:EXACT_LOCAL_IMAGE_DIGEST --approval-reference APPROVAL_REFERENCE`.
An OCI `repository@sha256:DIGEST` is also accepted. Tags/branches are rejected.
Registration inspects the exact image, hashes its copied source in a read-only,
network-none, capability-dropped container, and binds the current Compose/schema
digests. This host administrator enrollment path is not a web command.

Backups invoke the existing integrity-checking backup script with automatic
retention disabled; catalog deletion is an explicit reviewed action respecting
retention/holds. SHA256, size and `pg_restore --list` are checked before verify,
delete or restore. Restore requires all application consumers stopped, fresh
flat proof, and an unchanged durable trading-evidence digest since backup.
Changed provider claims/fills/history refuse restore rather than replaying an
older database. The current console control-plane dump is preserved and restored
around the existing restore script; consumers remain stopped and dedicated
admission pause stays active until separately reviewed readiness/reconciliation.

Every host effect has a durable pre-effect receipt in PostgreSQL and a private
filesystem journal surviving database restore. A crash or existing effect claim
uses GET-only reobservation, never another dangerous start/stop/restart, deploy,
restore or latch-release call. Running containers alone do not prove a restart.
`latch.prove` and `latch.release_approved` invoke the exact existing scoped
recovery script with baseline/account/reason/digest binding and continuous
consumer exclusion; no blanket latch clear exists. Dedicated audited
`admission.pause/resume` affects only new-entry admission, preserving monitor,
native/fallback protection and close recovery. Resume refuses incident latches,
unready ledger or stale independent observations.

Notification tests use the configured owner-approved delivery destination.
Requeue creates a distinct linked delivery attempt and preserves original
dedup/failed-at/attempt evidence; it never replays trading.
`notification.send_health` queues a full independently collected health snapshot
rendered by the canonical shared health formatter; it never accepts arbitrary HTML.
`notification.configure_schedule` changes only the existing user
`fatty-health-report.timer` interval to 1/2/3/6/12/24 hours, with durable claim,
fixed systemctl commands, and observed effective monotonic interval/state.
Actual timer fields and next/last trigger observations are reported separately;
an absent timer is unavailable evidence, not an invented six-hour schedule.
The user service must have its existing `XDG_RUNTIME_DIR`/D-Bus environment.
Signal reanalysis uses the existing full analyzer but records only a separate analysis run,
without rewriting source receipt/revision, canonical dispatch or eligibility.
Exports are bounded authenticated job results, with coverage/expiry metadata
and CSV formula escaping; no unrestricted filesystem download is introduced.
