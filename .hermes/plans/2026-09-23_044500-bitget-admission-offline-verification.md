# Bitget Admission Offline Verification Plan

> **For Hermes:** Implement this plan task-by-task with a fresh reviewer; do not touch production or live execution.

**Goal:** Independently verify the Bitget margin/leverage admission remediation using isolated local test resources only, and close the remaining PostgreSQL evidence gap without affecting live or production.

**Architecture:** Keep the current committed implementation (`17bb0f1`) unchanged until local validation is complete. Run PostgreSQL E2E tests against a disposable, isolated local database/container and unique schema, never production credentials, hosts, databases, or exchange APIs. Any required correction is developed on a separate local branch/worktree and validated before proposing a commit; no deploy, push, live cutover, trade, or operator mutation is part of this plan.

**Tech Stack:** Python 3.11, pytest, psycopg, Docker/local PostgreSQL, Ruff, mypy.

---

## Safety invariants (apply to every task)

- No production host/database DSN, production secrets, Bitget credentials, provider API calls, order submits, or trades.
- No service restart, deployment, cutover, migration against a shared/production database, or mutation of live configuration.
- Use a disposable PostgreSQL instance bound to loopback or an isolated Docker network; create a random test database/schema and destroy it after tests.
- Do not print DSN/passwords. Check DSN host/database identity without echoing secrets; fail closed if not explicitly local/test.
- Preserve DEMO/LIVE gates, live caps, kill-switch checks, and `BITGET_OPERATOR_MUTATIONS_ENABLED=0`; tests must not weaken or bypass them.
- Do not push or merge automatically. Any code correction requires explicit user approval before external publication/deployment.

## Current context

- Reviewed commit: `17bb0f103e62ccdde91a47bf50a8bd02f6d7d4fd`, branch `feat/bitget-protection-ws-hardening`.
- Local HEAD was reported equal to origin at review time.
- Code review found no blocker by inspection in durable mismatch latching, reservation startup reconciliation, replay post-fill reconciliation, row mapping, or savepoint handling.
- Independent local test attempt: 498 passed; two PostgreSQL E2E tests errored at setup because configured host `postgres` could not resolve in this environment. Implementer separately reported 2 real-PostgreSQL E2E passes; this remains to be independently reproduced.
- Existing plan markdown files are untracked; keep them untouched unless explicitly asked.

## Phase 0 — Establish a read-only baseline

### Task 0.1: Confirm exact code state

**Files:** None.

Run from `/home/valarion/apps/fatty-multi-exchange-trader`:

```bash
git status --short
git rev-parse HEAD
git rev-parse origin/feat/bitget-protection-ws-hardening
git diff --check
```

Expected: HEAD matches the reviewed commit and origin; only known untracked plan files are present. If code changes or divergence are found, stop and re-audit the exact diff before any tests.

### Task 0.2: Confirm current test and static baseline

**Files:** None.

```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
python -m compileall -q src scripts
```

Expected: static checks pass. Pytest may show exactly the two PostgreSQL setup errors if the external test DSN is misconfigured; record this as environment failure, not a pass. Do not accept a test that silently skips or masks connection failure.

## Phase 1 — Safely provision isolated PostgreSQL

### Task 1.1: Inspect available local runtime without changing it

**Files:** None.

Check Docker availability and running container names/ports using read-only inspection. Do not stop, restart, or modify existing containers. Check whether a dedicated disposable PostgreSQL container or local server is available.

Expected: identify an explicitly isolated test-only endpoint. If unavailable, request approval to start a disposable loopback-bound container; do not repurpose a possibly shared database.

### Task 1.2: Create disposable database target

**Files:** None; local runtime only.

Start a pinned PostgreSQL image in a uniquely named container on an isolated network or loopback-only port, with a throwaway database/user/password generated for this test run. Store DSN only in process environment; never place it in logs, source, plan, shell history, or command output. Confirm target identity by querying `current_database()`, server address, and the disposable container identity, while redacting credentials.

**Stop condition:** If any endpoint resolves to a non-loopback host, shared environment, production-like database, or unknown container, stop without running migrations.

### Task 1.3: Run only the real PostgreSQL E2E tests

**Files:** None.

Set `FATTY_TEST_POSTGRES_DSN` only for the pytest process and run:

```bash
uv run pytest -q tests/e2e/test_postgres_migration_and_margin_admission.py
```

Expected: 2 passed, 0 skipped/errors. Save only sanitized output (test names/count and PostgreSQL version); redact DSN and credentials. If it fails, retain the disposable DB for diagnosis only within the local session and do not point tests at any other database.

## Phase 2 — Verify database behavior and concurrency evidence

### Task 2.1: Migration replay and data preservation

Use the E2E test to prove migrations on a fresh disposable schema, replay on the same schema, and preservation of seeded data. Verify test cleanup drops only the random schema created by its fixture.

Expected: repeat execution is idempotent; data written before replay remains intact; no test touches `public` or unrelated schemas.

### Task 2.2: Concurrent admission proof

Use the E2E test’s real concurrent PostgreSQL admissions to prove exactly one admission fits within the configured headroom; a rejected reservation releases capacity for the next admission. Confirm the test uses isolated DB connections and synchronized concurrent calls, and that the locking scope is exchange-wide.

Expected: repeat at least 10 times against the disposable instance with deterministic acceptance counts. Any nondeterministic result blocks completion.

### Task 2.3: Negative-control mutation check (optional, test-only)

If and only if the test harness supports a temporary isolated worktree/patch, invert the headroom comparison in a disposable worktree and show the concurrency test fails, then discard that worktree. Never alter the primary checkout, commit, or push the mutation. Skip this task if it risks touching the active working tree.

Expected: mutation is detected, and primary checkout remains byte-for-byte unchanged.

## Phase 3 — Focused safety regression validation

### Task 3.1: Durable latch across worker restart

Run focused tests for post-fill mismatch persistence and dispatcher pre-entry latch checks. Verify a newly constructed dispatcher/repository (simulating a replacement process) sees the persisted latch and rejects before any entry POST call.

Target files/tests:
- `src/fatty_trader/exchanges/bitget/async_execution.py`
- `src/fatty_trader/service.py`
- `src/fatty_trader/execution/bitget_dispatcher.py`
- `tests/unit/test_bitget_production_admission.py` and related async/dispatcher tests.

Expected: mismatch persists; new worker fails closed; mock provider records zero entry POST calls.

### Task 3.2: Reservation lifecycle and startup reconciliation

Exercise ACKNOWLEDGED, SUBMITTED, UNKNOWN, FILLED/PARTIAL, REJECTED/CANCELLED, and expired-unresolved cases. Confirm startup reconciliation is GET-only; unresolved expiry remains reserved/escalates to unknown rather than releasing capacity; terminal evidence releases or consumes capacity according to policy.

Target files/tests:
- `src/fatty_trader/execution/bitget_dispatch_execution.py`
- `src/fatty_trader/execution/bitget_dispatcher.py`
- `src/fatty_trader/storage/balance_reservations.py`

Expected: no ambiguous outcome frees headroom; no provider mutation occurs in reconciliation.

### Task 3.3: Replay fill reconciliation

Test restart/replay of a durable filled intent. Confirm it uses persisted admission evidence and invokes the same post-fill observation/mismatch latch as normal submit, with no duplicate entry submission.

Target files/tests:
- `src/fatty_trader/execution/bitget_dispatch_execution.py`
- `src/fatty_trader/storage/live_intents.py`
- `tests/unit/test_bitget_dispatch_execution_adapter.py`

Expected: one readback/reconciliation path, zero second entry POSTs, mismatch latches durably.

### Task 3.4: Verify mode and operator gates unchanged

Inspect the reviewed diff and relevant configuration/tests. Assert execution mode remains restricted to DEMO/LIVE as designed, all existing LIVE caps and execution kill switches remain enforced, and `BITGET_OPERATOR_MUTATIONS_ENABLED=0` remains the operational default/constraint.

Expected: no gate bypass, no auto-enable, no emergency raw API fallback, and no production configuration change.

## Phase 4 — Independent review and closeout

### Task 4.1: Run complete local verification

```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
python -m compileall -q src scripts
git diff --check
```

Expected: all unit tests pass; PostgreSQL E2E tests pass when the isolated DSN is present, otherwise they are explicitly marked NOT VERIFIED (not counted as passing DB evidence). No new warnings/errors.

### Task 4.2: Independent fail-closed review

Request a fresh-context review of the exact commit/diff and sanitized test evidence. Reviewer must inspect latch enforcement, reservation lifecycle, replay fill, migration savepoints, concurrency test quality, and all production/live gates. Any logic/security blocker means FAIL; do not publish/deploy.

Expected: explicit PASS or cited FAIL findings; reviewer must not rely on implementer assertions alone.

### Task 4.3: Clean up test-only resources and report

Drop only the random test schema/database and stop/remove only the uniquely named disposable container/network created in Phase 1. Verify no unrelated container/service/process changed. Report commit ID, sanitized DB test counts, full suite/static-check status, reviewer verdict, and any limitation.

## Files likely to change if a defect is found

No source changes are expected if current implementation is correct. If tests expose a defect, likely code/test files include:
- `src/fatty_trader/storage/migrations.py`
- `src/fatty_trader/storage/balance_reservations.py`
- `src/fatty_trader/storage/live_intents.py`
- `src/fatty_trader/execution/bitget_dispatch_execution.py`
- `src/fatty_trader/execution/bitget_dispatcher.py`
- `src/fatty_trader/exchanges/bitget/async_execution.py`
- `src/fatty_trader/service.py`
- `tests/e2e/test_postgres_migration_and_margin_admission.py`
- focused unit tests under `tests/unit/`

Any fix must use TDD on a separate local worktree/branch, pass all checks, and receive a separate independent review. No push, merge, release, deployment, migration to shared DB, or live cutover is authorized by this plan.

## Completion criteria

- Both PostgreSQL E2E tests independently pass on disposable local PostgreSQL; concurrency test is stable over repeated runs.
- Full unit suite, Ruff, format check, mypy, compileall, and diff check pass.
- Independent review returns PASS with no blocking finding.
- No production/live/provider/database mutation occurred; only the disposable local resources were created and removed.
- If any criterion cannot be met, report the exact blocker and keep production unchanged.
