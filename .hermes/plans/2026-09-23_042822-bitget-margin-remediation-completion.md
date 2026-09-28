# Bitget Margin Admission Remediation Completion Plan

> **For Hermes:** Execute task-by-task with TDD and independent two-stage review (spec compliance, then code quality/safety). Do not enable production execution.

**Goal:** Close all known correctness, durability, recovery, database-proof, and test-regression gaps in Bitget asynchronous margin/leverage admission before any production enablement is considered.

**Architecture:** Preserve the existing immutable admission payload and PostgreSQL reservation model. Make safety degradation durable and checked by every admission, add a recoverable lifecycle for acknowledged orders and replayed fills, repair database-row mapping tests, then prove migration and concurrency behavior against a real PostgreSQL instance. Unknown provider outcomes remain reserved and fail closed; only evidence-backed terminal outcomes release or consume capacity.

**Tech Stack:** Python, asyncio, Pydantic, PostgreSQL, pytest, Ruff, mypy, Docker Compose (if available).

---

## Current state and constraints

- Repository: `/home/valarion/apps/fatty-multi-exchange-trader`, branch `feat/bitget-protection-ws-hardening`.
- Existing remediation commits include `ef6607c`, `9daa518`, and `416d7e0`; there are also **uncommitted concurrent edits**. Before implementing, inspect `git status` and the full diff; preserve and integrate user/subagent changes rather than overwrite them.
- Current latest test run on the mutable working tree: **3 failed, 495 passed, 2 skipped**. All three failures are `IndexError` in `PostgresLiveIntentStore.get()` where legacy test fakes return 12 columns but the query now selects 18.
- Existing E2E PostgreSQL tests are in `tests/e2e/test_postgres_migration_and_margin_admission.py`; they skip unless `FATTY_TEST_POSTGRES_DSN` is set.
- Prior independent review identified three blocking gaps: (1) post-fill mismatch degradation is process-local and not enforced durably on newly started workers; (2) acknowledged reservations lack a worker/sweep for reconciliation and escalation; (3) replay/restart fills do not run post-fill reconciliation.
- Preserve `BITGET_OPERATOR_MUTATIONS_ENABLED=0`, existing `BITGET_EXECUTION_ENABLED`/canary/risk gates, caps, and fail-closed policy. No LIVE provider calls or trades. Do not add an emergency bypass or raw API fallback.
- Keep migrations additive and support both fresh-schema setup and upgrades. Do not claim real PostgreSQL proof from mocks or skipped tests.

## Global acceptance criteria

1. Existing fixtures and database row mapping no longer crash; both tuple and mapping rows map correctly with all admission fields, and legacy/null fields have explicit safe behavior.
2. A material provider-vs-planned leverage/margin mismatch creates a durable degradation latch; every new service/process checks it before reservation and order submission. Restart/new-worker tests prove the latch persists. Only an explicit audited recovery procedure backed by fresh provider evidence may clear it; no automatic clear.
3. An acknowledged/submitted order retains its reservation until provider evidence reaches a documented terminal state. A durable reconciliation worker/sweep claims pending intents safely, retries transient failures, escalates expired/indeterminate orders without freeing capacity, and is safe under duplicate workers/restarts.
4. Every path that discovers a fill—including normal submit, duplicate dispatch, startup recovery, and `reconcile_intent()` replay—runs the same post-fill verification exactly once/idempotently and latches material mismatches durably before further entries.
5. Real PostgreSQL proves migrations apply to both fresh and prior schema and can be re-applied/idempotently advanced without duplicate objects or corruption.
6. Deterministic concurrent admission proof demonstrates that two tasks sharing one PostgreSQL-backed balance headroom cannot both reserve beyond capacity; rejected pre-POST paths release, unknown outcomes retain, and terminal filled/partial outcomes consume according to policy.
7. Full tests, source/test lint, format, mypy, compile, migration proof, concurrency proof, and independent safety review pass. Any infrastructure-dependent proof not run remains an explicit blocker; no production-readiness claim.

## Task 1: Repair LiveIntent database row mapping

**Objective:** Eliminate the three known failing tests and prevent positional row shape from silently corrupting admission evidence.

**Files:**
- Modify: `src/fatty_trader/storage/live_intents.py` (`PostgresLiveIntentStore.get` and related row decoding)
- Modify: `tests/unit/test_live_intents.py`
- Modify: `tests/unit/test_bitget_live_recovery.py`
- Inspect: `src/fatty_trader/storage/schema.py`, migrations selecting the admission columns

**Steps (TDD):**
1. Add/update tests using 18-column tuple rows, 18-key mapping rows, legacy 12-column rows, null admission fields, and malformed short rows. Assert values map to the exact named fields.
2. Run `uv run pytest -q tests/unit/test_live_intents.py tests/unit/test_bitget_live_recovery.py`; confirm the old fixtures currently fail.
3. Implement an explicit row-decoding boundary. Prefer cursor-provided named-column information or a documented select-column tuple; never guess arbitrary missing values. Legacy short-row compatibility is allowed only if explicitly defined and all newer admission evidence defaults to absent, after which entry replay must safely reject missing evidence.
4. Run the targeted tests, then commit only those files.

**Acceptance:** The three reported `IndexError` failures are fixed; extra/missing columns cannot be silently misassigned.

## Task 2: Add durable fail-closed mismatch latch

**Objective:** Make post-fill material mismatch close future admissions across restarts and workers.

**Files:**
- Modify: `src/fatty_trader/exchanges/bitget/async_execution.py`
- Modify: `src/fatty_trader/service.py`
- Modify: `src/fatty_trader/storage/schema.py`
- Modify: `src/fatty_trader/storage/migrations.py` (new append-only migration)
- Add or modify: `src/fatty_trader/storage/` durable execution safety state repository
- Add: focused latch repository/service tests

**Steps (TDD):**
1. Test that a mismatch writes durable state with exchange, reason, planned/observed evidence, intent/reservation IDs, timestamp, and unresolved status.
2. Test a newly constructed repository/service instance sees the unresolved latch and rejects admission before balance reservation or provider mutation.
3. Test concurrent admission cannot race past a latch being set; use a transaction/lock or equivalent serialization shared with reservation admission.
4. Test database errors, missing rows, malformed evidence, and stale/unavailable reads fail closed.
5. Implement append-only schema/migration and repository methods (`latch`, `get_unresolved`, audited resolution only if the project has an approved recovery authority; otherwise no clear method).
6. Wire every production admission entry point to consult the durable state before sizing/reserving. Local `_degraded` may remain as an optimization only, never as the source of truth.
7. Run focused tests; inspect migration SQL and ensure no gate bypass was introduced; commit.

**Acceptance:** Restart/new worker remains blocked after mismatch; unrelated process-local object state cannot clear the latch.

## Task 3: Unify and test post-fill verification, including replay

**Objective:** Ensure any newly discovered fill receives the same provider margin/leverage verification, regardless of how the intent is observed.

**Files:**
- Modify: `src/fatty_trader/exchanges/bitget/async_execution.py`
- Modify: `src/fatty_trader/execution/bitget_dispatch_execution.py`
- Modify: `src/fatty_trader/storage/live_intents.py` if idempotent observation persistence is needed
- Add/modify: async execution and replay/recovery unit tests

**Steps (TDD):**
1. Add regression tests for a fill found by normal submission, duplicate intent replay, restart reconciliation, and partial fill; assert provider position/leverage observations occur and are persisted.
2. Add mismatch tests proving durable latch creation happens before admission can continue.
3. Add tests that repeated reconciliation is idempotent and does not duplicate fill, reservation, or mismatch records.
4. Extract one shared post-fill verification routine and invoke it on every transition that first discovers a fill; persist observation before reporting completion.
5. On unavailable/ambiguous readback, retain reservation, mark outcome indeterminate, and latch or block new admission according to the risk policy; never fabricate successful margin evidence.
6. Run focused tests; commit.

**Acceptance:** No fill-discovery/replay path bypasses post-fill reconciliation; retry is idempotent and failures fail closed.

## Task 4: Build acknowledged-order reconciliation and expiry/escalation sweep

**Objective:** Prevent reservations from remaining indefinitely acknowledged with no provider reconciliation or operational escalation.

**Files:**
- Modify: `src/fatty_trader/storage/balance_reservations.py`
- Modify: `src/fatty_trader/storage/live_intents.py`
- Modify: service/startup or existing background-worker lifecycle (inspect `src/fatty_trader/service.py` and app startup before choosing exact hook)
- Modify: `scripts/health_report.py`
- Add: reservation sweep, claim/retry, and lifecycle tests

**Steps (TDD):**
1. Test repository query returns only acknowledged/submitted/unknown intents requiring reconciliation, with bounded batches and deterministic ordering.
2. Test atomic claim/lease prevents two workers reconciling the same item; expired lease can be reclaimed after restart.
3. Test provider confirmed rejected/cancelled releases; partial/full fill consumes; pending/timeout/unknown retains reservation and increments retry/escalation state.
4. Test expiry alone never frees funds; it creates an operationally visible escalation and continues counting as reserved/unknown.
5. Implement durable claim/lease and worker invocation using the repository’s established service lifecycle; avoid launching untracked background tasks that silently die.
6. Expose pending age, unresolved count, retry/error, last successful sweep, and mismatch latch status in health reporting without secrets.
7. Run worker/repository/health tests; commit.

**Acceptance:** Restart-safe sweep runs or is explicitly operated by an existing supervised worker; pending reservations remain fail-closed and visible until evidence resolves them.

## Task 5: Real PostgreSQL migration and concurrency proof

**Objective:** Replace skipped/unexecuted proof with reproducible PostgreSQL-backed tests.

**Files:**
- Modify: `tests/e2e/test_postgres_migration_and_margin_admission.py`
- Inspect: `pyproject.toml`, `docker-compose.yml`/compose files, CI workflows, migration runner
- Add CI configuration only if needed to ensure this test executes in an approved isolated test database

**Steps:**
1. Inspect current E2E test and available Docker/PostgreSQL. Confirm no production DB URL or credentials are used; use a disposable isolated test database.
2. Add failing tests for fresh schema bootstrap, upgrade from the immediately previous schema, applying all migrations, reapplying migration runner, and validating columns/constraints/indexes.
3. Run the real PostgreSQL tests with `FATTY_TEST_POSTGRES_DSN` explicitly pointed at the disposable test DB; record server version and exact command, do not print credentials.
4. Add deterministic concurrency test using barriers/two independent connections/process-level transactions against the real advisory-lock reservation path; assert only one reservation succeeds when combined demand exceeds available capacity.
5. Test first attempt rejected before provider POST releases reservation and permits a later attempt; test unknown outcome remains reserved and blocks excess allocation.
6. Ensure skipped markers make missing infrastructure obvious; do not count skipped as proof. Configure CI service PostgreSQL if repo supports it.
7. Run all PostgreSQL proofs twice to demonstrate idempotence/repeatability; commit.

**Acceptance:** At least one executed run (zero skips for these E2E cases) against a disposable real PostgreSQL instance passes all migration and concurrency assertions.

## Task 6: Comprehensive gate-path and risk regression matrix

**Objective:** Prove admission evidence and capacity behavior remain correct across gates and failures.

**Files:**
- Modify: `tests/unit/test_bitget_production_admission.py`
- Modify: `tests/unit/test_bitget_service_admission_wiring.py`
- Modify: `tests/unit/test_bitget_dispatch_execution_adapter.py`
- Modify: `tests/unit/test_bitget_margin_reservation_lifecycle.py`
- Add targeted tests as needed

**Cases:**
- configured cross-symbol active position cap is honored;
- stale/missing balance evidence rejects before reservation;
- leverage set/readback mismatch rejects before order POST;
- canary/local validation rejection releases reservation only when POST is provably not attempted;
- timeout/exception after POST may have occurred retains `unknown` reservation;
- acknowledged/submitted remains held pending sweep;
- rejected releases exactly once; partial/full consumes exactly once;
- durable mismatch latch prevents admission after process restart;
- operator mutation gate remains disabled and no raw API fallback exists.

**Steps:** Write each regression test first, run to observe failure, implement smallest correction, run focused suite, commit coherent group.

## Task 7: Full verification and independent safety review

**Objective:** Establish evidence-based completion without claiming production readiness prematurely.

**Commands:**
```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
python -m compileall -q src scripts
```
Run the PostgreSQL E2E tests separately with the disposable test DSN and verify they execute rather than skip. Run `git diff --check` and inspect complete diff/status.

**Independent review checklist:**
- no synchronous/concurrent path can bypass durable mismatch latch or position cap;
- no reservation is released after uncertain POST outcome;
- acknowledged/replayed fills enter the durable reconciliation lifecycle;
- migrations are additive/idempotent and match fresh schema;
- no secret, LIVE gate, canary cap, or operator-mutation setting changed;
- no external provider call or trade was made.

**Stop conditions:** If PostgreSQL cannot be provisioned, credentials are unavailable, migration proof fails, review returns a blocking finding, or any test fails, report the exact blocker. Do not enable production execution or claim complete remediation.

## Expected changed files

- `src/fatty_trader/storage/live_intents.py`
- `src/fatty_trader/storage/balance_reservations.py`
- `src/fatty_trader/storage/schema.py`
- `src/fatty_trader/storage/migrations.py`
- `src/fatty_trader/exchanges/bitget/async_execution.py`
- `src/fatty_trader/execution/bitget_dispatch_execution.py`
- `src/fatty_trader/service.py` and supervised worker startup module (exact module to be confirmed during read-only discovery)
- `scripts/health_report.py`
- focused unit tests and `tests/e2e/test_postgres_migration_and_margin_admission.py`

## Delivery protocol

- Preserve current concurrent working-tree changes; coordinate before edits to overlapping files.
- Use TDD, small focused commits after tests pass, and independent review after safety-critical groups.
- Final report must list commit SHAs, exact test/lint/mypy/E2E outputs, real PostgreSQL availability or blocker, review verdict, remaining uncommitted work, and an explicit statement that production execution was not enabled.
