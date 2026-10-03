# Remediation lessons and invariants

This is an evolving remediation candidate. Runtime gates, tests, code review,
CI, merge and deployment are separate acceptance states. See
[verification gates](remediation-verification.md).

## Atomic final ENTRY permission

- Alternating unlocked kill and source reads cannot prove final permission:
  either read may block while the other veto changes. Lock the source ownership
  first, fence all kill-table writes (including insertion of absent scopes), then
  recompute database-clock SOURCE eligibility. Hold that transaction only through
  the single ENTRY POST; release it before GET reconciliation or post-fill latches.
- The PostgreSQL regression in `tests/e2e/test_bitget_final_permission_postgres.py`
  first reproduced both global/Bitget late latches crossing the offline POST, then
  verified rejection with zero POSTs. It also observes a later INSERT waiting on
  the held fence via `pg_stat_activity`; no provider network calls are used.
- A kill latch requested after permission wins must wait until the in-flight POST
  exits. This establishes ordering, not cancellation of an already-authorized
  HTTP request. The table SHARE lock also delays unrelated kill scopes, and source
  row locks delay source updates during that POST. Production latency/permissions
  and deployment have not been verified; this is a local remediation candidate.
- A port stated by delegation may not match a surviving disposable PostgreSQL
  instance. The requested port 55442 was unavailable; the disposable socket's
  default port worked. Use a separate test database, never a production DSN.

## Provider contracts

- Position TPSL market execution permits omitted execute-price fields. Validate
  market-only semantics before omitting; never work around code 43011 by
  replacing a stop with positive-price limit execution.
- Parameter rejection is not proof of unsupported native TPSL. Preserve known
  entry fills independently of missing or ambiguous protection.
- Reuse durable client OIDs after ambiguous responses. GET-only reconciliation
  precedes any retry; a fresh timestamp suffix can duplicate real exposure.
- A registered fallback row is not proof of enforcing protection. Require runtime
  ownership, fresh prices, explicit gates and provider evidence.
- Empty/malformed/failed provider reads are UNKNOWN, never confirmed flat.

## Durability and accounting

- Reservation margin, per-symbol ownership and position slots share atomic
  PostgreSQL admission. Recheck snapshot freshness after waiting on the lock.
- Consumed ownership persists until an authenticated matching close fill and
  confirmed owned flat-position receipt justify release.
- Add new migration versions. Never silently rewrite historical schema migration
  bodies or alter old trade accounting as part of an unrelated release.
- Source fills and provisional accounting must converge without double counting.
  Never fabricate a close price from an entry price or mark.

## Intake and analysis

- Catchup coverage and realtime MAX(message_id) are different cursors. Keep
  persistent ascending coverage; retain history without replaying stale entries.
- Explicit wait/cancel/negated entry vetoes must occur before market lookup.
  Prefix-aware guards preserve real $NOT/$WAIT tickers.
- Model summaries do not override original caption stand-down. Media failures
  preserve valid text fallbacks and report that image validation failed.
- Each message uses a database savepoint. A genuine SQL error in one row must
  not poison valid siblings or leave half-written interpretations.
- Subprocess environment is an allowlist with explicit CLI auth. Read-only
  sandboxing limits writes, not every UID-visible read. Keep sensitive host
  directories outside mounts and never grant production DB/venue credentials.
- Verify CLI flags against the deployed CLI's --help without an authenticated
  provider request. Bound process-group termination and pipe-reader joins.

## Verification and operations

- Use disposable PostgreSQL, not SQLite facsimiles, for migrations, concurrency,
  constraints, recovery and lock/freshness proofs.
- Isolated tests expose only fixed source snapshots, an allowlisted environment
  and the disposable DB Unix socket. Never source the LIVE .env for tests.
- Baselines must distinguish clean main, committed branch and dirty candidate.
  Preserve expected RED logs and exact failed/skipped counts.
- Frozen tests and independent-review hashes must match the revision merged and
  released. Mechanical formatting after review invalidates its exact hashes.
- Configuration validation is not worker liveness. A separate healthcheck must
  read evidence published by the actual worker rather than another config check.
- Publish durable checkpoints to the candidate branch; neither a green subset
  nor a successful image build authorizes a LIVE cutover.

## Verified operational recovery, not release readiness

The original PUMP native market SL/TP installation was accepted and read back
without entry replay. Later authenticated reads showed no PUMP position/pending
orders and a matching 1880-unit close fill at 0.005426. This receipt does not
by itself attribute the close to a particular plan or prove all bot accounting.
The candidate has not been released. Entry/fallback/stream mutation gates stay
closed until the full verification and deployment gates are satisfied.
