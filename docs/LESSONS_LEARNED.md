# Remediation lessons and invariants

This is an evolving remediation candidate. Runtime gates, tests, code review,
CI, merge and deployment are separate acceptance states. See
[verification gates](remediation-verification.md).

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

## Monitor provider-read diagnosis

- Keep current anomaly reasons separate from persisted latch reasons. Clean later
  GETs do not release an existing switch or explain its original failure.
- Preserve exception class, safe numeric provider code and HTTP status separately
  from shape validation. Do not log arbitrary provider bodies or exception text.
- Rate-limit retries belong only to bounded GET reads; never replay a POST.
  Re-sign every retry after waiting so an old signature does not create a new fault.
- Preserve the first non-NULL timestamp of an active latch. New activation after
  release starts a new epoch; a historical NULL cannot prove its original time.
- See [monitor repair evidence](bitget-monitor-provider-read-repair.md) for
  reproduced source faults, RED/GREEN receipts and the undeployed verdict.

## Verified operational recovery, not release readiness

The original PUMP native market SL/TP installation was accepted and read back
without entry replay. Later authenticated reads showed no PUMP position/pending
orders and a matching 1880-unit close fill at 0.005426. This receipt does not
by itself attribute the close to a particular plan or prove all bot accounting.
The original candidate receipt did not establish release readiness. Current
production configuration is governed by [LIVE-only policy](PRODUCTION-LIVE-POLICY.md),
not historical closed-gate notes. Do not infer current admission from old receipts.

## Report truth and recovery evidence

- A canonical signal with a dispatch count is queue creation, not execution.
  Explicitly disclaim absent provider order/fill confirmation in the setup card.
- Historical filled recovery with an unresolved close/protection reason is a
  blocked recovery event, not a new successful entry.
- Aggregate health includes global, venue and stream latches. Missing dispatcher
  or lifecycle evidence defaults to UNKNOWN/degraded, never running/ready.
- Signed historical positions provide actual `ctime` and position IDs. Matching
  economics and time is a diagnostic candidate, not a direct entry-to-epoch binding;
  never substitute the earliest fill time for a position creation epoch.
- A truthful report deployment does not restore blocked entry. Preserve historical
  uncertainty and obtain a reviewed policy before any new operational baseline.
- See [deployed correction and remaining recovery blockers](REPORT-TRUTH-RECOVERY-STATUS.md)
  and [notification contract](SIGNAL-NOTIFICATION-TRUTH.md).
