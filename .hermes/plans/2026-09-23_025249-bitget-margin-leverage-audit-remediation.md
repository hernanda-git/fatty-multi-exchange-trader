# Bitget Async Margin, Leverage, and Balance Admission Remediation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make every async Bitget entry use and prove the exact leverage and isolated-margin assumptions used for sizing, and prevent concurrent/stale balance reads from over-committing margin.

**Architecture:** Replace the current quantity-only handoff with one immutable admission object containing provider snapshot evidence, the selected leverage, planned margin/notional/quantity, and a durable reservation ID. Serialize balance admission per exchange with a PostgreSQL advisory lock and durable reservation rows; the provider remains authoritative, while local reservations protect against concurrent workers between provider reads and fills. Configure leverage before submit, then read it back from the same account model; a mismatch is a pre-POST rejection.

**Tech Stack:** Python 3.11, asyncio, Decimal, Pydantic, psycopg/PostgreSQL, pytest.

---

## Audit findings and corrections

### Confirmed critical defects

1. **Async production execution does not set the leverage it sizes for.**
   - `src/fatty_trader/execution/bitget_dispatcher.py:150` computes a `SizingPlan`, but passes only `plan.quantity` at line 192.
   - `src/fatty_trader/execution/bitget_dispatch_execution.py:52-113` builds `LiveIntentRecord` without leverage.
   - `src/fatty_trader/exchanges/bitget/async_execution.py:164-184` calls `AsyncBitgetVenue.preflight()` then immediately posts an order. It never calls the existing client method `BitgetAsyncClient.set_leverage()` (`client.py:266-282`).
   - Result: `SizingPlan.effective_leverage` is discarded and the provider may retain a different per-symbol leverage.

2. **The stated `_try_margin()` path is not the live dispatcher path.**
   - `risk/live_policy.py` implements the ascending 20–50× search, but `BitgetDispatcher` calls `risk.sizing.minimum_safe_plan()` instead.
   - The currently active sizing path derives a single `effective_leverage` using `VenueRiskConfig.default_leverage` (`risk/sizing.py:18-68`). It has no liquidation/SL validation from `live_policy.py`.
   - Do not only wire `_try_margin()` output; consolidate the production path onto one sizing contract so the persisted/executed leverage cannot diverge from the audited sizing policy.

3. **Balance admission has a TOCTOU and concurrency gap.**
   - `_bitget_dispatch_preflight()` reads `snapshot.available_balance` once (`service.py:369-400`), then execution runs another account preflight (`async_execution.py:167`). Neither read has a freshness bound, reservation, or exchange-wide serialization.
   - The existing `canary_entry_reservations` cap protects order count only (`bitget_dispatch_repository.py:61-82`); it does not reserve USDT margin.

4. **The schema is only partially ready for auditability.**
   - `live_order_intents` already has nullable `leverage` and `margin_mode` columns (`storage/schema.py:192-193`) but `PostgresLiveIntentStore` never writes/reads them.
   - `balance_snapshots` exists (`schema.py:219-229`) but no code inserts rows.
   - No persisted planned margin, balance-observed timestamp, reservation, or post-fill margin reconciliation exists.

5. **Margin mode is enforced, leverage is only checked for parity.**
   - `AsyncBitgetVenue.preflight()` correctly converges to isolated mode with read-back/retry (`async_venue.py:48-77`), but only rejects unequal long/short leverage (`80-81`), not a wrong selected leverage.

### Safety decision

Keep `BITGET_OPERATOR_MUTATIONS_ENABLED=0` unchanged. This work concerns the signal execution path behind its existing explicit execution/canary gates; it must not add an operator-command bypass, auto-enable any mutation gate, or invoke raw API fallbacks.

## Acceptance invariants

- An entry POST is impossible unless the durable intent says `leverage == planned_leverage`, `margin_mode == ISOLATED`, and Bitget read-back confirms the same leverage immediately before POST.
- A provider failure, set/read-back mismatch, stale account snapshot, missing reservation, or reservation-expiry condition rejects before `place_entry_order()`.
- At most the configured available-balance headroom minus active margin reservations can be admitted across concurrent Bitget workers.
- Each admission writes a balance snapshot and a reservation/audit record before the entry POST; history is append-only.
- Terminal entry outcomes release their active margin reservation. `UNKNOWN` remains reserved until reconciliation proves release/consumption, expiry policy escalates it, or an explicit reconciliation action records the outcome.
- Fill reconciliation records planned versus provider-observed leverage/margin and latches new entries closed for a material mismatch; it never invents a provider value.

## Task 1: Freeze the production sizing and admission contract

**Objective:** Introduce a single immutable payload instead of transmitting only quantity through the dispatcher.

**Files:**
- Modify: `src/fatty_trader/execution/bitget_dispatcher.py:39-67, 147-195`
- Modify: `src/fatty_trader/execution/bitget_dispatch_execution.py:52-113`
- Modify: `src/fatty_trader/domain/models.py:65-73`
- Create: `src/fatty_trader/execution/bitget_admission.py`
- Test: `tests/unit/test_bitget_dispatcher.py`
- Test: `tests/unit/test_bitget_dispatch_execution_adapter.py`

**Step 1: Write failing contract tests.**

Assert that execution receives all of the following, not just a `Decimal` quantity:

```python
assert submission.quantity == Decimal("0.004")
assert submission.effective_leverage == 20
assert submission.planned_margin_usdt == Decimal("10")
assert submission.planned_notional_usdt == Decimal("256")
assert submission.margin_mode == "ISOLATED"
assert submission.balance_snapshot_id is not None
assert submission.margin_reservation_id is not None
```

Add a replay test proving the same durable intent cannot be reinterpreted with a different leverage.

**Step 2: Run the focused tests and verify RED.**

Run:
```bash
pytest tests/unit/test_bitget_dispatcher.py tests/unit/test_bitget_dispatch_execution_adapter.py -q
```
Expected: failures because the protocols and adapter accept only `quantity`.

**Step 3: Add immutable types.**

Create a frozen Pydantic/dataclass boundary such as:

```python
@dataclass(frozen=True)
class BitgetEntrySubmission:
    quantity: Decimal
    effective_leverage: int
    planned_margin_usdt: Decimal
    planned_notional_usdt: Decimal
    margin_mode: str
    balance_snapshot_id: UUID
    margin_reservation_id: UUID
    observed_at: datetime
```

Validate positive quantities/margin/notional, `effective_leverage >= 1`, uppercase `ISOLATED`, and timezone-aware `observed_at`. Do not duplicate mutable provider payloads in this object.

**Step 4: Change interfaces atomically.**

- Change `EntryExecution.submit_entry()` and `RoutedEntryExecution.submit_entry_route()` to accept `BitgetEntrySubmission`.
- Update the dispatcher to build/pass this object.
- Update `BitgetDispatchExecution._intent()` to copy planned leverage/margin/mode and reservation identity into `LiveIntentRecord`.
- Keep the deterministic `client_oid` rules unchanged.

**Step 5: Run focused tests and commit.**

Run the command from Step 2; expected: PASS. Commit:
```bash
git add src/fatty_trader/domain/models.py src/fatty_trader/execution/bitget_admission.py src/fatty_trader/execution/bitget_dispatcher.py src/fatty_trader/execution/bitget_dispatch_execution.py tests/unit/test_bitget_dispatcher.py tests/unit/test_bitget_dispatch_execution_adapter.py
git commit -m "feat: carry Bitget sizing admission through dispatch"
```

## Task 2: Add durable balance snapshot and margin-reservation storage

**Objective:** Persist the exact balance evidence and serialize margin commitments safely across workers.

**Files:**
- Modify: `src/fatty_trader/storage/migrations.py` (append migration 14 only)
- Modify: `src/fatty_trader/storage/schema.py` (keep fresh-install schema equivalent)
- Create: `src/fatty_trader/storage/balance_reservations.py`
- Modify: `src/fatty_trader/exchanges/bitget/live.py:127-150` (record fields/protocols)
- Test: `tests/unit/test_live_dispatch_migration.py`
- Create: `tests/unit/test_balance_reservations.py`

**Step 1: Write failing migration/repository tests.**

Test for additive SQL that creates `bitget_margin_reservations` with:
- UUID primary key; `exchange`, `dispatch_id`, `client_order_id`, and `balance_snapshot_id` foreign/key relationships as appropriate.
- `planned_margin_usdt NUMERIC NOT NULL CHECK (> 0)`.
- state limited to `reserved`, `consumed`, `released`, `unknown`.
- `expires_at`, `created_at`, `resolved_at`, `resolution_reason`.
- unique active reservation per dispatch and an index for `exchange,state,expires_at`.

Test that `reserve()` contains `pg_advisory_xact_lock(hashtext(exchange))`, expires old reservations inside the same transaction, compares `available_balance * headroom` to `SUM(planned_margin_usdt)` for active reservations, inserts the snapshot, then inserts the reservation. It must return a typed rejection reason, not a boolean-only result.

**Step 2: Run RED tests.**

Run:
```bash
pytest tests/unit/test_live_dispatch_migration.py tests/unit/test_balance_reservations.py -q
```
Expected: FAIL because migration 14/repository do not exist.

**Step 3: Append migration 14; never alter previous migrations.**

Add the table and constraints above. Add immutable audit fields to `live_order_intents` only if they are not represented by a reservation relationship: `planned_margin_usdt`, `balance_snapshot_id`, and `margin_reservation_id`. Existing nullable `leverage` and `margin_mode` are reused.

Keep `balance_snapshots` immutable. If account fields from Bitget are unavailable, do not substitute `available` as total/equity; extend the read model first or reject snapshot persistence clearly.

**Step 4: Implement `PostgresBitgetMarginReservationRepository`.**

Use one transaction:
1. advisory-lock `bitget`;
2. mark only expired `reserved` rows `unknown` (not silently released) and emit an outbox alert;
3. insert one `balance_snapshots` row from the just-read provider account;
4. calculate currently active reservations (`reserved` and `unknown`, never terminal/released) for Bitget;
5. admit only when `new_margin + active_reserved <= available_balance * configured_headroom`;
6. insert a `reserved` row and return its IDs.

A DB lock cannot make Bitget's remote balance atomic. The invariant is therefore local serialization plus a fresh provider read performed while admission is serialized; note and test this explicitly.

**Step 5: Implement deterministic terminal resolution.**

- `FILLED`/`PARTIALLY_FILLED`: mark reservation `consumed`; retain it for audit and let post-fill reconciliation determine actual locked margin.
- `REJECTED`/`CANCELLED`: mark `released`.
- `UNKNOWN`: retain as `unknown`; do not free funds automatically.
- Any update must be idempotent and must not erase prior audit data.

**Step 6: Run focused tests and commit.**

```bash
pytest tests/unit/test_live_dispatch_migration.py tests/unit/test_balance_reservations.py -q
git add src/fatty_trader/storage/migrations.py src/fatty_trader/storage/schema.py src/fatty_trader/storage/balance_reservations.py src/fatty_trader/exchanges/bitget/live.py tests/unit/test_live_dispatch_migration.py tests/unit/test_balance_reservations.py
git commit -m "feat: reserve Bitget margin with durable balance evidence"
```

## Task 3: Make account snapshots complete and freshness-bounded

**Objective:** Read one auditable account snapshot per admission and reject untrustworthy values before sizing or reservation.

**Files:**
- Modify: `src/fatty_trader/exchanges/bitget/read_model.py`
- Modify: `src/fatty_trader/exchanges/bitget/async_venue.py`
- Modify: `src/fatty_trader/service.py:361-403`
- Test: `tests/unit/test_bitget_async_venue.py`
- Create: `tests/unit/test_bitget_dispatch_preflight.py`

**Step 1: Write failing tests.**

Cover exact provider parsing for documented `available`, `accountEquity`/documented equivalent, `usdtEquity`/documented total equivalent, `marginCoin`, server/account observation timestamp, and no fallback for missing required fields. Assert an explicit `BITGET_BALANCE_MAX_AGE_SECONDS` default (recommend 5 seconds) and `BITGET_BALANCE_RESERVATION_TTL_SECONDS` default (recommend 30 seconds) reject zero/negative/non-finite/missing data and invalid config.

**Step 2: Run RED.**

```bash
pytest tests/unit/test_bitget_async_venue.py tests/unit/test_bitget_dispatch_preflight.py -q
```

**Step 3: Extend `BitgetAccountState` and `BitgetPreflightSnapshot`.**

Include `total_balance`, `available`, `equity`, `margin_coin`, and an application-side UTC `observed_at` taken immediately after successful account response parsing. The local observation timestamp is the only freshness value the application can prove unless Bitget supplies a documented server account timestamp.

**Step 4: Create one admission preflight.**

Replace `_bitget_dispatch_preflight()`'s tuple result with a typed admission result that:
- checks isolated/one-way account state;
- fetches current metadata and ticker;
- uses the **same current price** for sizing and submission validation;
- calculates exactly one production sizing plan;
- acquires the margin reservation while its fresh snapshot is under the exchange admission lock;
- returns the immutable `BitgetEntrySubmission` plus instrument metadata needed downstream.

Do not retain the current second independent account preflight in `AsyncBitgetExecution.submit_entry()`; it creates the race the new contract removes. Preserve required order metadata/price validation, but use the carried fresh admission values.

**Step 5: Run tests and commit.**

```bash
pytest tests/unit/test_bitget_async_venue.py tests/unit/test_bitget_dispatch_preflight.py -q
git add src/fatty_trader/exchanges/bitget/read_model.py src/fatty_trader/exchanges/bitget/async_venue.py src/fatty_trader/service.py tests/unit/test_bitget_async_venue.py tests/unit/test_bitget_dispatch_preflight.py
git commit -m "feat: admit Bitget entries from fresh balance snapshots"
```

## Task 4: Unify sizing and carry exact leverage/margin into the intent

**Objective:** Eliminate the dormant-vs-production sizing split and prove Decimal-safe sizing against the carried provider snapshot.

**Files:**
- Modify: `src/fatty_trader/risk/live_policy.py`
- Modify: `src/fatty_trader/risk/sizing.py` (deprecate or make a thin compatibility wrapper only)
- Modify: `src/fatty_trader/service.py:361-403`
- Modify: `src/fatty_trader/exchanges/bitget/live.py:127-142`
- Modify: `src/fatty_trader/storage/live_intents.py`
- Test: `tests/unit/test_live_sizing.py`
- Create: `tests/unit/test_bitget_admission_sizing.py`

**Step 1: Write failing integration-level sizing tests.**

For a fixed metadata/snapshot/signal fixture, assert the same `effective_leverage`, margin, quantity, notional, rounded entry and stop guard are persisted in `BitgetEntrySubmission`, `LiveIntentRecord`, and database SQL parameters. Cover:
- lowest valid leverage selected in ascending `[20, min(50, symbol/risk cap)]`;
- min-notional rounding;
- all-in fallback only for zero active positions;
- no plan if SL/liquidation guard fails;
- a changed `BITGET_MIN_LEVERAGE` affects the single production path;
- planned notional and reservation exactly agree.

**Step 2: Run RED.**

```bash
pytest tests/unit/test_live_sizing.py tests/unit/test_bitget_admission_sizing.py -q
```

**Step 3: Select `plan_live_position()` as the single live sizing authority.**

Wire its `LiveSizingDecision` into dispatcher admission. Map environment settings into `BitgetLiveRiskConfig` with strict validators; do not rely on a separate `VenueRiskConfig.default_leverage` calculation. Preserve `_MIN_LIVE_LEVERAGE=20` and cap at the symbol's metadata/risk maximum.

If `minimum_safe_plan()` is still needed for non-Bitget callers, leave it isolated with a deprecation comment and dedicated tests. It must no longer be reachable from `BitgetDispatcher`.

**Step 4: Persist intent fields.**

Add to `LiveIntentRecord` at minimum:

```python
planned_leverage: int | None = None
planned_margin_usdt: Decimal | None = None
margin_mode: str | None = None
balance_snapshot_id: UUID | None = None
margin_reservation_id: UUID | None = None
```

For `role="ENTRY"`, require all five values. Emergency-close intents deliberately leave them null. Update every `INSERT`, `SELECT`, `UPDATE`, in-memory store, reconciliation row mapper, and fake fixture.

**Step 5: Run tests and commit.**

```bash
pytest tests/unit/test_live_sizing.py tests/unit/test_bitget_admission_sizing.py tests/unit/test_bitget_dispatcher.py tests/unit/test_bitget_dispatch_execution_adapter.py -q
git add src/fatty_trader/risk/live_policy.py src/fatty_trader/risk/sizing.py src/fatty_trader/service.py src/fatty_trader/exchanges/bitget/live.py src/fatty_trader/storage/live_intents.py tests/unit/test_live_sizing.py tests/unit/test_bitget_admission_sizing.py tests/unit/test_bitget_dispatcher.py tests/unit/test_bitget_dispatch_execution_adapter.py
git commit -m "fix: use one Bitget live sizing authority"
```

## Task 5: Set and verify leverage before entry POST

**Objective:** Make the exchange configuration match durable admission before any entry order can leave the process.

**Files:**
- Modify: `src/fatty_trader/exchanges/bitget/async_venue.py`
- Modify: `src/fatty_trader/exchanges/bitget/async_execution.py:21-28, 164-184`
- Modify: `src/fatty_trader/exchanges/bitget/client.py` only if typed API normalization is required
- Test: `tests/unit/test_bitget_async_venue.py`
- Create: `tests/unit/test_bitget_async_execution_leverage.py`

**Step 1: Write failing tests.**

Use a fake async client recording calls. Assert order:
1. isolated mode convergence/read-back;
2. `set_leverage(symbol, leverage=str(planned_leverage))`;
3. `get_account(symbol)` read-back;
4. `place_entry_order()`.

Assert no order POST for: missing `set_leverage`, wrong response, propagation mismatch after one bounded retry, unequal long/short leverage, or record leverage differing from the submission. Assert the final account has both long and short leverage equal to the selected value in one-way mode.

**Step 2: Run RED.**

```bash
pytest tests/unit/test_bitget_async_venue.py tests/unit/test_bitget_async_execution_leverage.py -q
```

**Step 3: Implement `AsyncBitgetVenue.ensure_leverage()`.**

Extend `AsyncBitgetClient` with typed `set_leverage`. The method must:
- validate a positive integer requested leverage;
- require already-confirmed isolated and supported one-way mode;
- call `set_leverage` with the exact decimal-string integer;
- re-read account once; if mismatch, wait the existing bounded propagation delay and re-read once;
- reject on any mismatch; never submit an order based only on a successful POST response.

**Step 4: Enforce durable intent consistency.**

In `AsyncBitgetExecution.submit_entry()`, require `intent.planned_leverage` and `intent.margin_mode == "ISOLATED"`, invoke `ensure_leverage`, then run order validation from carried admission metadata/price and submit. Never call a fresh balance read in this method.

**Step 5: Run tests and commit.**

```bash
pytest tests/unit/test_bitget_async_venue.py tests/unit/test_bitget_async_execution_leverage.py tests/unit/test_bitget_async_execution.py -q
git add src/fatty_trader/exchanges/bitget/async_venue.py src/fatty_trader/exchanges/bitget/async_execution.py src/fatty_trader/exchanges/bitget/client.py tests/unit/test_bitget_async_venue.py tests/unit/test_bitget_async_execution_leverage.py
git commit -m "fix: enforce planned Bitget leverage before entry"
```

## Task 6: Resolve reservations from every execution outcome

**Objective:** Ensure balance commitments cannot leak or be prematurely released.

**Files:**
- Modify: `src/fatty_trader/execution/bitget_dispatcher.py:184-226`
- Modify: `src/fatty_trader/execution/bitget_dispatch_execution.py:75-142`
- Modify: `src/fatty_trader/storage/balance_reservations.py`
- Test: `tests/unit/test_bitget_dispatcher.py`
- Create: `tests/unit/test_bitget_margin_reservation_lifecycle.py`

**Step 1: Write failing state-machine tests.**

Assert exact resolution mapping for `FILLED`, `PARTIAL`, `REJECTED`, `ACKNOWLEDGED`, thrown exception, timeout, `UNKNOWN`, native-protection failure, and restart/replay. Verify that:
- `UNKNOWN` remains active;
- provider exception after intent creation yields no unrecorded release;
- `REJECTED` releases exactly once;
- `FILLED` becomes consumed exactly once;
- terminal dispatch transition and reservation state are consistent even under retries.

**Step 2: Run RED.**

```bash
pytest tests/unit/test_bitget_dispatcher.py tests/unit/test_bitget_margin_reservation_lifecycle.py -q
```

**Step 3: Add repository protocol and resolution calls.**

The dispatcher must resolve the reservation immediately after durable result persistence, before terminal dispatch transition. For failed transitions, retain a repairable `unknown` reservation and alert; do not falsely report completion.

Ensure reservation release handling is distinct from the existing count-only `canary_entry_reservations`; retain both protections.

**Step 4: Add a startup/reconciliation sweep.**

A read-only/reconciliation worker identifies expired `reserved`/`unknown` margin reservations, obtains provider order/position evidence, and resolves only when evidence proves the funds are not committed. Ambiguous records remain `unknown`, alert, and block affected allocation.

**Step 5: Run tests and commit.**

```bash
pytest tests/unit/test_bitget_dispatcher.py tests/unit/test_bitget_margin_reservation_lifecycle.py -q
git add src/fatty_trader/execution/bitget_dispatcher.py src/fatty_trader/execution/bitget_dispatch_execution.py src/fatty_trader/storage/balance_reservations.py tests/unit/test_bitget_dispatcher.py tests/unit/test_bitget_margin_reservation_lifecycle.py
git commit -m "fix: reconcile Bitget margin reservations by outcome"
```

## Task 7: Add post-fill margin/leverage reconciliation and operator visibility

**Objective:** Detect provider-state divergence after fill without claiming a false safety guarantee.

**Files:**
- Modify: `src/fatty_trader/exchanges/bitget/async_execution.py`
- Modify: `src/fatty_trader/exchanges/bitget/read_model.py`
- Modify: `src/fatty_trader/storage/balance_reservations.py`
- Modify: `src/fatty_trader/storage/schema.py` and append migration 15 only if a distinct reconciliation table is needed
- Modify: `scripts/health_report.py`
- Test: `tests/unit/test_bitget_async_execution.py`
- Test: `tests/unit/test_health_report_contract.py`
- Create: `tests/e2e/test_bitget_balance_admission_cycle.py`

**Step 1: Write failing tests.**

For confirmed fills, require a fresh provider position/account read and persist:
- planned leverage/margin/notional;
- observed leverage, margin mode, position quantity, entry/mark values, and any documented position-margin field;
- status `matched`, `within_tolerance`, `mismatch`, or `unavailable` with a reason.

Test that a leverage/mode mismatch blocks subsequent entries (via the existing fail-closed/degraded or kill-switch mechanism chosen explicitly in implementation) and emits an alert. Test that a missing documented provider margin field is reported `unavailable`, never synthesized from notional/leverage.

**Step 2: Run RED.**

```bash
pytest tests/unit/test_bitget_async_execution.py tests/unit/test_health_report_contract.py tests/e2e/test_bitget_balance_admission_cycle.py -q
```

**Step 3: Implement read-back and tolerance policy.**

Use provider position truth after filled/partial entry. Define a documented Decimal tolerance only for rounding/fees; leverage and margin mode must match exactly. Store the raw observed provider fields needed for audit (with secrets excluded).

**Step 4: Expose clear health evidence.**

Extend `scripts/health_report.py` with current reservation totals/state, newest balance snapshot age, and most recent post-fill reconciliation result. Keep historical snapshots visible; do not show credentials or provider private IDs.

**Step 5: Run tests and commit.**

```bash
pytest tests/unit/test_bitget_async_execution.py tests/unit/test_health_report_contract.py tests/e2e/test_bitget_balance_admission_cycle.py -q
git add src/fatty_trader/exchanges/bitget/async_execution.py src/fatty_trader/exchanges/bitget/read_model.py src/fatty_trader/storage/balance_reservations.py src/fatty_trader/storage/schema.py src/fatty_trader/storage/migrations.py scripts/health_report.py tests/unit/test_bitget_async_execution.py tests/unit/test_health_report_contract.py tests/e2e/test_bitget_balance_admission_cycle.py
git commit -m "feat: reconcile Bitget planned and observed margin"
```

## Task 8: Run complete verification, migration proof, and controlled environment checks

**Objective:** Prove local behavior, persistence, and DEMO-only provider semantics before any separately approved live canary.

**Files:**
- Modify as needed only to fix verified test failures.

**Step 1: Static and full test suite.**

```bash
uv run ruff check src tests scripts
uv run pytest -q
```
Expected: no lint failures; all tests pass.

**Step 2: Fresh PostgreSQL migration proof.**

Use the repository's existing test/database harness to apply migrations from empty state, assert migration versions include 14 (and 15 only if used), then re-run to prove idempotence. Verify the new columns/tables/indexes with `information_schema`; do not hand-edit production tables.

**Step 3: Deterministic concurrent-admission E2E.**

Start two dispatcher instances/fakes against the same PostgreSQL database and balance. Arrange margins whose sum exceeds permitted headroom. Assert exactly one reservation/order admission succeeds, the other rejects pre-POST, and only one fake `place_entry_order()` call occurs. Repeat with a rejected first order to prove the reservation releases.

**Step 4: DEMO read-only preflight.**

With DEMO credentials and `BITGET_EXECUTION_ENABLED=0`, run the authenticated account/metadata/preflight path. Verify balance snapshot persistence, no `set_leverage`, no order mutation, no protection mutation, and correct DEMO environment header. This is provider-schema evidence only.

**Step 5: Separately approved DEMO mutation canary.**

Only after an explicit user approval and a fresh configuration review: enable the existing DEMO execution gate for one allowlisted symbol/minimum size. Confirm provider read-back in order: isolated mode, selected leverage, entry fill, protection, position, margin/leverage reconciliation, terminal reservation state. On mismatch, stop new entries and retain audit records.

**Step 6: Do not promote to LIVE in this change.**

A LIVE canary remains a separate approval under `bitget-live-cutover-safety`, with distinct credentials, a 1× initial leverage policy unless that policy is separately changed, one symbol, one entry, minimum size, mandatory protection, and complete provider read-back.

## Risks, tradeoffs, and decisions required before implementation

- **Remote balance cannot be transactionally locked.** The recommended PostgreSQL advisory lock/reservation design prevents bot-internal races, not manual exchange activity or another external bot. Fresh post-admission/pre-POST verification reduces the window but cannot eliminate it. The bot must fail closed on provider balance/margin mismatch.
- **Changing the active sizing engine can change quantity.** This is intentional because the current report incorrectly identifies `_try_margin()` as the dispatcher path. Compare planned quantities for a representative set of queued/fixture signals before enabling execution.
- **Leverage is per symbol/provider account state.** If Bitget needs separate long/short settings even in one-way mode, the implementation must follow the live API response contract and require both values equal before entry.
- **Unknown reservations reduce availability by design.** Releasing them on timeout would reintroduce over-allocation. Reconciliation must resolve evidence rather than guessing.
- **Snapshot schema lacks an explicit provider timestamp.** Start with an app-side `observed_at`; add a provider timestamp only after confirming Bitget's documented account response field.
- **Open question:** confirm whether the intended production policy is the `live_policy.py` ascending 20–50×/liquidation-guard policy. This plan assumes yes because the audit explicitly describes it. If not, preserve `minimum_safe_plan()` but still carry/set/read-back its chosen leverage and add equivalent stop/liquidation validation before enabling LIVE.

## Completion checklist

- [ ] All async entry routes carry one immutable admission object rather than quantity alone.
- [ ] `live_order_intents` persists planned leverage/margin/mode and linkage to balance snapshot/reservation.
- [ ] Bitget leverage is set and verified before every entry POST.
- [ ] Balance snapshot and margin reservation are atomically recorded under an exchange lock.
- [ ] Concurrent admission, stale snapshot, and unknown-outcome tests pass.
- [ ] Provider post-fill leverage/margin reconciliation is persisted and health-reported.
- [ ] Full tests, migration idempotence, and DEMO read-only proof pass.
- [ ] No execution gate, operator mutation gate, or LIVE cap was loosened automatically.
