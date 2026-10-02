# Bitget Context-Aware Entry and Unknown-Order Reconciliation Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Prevent ICPUSDT-style ambiguous entries and incorrect 25%/75% routing by distinguishing a limit order that has genuinely moved away from its entry from a signal that was already waiting for a normal pullback, while making every ambiguous provider result reconcile to a terminal state without blind retry.

**Architecture:** Extend entry routing from a price-only pure function to a context-aware decision that receives durable limit-order history and directional movement evidence. Persist an entry batch plus separate market and residual-limit intents before provider POSTs. Add a GET-only reconciliation worker for `UNKNOWN` dispatches/intents, using order detail, matching fills, current position, and pending orders. Keep all ambiguous results fail-closed: no replay, no second POST, and no residual limit until the market child is reconciled and protected.

**Tech Stack:** Python 3.11, Pydantic/domain dataclasses, PostgreSQL/psycopg, Bitget REST API, Docker Compose, pytest, Ruff, mypy.

---

## Current context and constraints

- Existing pure router: `src/fatty_trader/execution/entry_routing.py`.
- Existing rule currently uses price only:
  - LONG: market at least threshold above signal entry → 25% market + 75% limit at entry.
  - SHORT: market at least threshold below signal entry → 25% market + 75% limit at entry.
  - Otherwise full market.
- Existing tests: `tests/unit/test_entry_routing.py`.
- Existing provider reconciliation logic: `src/fatty_trader/exchanges/bitget/async_execution.py`.
- Existing durable intent store: `src/fatty_trader/storage/live_intents.py`.
- Existing dispatch transitions: `src/fatty_trader/execution/bitget_dispatch_repository.py`.
- Existing fallback protection state: `src/fatty_trader/execution/bitget_fallback_protection.py`.
- The production worktree already has unrelated modifications. Do not reset, overwrite, or include unrelated changes in commits.
- No new provider POST is permitted for an ambiguous result. Reconciliation is GET-only until evidence proves the original order was absent and the position is flat.
- Do not make live execution safer by silently changing the global execution gate. The feature must be tested in unit/integration/demo mode first and promoted through the existing live cutover gates.

## Definitions

### Normal pullback-waiting entry
A signal whose entry limit was not previously working in the current entry lifecycle. The current mark may already be above the LONG entry or below the SHORT entry. This is not evidence that price recently departed from the limit.

### Just-departed limit entry
A signal with a durable, provider-acknowledged limit child that was recently working, with evidence that the market was at/near the signal entry and then moved away in the adverse direction:

- LONG: mark moved upward away from entry.
- SHORT: mark moved downward away from entry.

Only this condition may use the near-limit split route.

### Near-limit split route
- 25% at the current mark using a market order.
- 75% residual limit at the original signal entry.
- The residual limit must have its own deterministic client OID and must be reconciled independently.

## Acceptance criteria

1. A LONG signal that starts above entry without a previously working limit is not classified as just-departed.
2. A SHORT signal that starts below entry without a previously working limit is not classified as just-departed.
3. A previously working LONG limit that moves slightly upward uses 25% market plus 75% entry limit when within the configured near-limit window.
4. A previously working SHORT limit that moves slightly downward uses the equivalent split.
5. A large adverse move uses the existing late-entry policy.
6. A favorable move or retrace toward entry does not trigger the just-departed route.
7. Market and residual-limit children have durable intents before POST.
8. A residual limit is never submitted when the market child is `UNKNOWN`, unprotected, or unreconciled.
9. `UNKNOWN` orders are reconciled by provider order detail, matching fills, current positions, and pending orders; no blind POST retry occurs.
10. Provider `40109` + no matching fills + flat position becomes terminal rejection evidence with an audit record.
11. A filled close/entry stores provider fill quantity, price, fee, realized PnL, provider fill ID, and client OID exactly once.
12. Runtime reports distinguish `UNKNOWN`, `RECONCILIATION_PENDING`, `REJECTED`, `FILLED`, and `RECONCILED_NO_ORDER`.

## Review gates added before implementation

- Preserve the existing normal pullback behavior exactly. The new near-limit route must not convert a normal LONG pullback-wait or SHORT pullback-wait into a market entry. A signal with no previously working limit remains on the existing route for that signal lifecycle; `FULL_MARKET` is not a substitute for an existing limit-wait state.
- Never classify a just-departed route from one current-price snapshot. Require a durable provider-acknowledged limit lifecycle plus at least two time-ordered observations: one at/near entry and one moving away in the correct direction.
- Use remaining quantity, not original quantity, after any partial limit fill. The 25%/75% split applies only to unfilled exposure and must not increase total intended position size.
- A residual limit may be submitted only after the market child is reconciled and its protection is confirmed under the venue policy: native protection when supported, or the explicitly approved fallback path with its own read-back. Do not hard-require native protection if that would silently break an existing approved fallback policy.
- Never classify `40109` as rejection when fills retrieval is incomplete, paginated data has not been exhausted, the position read failed, or evidence conflicts. Provider fill history must be queried with pagination/time bounds and matched by client OID and provider order ID.
- Resolve the state-model choice before coding: prefer existing terminal `REJECTED` plus reason `provider-flat-no-order-or-fill` and a reconciliation audit row unless a distinct `RECONCILED_NO_ORDER` state is added consistently to every transition, notification, report, and test path.
- Keep manual operator mutations disabled by default and do not add an emergency bypass in this plan. `/close`, `/open`, `/cancel`, `/setsl`, and `/settp` remain blocked while `BITGET_OPERATOR_MUTATIONS_ENABLED=0`; any future one-time enablement requires a separate explicit operational decision outside this implementation.
- Before any live deployment, run a shadow/read-only mode that records route decisions without POSTing and compare decisions against historical signals. A false-positive rate of zero is required for the just-departed classification in the review fixture set.

## Task 0: Freeze semantics, state contracts, and ownership fences before coding

**Objective:** Remove architecture ambiguity before modifying routing or reconciliation code.

**Files:**
- Inspect/modify: `src/fatty_trader/domain/state_machines.py`
- Inspect/modify: `src/fatty_trader/execution/bitget_dispatcher.py`
- Inspect/modify: `src/fatty_trader/exchanges/bitget/async_execution.py`
- Inspect/modify: `src/fatty_trader/exchanges/bitget/client.py`
- Inspect/modify: `src/fatty_trader/storage/migrations.py`
- Test: state, interface, and concurrency contract tests

**Decisions required before implementation:**
1. Define `LIMIT_ONLY`/`WAIT_FOR_ENTRY_LIMIT` as the normal pullback route. It must place or maintain only the original entry limit and must never market-enter from a current-price snapshot.
2. Keep the existing far-adverse late-entry policy only as a separately named and separately tested route. It must not be conflated with `NEAR_LIMIT_SPLIT_MARKET_LIMIT`. If the owner requirement is strict “25/75 only after a previously working limit departed,” disable the far-adverse split and test that no prior-limit evidence means no 25/75 route.
3. Define the child/batch state machine and terminal states before schema migration. Distinguish intent state, entry-batch state, and dispatch state. Prefer current `REJECTED` plus reason `provider-flat-no-order-or-fill` over adding `RECONCILED_NO_ORDER`; if a new state is required, update transitions, DB checks, reports, notifications, and tests atomically.
4. Define one execution interface for market and limit children with batch ID, child ID, client OID, order type, limit price, quantity, POST result, reconciliation result, and protection result. Adapt existing callers rather than adding a second incompatible protocol.
5. Define evidence precedence: exhaustive detail/fills/pending-orders/position reads are required before no-order rejection; any incomplete read keeps the child `UNKNOWN`.
6. Define the residual policy: explicit signal TTL plus hard maximum, deterministic cancel intent, cancel API, cancel read-back, and startup orphan cleanup.
7. Define protection admission: native provider protection is preferred; a verified fallback is acceptable only when its separate mutation gate is enabled and the fallback state/read-back is healthy. Stale or unverified protection blocks residual submission.
8. Define an atomic entry-batch claim with `SELECT FOR UPDATE` or advisory lock, unique signal/revision ownership, compare-and-set lifecycle transitions, and lease ownership checked immediately before every provider POST.

**Required tests before proceeding:**
- Two workers/restarts cannot produce more than one market POST per batch.
- Lease expiry cannot allow a second owner to POST while the first owner is unresolved.
- `40109` + no fills + non-flat position stays `UNKNOWN`.
- `40109` + no fills + matching pending order stays `UNKNOWN`.
- Any failed position/fill/pending-order read stays `UNKNOWN`.
- Normal pullback creates/maintains `LIMIT_ONLY`, not `FULL_MARKET`.

**Gate:** Do not start Task 1 implementation until these decisions are recorded in code-level contracts and the tests establish the safety baseline.

---

## Task 1: Document the routing contract and configuration surface

**Objective:** Define the new decision inputs and thresholds without changing runtime behavior yet.

**Files:**
- Modify: `src/fatty_trader/execution/entry_routing.py`
- Modify: `src/fatty_trader/config/bitget.py`
- Modify: `docker-compose.yml`
- Modify: `.env.example` if present
- Test: `tests/unit/test_entry_routing.py`

**Steps:**
1. Add explicit domain concepts for limit lifecycle context, such as `LimitEntryContext` with:
   - `was_working`
   - `provider_acknowledged`
   - `created_mark`
   - `last_seen_mark`
   - `created_at`
   - `current_mark`
   - `signal_entry`
   - `near_limit_threshold_pct`
   - `just_departed_window_seconds`
2. Add configuration values with safe defaults:
   - `BITGET_LATE_ENTRY_THRESHOLD_PCT=0.005`
   - `BITGET_NEAR_LIMIT_THRESHOLD_PCT=0.005`
   - `BITGET_JUST_DEPARTED_WINDOW_SECONDS=60`
   - `BITGET_RESIDUAL_LIMIT_ENABLED=0` by default; enable only after the route passes shadow/demo/canary verification and an explicit deployment approval.
3. Reject invalid values: thresholds must be finite and in `(0, 1)`, window must be positive.
4. Add comments explaining that near-limit routing requires prior limit-order evidence and is not inferred from price alone.
5. Add failing tests for configuration validation and default values.
6. Run: `uv run pytest -q tests/unit/test_entry_routing.py`.
7. Expected: new configuration tests fail until implementation is added.

---

## Task 2: Implement pure context-aware routing with TDD

**Objective:** Make routing distinguish just-departed limits from normal pullback entries.

**Files:**
- Modify: `src/fatty_trader/execution/entry_routing.py`
- Modify: `tests/unit/test_entry_routing.py`

**Steps:**
1. Write failing tests for:
   - LONG, no prior limit, current mark above entry within 0.5% → preserve the existing normal pullback-wait route; never classify as just-departed and never inject a market child solely from the snapshot.
   - SHORT, no prior limit, current mark below entry within 0.5% → preserve the existing normal pullback-wait route; never classify as just-departed and never inject a market child solely from the snapshot.
   - LONG, acknowledged limit was working at entry and mark moved upward slightly → 25% market + 75% limit at entry for the remaining unfilled quantity.
   - SHORT, acknowledged limit was working at entry and mark moved downward slightly → 25% market + 75% limit at entry for the remaining unfilled quantity.
   - LONG retrace toward entry → no just-departed split.
   - SHORT retrace toward entry → no just-departed split.
   - Movement outside near-limit window → existing late-entry route, not near-limit route.
   - Expired limit context → no just-departed classification.
   - Invalid/unknown limit context → fail closed to the configured safe route; never invent prior limit evidence.
   - Partial limit fill → split only the remaining quantity and never exceed the original requested quantity.
   - Two observations with equal price or wrong-direction movement → no just-departed classification.
   - Boundary values exactly at the threshold and exactly at the time window → deterministic, documented behavior.
2. Add a distinct route mode, for example:
   - `LIMIT_ONLY` (preserve the normal pullback-wait lifecycle)
   - `FULL_MARKET`
   - `SPLIT_MARKET_LIMIT`
   - `NEAR_LIMIT_SPLIT_MARKET_LIMIT`
   The route must carry `reason`, `market_quantity`, `limit_quantity`, and `limit_price`.
3. Implement directional predicates explicitly:
   - LONG just-departed: current mark > entry and the prior observed mark was at/near entry.
   - SHORT just-departed: current mark < entry and the prior observed mark was at/near entry.
4. Do not use only `current_mark` versus `signal_entry` to infer just-departed status.
5. Keep quantity split calculation centralized and exact: `market = total * 0.25`, `limit = total - market` before venue-step rounding.
6. Run: `uv run pytest -q tests/unit/test_entry_routing.py`.
7. Expected: all routing tests pass.

---

## Task 3: Add durable entry-batch and limit lifecycle records

**Objective:** Persist enough history to prove whether a limit was actually working and whether price recently left it.

**Files:**
- Modify: `src/fatty_trader/storage/schema.py`
- Modify: `src/fatty_trader/storage/migrations.py`
- Modify: `src/fatty_trader/storage/live_intents.py`
- Modify: `src/fatty_trader/domain/models.py` or the existing dispatch domain model location
- Test: `tests/unit/test_live_intents.py` or the closest existing intent-store test file

**Steps:**
1. Add an idempotent migration for an entry-batch table containing:
   - batch ID
   - exchange/symbol/direction
   - signal ID/revision
   - signal entry
   - market-at-routing
   - route mode/reason
   - total quantity
   - market child client OID
   - residual limit child client OID
   - lifecycle state
   - created/updated timestamps
2. Add an observation table or JSONB audit field for the recent mark observations required by the just-departed predicate. Each observation must include mark, timestamp, source/freshness, provider limit state, batch ID, limit child identity, and lifecycle revision; `created_mark`/`last_seen_mark` alone are insufficient.
3. Add constraints preventing two active residual limit children for one entry batch.
4. Add additive, versioned migration/backfill rules, indexes for batch/signal/client OID/active state, and deployment verification. Do not rewrite existing live intents or fills.
5. Persist the batch before any provider POST.
6. Add idempotent storage methods for creating a batch, attaching child OIDs, recording provider IDs, and advancing lifecycle state.
7. Test restart/idempotency behavior: the same signal revision cannot create a second active batch.
8. Run migration tests and a local PostgreSQL schema check.

---

## Task 4: Wire routing context into the Bitget dispatcher

**Objective:** Make the production dispatcher use the new route only when the durable lifecycle proves it is valid.

**Files:**
- Modify: `src/fatty_trader/execution/bitget_dispatcher.py`
- Modify: `src/fatty_trader/execution/entry_routing.py`
- Modify: `src/fatty_trader/execution/bitget_dispatch_repository.py`
- Test: `tests/unit/test_bitget_dispatcher.py`
- Test: relevant `tests/e2e/` Bitget cycle test

**Steps:**
1. Before routing, load/create the entry batch and its limit lifecycle context.
2. Record the current mark and whether a provider-acknowledged limit child was working.
3. Call the context-aware router using the batch's remaining unfilled quantity, never the original quantity after a partial fill.
4. Persist the route decision and child OIDs before submission.
5. For `LIMIT_ONLY`, preserve the existing pullback-wait behavior and submit only the original entry limit through the intent-first path.
6. For `FULL_MARKET`, submit one market child through the existing intent-first path; do not use this branch to replace an existing normal pullback-wait limit lifecycle.
7. For split routes, submit the 25% market child first.
8. Reconcile market child by order detail, paginated matching fills, and current position.
9. Require confirmed filled quantity and confirmed protection under the existing venue policy before submitting the 75% residual limit child.
10. Persist the residual limit order and provider ID only after read-back.
11. Round both children to venue step size and reject a zero/below-minimum child rather than silently changing the allocation.
12. Add tests proving no residual limit is submitted after a market-child timeout or provider-readback error.
13. Add tests proving partial prior limit fills cannot cause total quantity to exceed the original signal quantity.
14. Run: `uv run pytest -q tests/unit/test_bitget_dispatcher.py tests/e2e/test_bitget_demo_live_cycle.py`.

---

## Task 5: Implement GET-only unknown-order reconciliation

**Objective:** Resolve ICP-style ambiguous states without replaying provider POSTs.

**Files:**
- Modify: `src/fatty_trader/execution/bitget_dispatcher.py` or create `src/fatty_trader/execution/bitget_reconciliation.py`
- Modify: `src/fatty_trader/exchanges/bitget/async_execution.py`
- Modify: `src/fatty_trader/storage/live_intents.py`
- Modify: `src/fatty_trader/execution/bitget_dispatch_repository.py`
- Test: new `tests/unit/test_bitget_reconciliation.py`
- Test: relevant integration test

**Steps:**
1. Create a reconciliation function accepting an intent and performing only:
   - order detail by client OID/provider order ID,
   - matching fills by client OID/provider order ID,
   - current symbol position read,
   - pending-order read.
2. Normalize provider fills through one shared normalization function.
3. Query all relevant fill pages/time windows before classifying no-fill evidence; a truncated or failed fill read is `UNKNOWN`, not empty fills.
4. Classify evidence:
   - detail filled/partial → reconcile with exact quantity/price/fee/fill IDs;
   - `40109` plus zero matching fills plus successful exhaustive fills read plus successful flat-position read → terminal `REJECTED` with reason `provider-flat-no-order-or-fill` and an audit row;
   - detail absent but non-flat position or matching fills → `UNKNOWN`/reconciliation-pending, never rejected;
   - transport/read failure, pagination failure, or symbol-position ambiguity → remain `UNKNOWN`, schedule retry with bounded backoff;
   - conflicting evidence → manual-review/degraded state.
5. Enforce one provider POST per client OID. The reconciler must have no POST capability in its interface.
6. Add an audit row for every reconciliation attempt, evidence snapshot, classification, and approval/reference where required. Routine GET-only attempts must not require an operator approval reference; use a dedicated reconciliation-attempt record with nullable approval metadata.
7. Add a durable reconciliation worker contract: claim/lease unresolved children, `next_attempt_at`, bounded exponential backoff, max-age/manual-review transition, startup scan, concurrency lock, metrics, and alerting. Wire it into the appropriate Compose service and verify restart recovery.
8. Add stale-UNKNOWN timeout and alerting, but do not auto-reject without provider evidence.
9. Add tests for ICP evidence: `40109`, no fills after exhaustive pagination, flat position → terminal rejection with no retry.
10. Add tests for a filled market order whose detail is temporarily unavailable but matching fill exists → filled/reconciled.
11. Add tests for provider fill-history pagination failure and position-read failure → remains `UNKNOWN`.
12. Run the focused reconciliation tests.

---

## Task 6: Correct fill persistence and PnL binding

**Objective:** Ensure every provider fill updates both the intent and the fills ledger exactly once.

**Files:**
- Modify: `src/fatty_trader/storage/live_intents.py`
- Modify: `src/fatty_trader/exchanges/bitget/live.py`
- Modify: `scripts/health_report.py`
- Test: fill persistence/PnL unit and integration tests

**Steps:**
1. Require reconciled filled intents to contain provider order ID, filled quantity, average price, fee, and provider fill IDs.
2. Insert normalized provider fills with a unique `(exchange, provider_fill_id)` constraint.
3. Treat Bitget negative fee fields as fee magnitude while preserving fee currency.
4. Store provider realized PnL from `profit`/`totalProfits` consistently.
5. Make repeated reconciliation idempotent; the second read-back must not duplicate fills or PnL.
6. Add report fields separating:
   - gross realized profit,
   - gross realized loss,
   - fees,
   - realized net,
   - open uPnL.
7. Add a regression test for the BONK close pattern: one fill, 8,843 quantity, 0.003418 price, 3.572572 realized profit, one ledger row.
8. Define stable enum values for route reasons, reconciliation reasons, and health states; do not use free-form strings as dashboard/alert contracts.
9. Define child-level fill ownership and batch-level aggregation for partial market fills, partial residual fills, cancellation remainder, and exactly-once PnL attribution.
10. Test repeated reconciliation, partial market + partial residual fills, overfill prevention, and aggregate quantity never exceeding the original batch quantity.

**Objective:** Prevent TP/SL/close actions from leaving a residual entry limit or ambiguous exposure.

**Files:**
- Modify: `src/fatty_trader/execution/source_management.py`
- Modify: `src/fatty_trader/execution/bitget_dispatcher.py`
- Modify: `src/fatty_trader/operator/bitget_gateway.py`
- Test: source-management and residual-order tests

**Steps:**
1. Before TP1, SL-to-entry, or full close, resolve the active entry batch.
2. If a residual limit exists, persist a cancellation intent before cancelling that exact provider order.
3. Read back cancellation and absence from pending orders.
4. Only then perform position management.
5. Implement explicit provider limit-order placement, detail, pending-order, and cancellation APIs with deterministic client OIDs.
6. Apply signal TTL plus hard maximum expiry to every residual limit; startup reconciliation must find and cancel orphaned provider residuals before new entries.
7. If child quantity/notional validation fails while a prior limit is active, cancel and reconcile that exact limit or keep the batch explicitly pending; never leave it active and unmanaged.
8. If cancellation is ambiguous, keep the action reconciliation-pending and do not partially close or replace protection.
9. Add tests for exactly-one-position and residual-order preconditions, expiry, cancel timeout, restart, and stale provider orders.

---

## Task 8: Fix health and operator reporting

**Objective:** Make the report accurately expose degraded protection, UNKNOWN state, stale residuals, and provider/ledger drift.

**Files:**
- Modify: `scripts/health_report.py`
- Modify: `src/fatty_trader/operator/health.py`
- Modify: `src/fatty_trader/operator/live_commands.py`
- Modify: `tests/unit/test_health_report.py` and operator-path tests

**Steps:**
1. Report entry batches and child states separately from provider positions.
2. Show counts for:
   - active UNKNOWN intents,
   - reconciliation-pending intents,
   - active residual limits,
   - stale fallback rows,
   - provider/DB position drift.
3. Return `DEGRADED` when protection watchdog is stale or any unresolved UNKNOWN exists.
4. Do not show `OK` merely because the main monitor loop is alive.
5. Keep dynamic values escaped and Telegram-safe.
6. Make the health command read-only and verify it never invokes provider mutation methods.
7. Add a regression fixture for the post-close state: provider 0, DB 0, fallback 0, UNKNOWN 0, PnL includes verified close fill.

---

## Task 9: Add failure-injection and restart tests

**Objective:** Prove the system behaves correctly through the exact failures that caused ICP uncertainty.

**Files:**
- Create/modify: `tests/e2e/test_bitget_entry_lifecycle.py`
- Modify: demo provider fixture in `tests/e2e/test_bitget_demo_live_cycle.py`
- Add fixtures under `tests/fixtures/` if needed

**Scenarios:**
1. Market POST succeeds, order-detail GET returns `40109`, no fills, position flat → rejected; no retry.
2. Market POST succeeds, order-detail GET times out, matching fill exists → reconciled filled.
3. Market POST times out, order detail later shows filled → reconciled; exactly one provider POST.
4. Market child ambiguous → residual limit is not submitted.
5. Limit was working, mark moves slightly away → 25% market + 75% entry limit.
6. Signal starts beyond entry with no limit history → normal pullback route, not just-departed split.
7. Restart between intent persistence and provider POST → durable state prevents duplicate POST.
8. Restart after provider fill but before DB update → reconciliation restores the exact fill once.
9. Provider flat while stale fallback rows exist → rows become cancelled with reason and audit.

---

## Task 10: Verification, staged deployment, and operational guardrails

**Objective:** Validate the implementation without exposing live capital to an unproven route.

**Files:**
- Modify only intended source/tests/config files.
- Preserve unrelated existing worktree changes.

**Steps:**
1. Run focused unit tests for routing, intents, reconciliation, fills, and reporting.
2. Run the demo/e2e lifecycle suite with forced timeout, `40109`, delayed detail, and duplicate-readback cases.
3. Run quality gates:
   ```bash
   uv run pytest -q
   uv run ruff check src tests
   uv run ruff format --check src tests
   uv run mypy src
   git diff --check
   docker compose config --quiet
   ```
4. Run the provider read-only verifier:
   ```bash
   /bin/bash scripts/verify_bitget_runtime.sh
   ```
5. Test in DEMO/paper mode with execution mutations disabled.
6. Run a read-only shadow pass over representative historical signals and compare the proposed route against the existing route; any false-positive just-departed classification blocks promotion.
7. Rebuild and force-recreate only affected services; verify effective environment flags from inside containers.
8. Run a read-only report and confirm:
   - provider/DB flat or exactly matching,
   - no active UNKNOWN beyond the allowed test fixture,
   - no stale fallback rows,
   - no pending residual limits without a batch,
   - operator mutation gate remains disabled by default.
9. Perform a bounded canary only after explicit approval, with one controlled signal, exact provider read-back, native protection verification, and immediate gate closure if any evidence is missing.
10. Commit only intended files with a focused message; do not include existing unrelated changes.
11. Push only after local tests, container verification, and independent review pass.

## Risks and tradeoffs

- Requiring prior limit evidence may reduce fills, but it prevents the bot from misclassifying a normal pullback setup as a just-departed limit.
- A stricter fail-closed reconciler can leave some orders in `UNKNOWN` longer during provider outages; this is safer than replaying an entry.
- A 60-second just-departed window is a starting value, not a proven optimum. Keep it configurable and measure route outcomes before tuning.
- A residual 75% limit can remain unfilled. It must have an explicit expiry/cancellation policy and must be cancelled before source-management actions.
- Existing live/account data and unrelated worktree modifications must not be rewritten as part of this feature.
- The protection stream remains a separate P1 issue; this plan must not enable stream mutations merely because entry routing is fixed.

## Open questions to resolve during implementation

1. What exact mark-distance threshold should define `near-limit`: 0.25%, 0.5%, or a symbol-volatility-adjusted value? Start with 0.5% behind configuration unless existing product requirements specify otherwise.
2. How long should a limit remain eligible as “just departed”: 30, 60, or 120 seconds? Start with 60 seconds and record the decision in configuration/docs.
3. What is the residual-limit expiry: signal TTL, fixed seconds, or until the next source-management action? Prefer an explicit signal TTL with a hard maximum.
4. Should the near-limit route be allowed when the original limit was acknowledged but not yet provider-visible in pending orders? Default no; require provider acknowledgement and durable evidence.
5. Should `RECONCILED_NO_ORDER` map to `REJECTED` in the current dispatch state model, or should the dispatch state machine gain a distinct terminal state? Resolve before migration and update all report/notification paths together.

## Definition of done

- The router cannot infer just-departed status from current price alone.
- Entry batches and both child intents survive restart and are idempotent.
- Ambiguous provider responses are reconciled GET-only and never blindly retried.
- ICP-style `40109`/no-fill/flat evidence becomes terminal and audited.
- Verified fills update intent, fills, and PnL exactly once.
- Residual limits cannot remain unmanaged during TP/SL/close actions.
- Health reporting exposes all degraded states and returns the correct status.
- Full tests, lint, type checks, Compose validation, runtime verification, and demo lifecycle tests pass.
- Live execution remains behind explicit approval and a bounded canary; no safety gate is silently opened.
