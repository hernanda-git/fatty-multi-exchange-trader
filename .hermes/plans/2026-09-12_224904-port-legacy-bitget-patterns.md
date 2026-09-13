# Legacy Bitget Reliability Patterns Port Plan

> **For Hermes:** Implement this plan task-by-task with TDD, independent review, and closed-by-default execution.

**Goal:** Port the proven reliability patterns from `/home/valarion/bitget-listener` into `/home/valarion/apps/fatty-multi-exchange-trader` without copying the legacy bot's topology or known limitations, so source signals, Bitget execution, protection, reconciliation, and account telemetry form one verifiable lifecycle.

**Architecture:** Keep `fatty-multi-exchange-trader`'s current Compose/PostgreSQL dispatcher architecture and `live_order_intents` as the durable intent boundary. Adapt the legacy bot's trace spine, fee-aware ledger, private/public stream model, dead-man gate, deterministic management path, persisted bot-side protection, exact provider read-back, and fail-closed tests into the existing modules. Do not replace the current stack with the legacy monolith or reintroduce the legacy hidden `bot_common` dependency.

**Tech Stack:** Python 3.11, PostgreSQL, Docker Compose, Bitget V2 LIVE REST, optional Bitget V2 WebSocket, pytest/pytest-asyncio, Ruff, Mypy.

---

## Evidence and constraints

### Current production findings to solve

- The current LIVE account is flat, but `BITGET_EXECUTION_ENABLED=1` and the canary cap is effectively full: 5 terminal reservations remain for a cap of 5.
- `live_order_intents` contains state summaries, but `orders`, `fills`, `positions`, `balance_snapshots`, and `position_snapshots` are empty despite real provider fills.
- Provider fills include real fees and prices, while local intents mostly lack `filled_price`, `fee`, and `provider_fill_ids`.
- Dispatch rows can end in `FILLED`/`UNKNOWN` without a matching final `dispatch_transitions` row.
- The fallback monitor has an uncommitted working-tree change that removed its provider-flat check; the running monitor uses that dirty file and attempted a close against a position already closed on Bitget.
- Source management parsed `sl to entry $ETHFI` as `SLUSDT`, then marked the update reconciled although no provider intent exists.
- One source message produced two GRASS canonical rows and two real entries; an explicit XPL setup produced no canonical signal or dispatch.
- Full local tests are not green: 315 passed, 17 failed. Ruff, format, and Mypy also fail.

### Proven legacy patterns worth adapting

From `/home/valarion/bitget-listener` at `c14abf5`:

1. **Trace spine:** durable `RECEIVED → PARSER → GATE_EVALUATED → DECISION → ORDER_SUBMITTED/ORDER_FAILED → BOOKKEEPING → TERMINAL` events, with receipt-before-parse and idempotent trace writes.
2. **Fee-aware durable ledger:** provider fill price, quantity, fee, liquidation price, exit reason, partial-close legs, and equity history are persisted; zero/unknown values are not silently fabricated.
3. **Deterministic execution boundary:** validate before POST, deterministic arithmetic repair once, decision/client-OID deduplication, real fill read-back, and no LLM order repair.
4. **Exact close semantics:** sync live position size before reduce-only close, never inflate reduce-only quantity to satisfy entry min-notional, and confirm provider flatness after the close.
5. **Persisted bot-side protection:** when Bitget native TPSL is unreliable, persist armed SL/TP levels and re-arm them after restart; surface `DEGRADED`/`UNPROTECTED` honestly.
6. **Private/public stream split:** public mark feed plus private order/position/account events, reconnect/backoff, literal Bitget heartbeat, and a real liveness sentinel when no trade symbols are subscribed.
7. **Dead-man behavior:** halt new entries on stale feed, cancel only non-protective entry orders, preserve reduce-only protection, and notify the operator.
8. **Management fast path:** deterministic `sl_to_entry`, TP1 partial, full close, and numeric modifications bypass the LLM; historical catch-up replays management only and never stale entries.
9. **Provider-state preflight:** inspect position mode, per-symbol margin mode, open positions, tradability, and balance before mutation; repair account mode only when flat.
10. **Verification discipline:** fakes-only tests, read-only live probes, no-creds paper smoke, and explicit go-live gates.

### Patterns explicitly not copied

- Do not copy the legacy default `crossed` margin contract blindly; the current Bitget live execution contract requires per-symbol `isolated`.
- Do not copy legacy raw `urllib` signing or interpolated SQL. Route provider calls through the current Bitget client and use parameterized PostgreSQL repositories.
- Do not copy the legacy monolith or its untracked `bot_common` runtime dependency.
- Do not treat a native TPSL acknowledgement or plan ID as protection proof. Require the current provider read-back contract and a durable fallback state.
- Do not auto-change margin/position mode with an open position.
- Do not introduce WebSocket execution as a substitute for REST reconciliation; WS is an event accelerator and REST remains the recovery authority.

---

## Phase 0: Freeze the current safety boundary and establish a baseline

### Task 0.1: Preserve the dirty runtime change before any implementation

**Files:**
- Read-only: `src/fatty_trader/execution/bitget_fallback_protection.py`
- Evidence: current `git diff`, running `monitor-bitget` file hash

**Steps:**
1. Save the existing uncommitted diff as an audit artifact outside the production runtime.
2. Record local `HEAD`, local working-tree hash, dispatcher-container hash, and monitor-container hash.
3. Do not discard or deploy the diff until the fallback flat-position regression is covered.

**Acceptance:** The implementation branch can restore the exact pre-worktree state, and no unreviewed monitor code is overwritten.

### Task 0.2: Record a clean baseline

**Commands:**
```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
python -m compileall -q src scripts
```

**Acceptance:** Baseline pass/fail counts are stored in the phase report. Existing failures are not hidden or rewritten.

### Task 0.3: Keep provider mutation closed during implementation

**Runtime rule:** Run unit and mocked E2E tests with provider POST disabled. Before any deployed verification, keep the Bitget kill switch latched or `BITGET_EXECUTION_ENABLED=0`; read-only probes are allowed. A live canary is a separate, explicitly approved phase.

**Acceptance:** A closed-cutover test proves zero provider POSTs, including preflight and fallback paths.

---

## Phase 1: Restore the source trace and parser correctness

### Task 1.1: Add a durable trace-event seam to the current pipeline

**Files:**
- Modify: `src/fatty_trader/intake/persistence.py`
- Modify: `src/fatty_trader/analyzer/postgres_worker.py`
- Modify: `src/fatty_trader/execution/bitget_dispatch_repository.py`
- Modify: `src/fatty_trader/storage/reconciliation.py`
- Test: `tests/unit/test_analyzer_worker.py`, `tests/unit/test_bitget_reconciliation.py`, new `tests/unit/test_trace_lifecycle.py`

**Design:** Use the existing PostgreSQL tables rather than creating a second database. For each source revision, persist one analysis outcome even when it is manual review or no-signal. For each dispatch and intent, persist every state transition transactionally and make the final state agree with the last transition.

**Required terminal outcomes:** `MANUAL_REVIEW`, `REJECTED`, `PENDING`, `UNKNOWN`, `FILLED`, `RECONCILED`, and `FAILED` must all be explicit, with a reason and source/message reference.

**Acceptance:** A fixture can be read back from raw Telegram row through canonical/analysis outcome, dispatch, intent, provider read-back result, protection result, and notification without any stage being inferred from another table.

### Task 1.2: Fix deterministic parser coverage from observed source shapes

**Files:**
- Modify: `src/fatty_trader/analyzer/deterministic_parser.py`
- Test: new `tests/unit/test_deterministic_parser_legacy_fixtures.py`

**Fixtures:**
- `#XPL $XPL LONG TRADE ENTRY: 0.098 - 0.096 TARGET: 0.117 Stoploss: 0.09475` must produce one canonical LONG signal, preserving the entry range explicitly or selecting a documented trigger price.
- `#PUMP ... TARGETS: 0.004438 - 0.004915 ...` must preserve both targets.
- `MANUAL SIGNAL: GRASSUSDT LONG @ ... TP1 ... TP2 ...` must parse deterministically.
- Invalid/incomplete prose must remain manual review and must not create a dispatch.

**Acceptance:** XPL no longer disappears; target plural/range syntax remains covered; parser does not use market data to invent missing fields.

### Task 1.3: Make canonical signal and dispatch creation idempotent

**Files:**
- Modify: `src/fatty_trader/storage/schema.py`/additive migration in `src/fatty_trader/storage/migrations.py` only if the existing unique key is insufficient
- Modify: `src/fatty_trader/analyzer/postgres_worker.py`
- Modify: `src/fatty_trader/intake/persistence.py`
- Test: new `tests/unit/test_signal_idempotency.py`

**Rules:**
- One `(telegram_message_uuid, source_revision)` produces at most one canonical signal.
- One canonical signal/revision/exchange produces at most one dispatch.
- Reprocessing an unchanged source row is a no-op.
- A changed source revision is a new auditable revision, not a duplicate of the old revision.

**Acceptance:** Replaying the manual GRASS source message twice creates one canonical signal and one Bitget dispatch; a real revision creates a second revision only when the source hash changes.

### Task 1.4: Fix management symbol normalization and no-position semantics

**Files:**
- Modify: `src/fatty_trader/analyzer/trade_management.py`
- Modify: `src/fatty_trader/execution/source_management.py`
- Modify: `src/fatty_trader/storage/source_management.py`
- Test: new `tests/unit/test_source_management_parser.py`, existing source-management tests

**Rules:**
- Extract symbols only from `$TOKEN`, `#TOKEN`, or a complete `TOKENUSDT` token; never treat `sl`, `to`, `entry`, or `be` as the symbol.
- Test both word orders: `tp1 booked` and `book tp1`; `sl to entry $ETHFI` and `$ETHFI sl to entry`.
- A missing position is an explicit `NO_POSITION`/notification-only result, not `reconciled` provider execution.
- Persist a provider intent before a mutating POST; if mutations are disabled, do not mark the update as executed.

**Acceptance:** ETHFI resolves to `ETHFIUSDT`; zero provider intents remain zero when source management is disabled; the update state explains why no mutation occurred.

---

## Phase 2: Make the current intent ledger provider-complete

### Task 2.1: Persist every actual Bitget fill and fee

**Files:**
- Modify: `src/fatty_trader/exchanges/bitget/async_execution.py`
- Modify: `src/fatty_trader/exchanges/bitget/client.py` only for response normalization
- Modify: `src/fatty_trader/storage/live_intents.py`
- Modify: `src/fatty_trader/storage/reconciliation.py`
- Test: `tests/unit/test_bitget_reconciliation.py`, new `tests/unit/test_bitget_fill_persistence.py`

**Design:** Adapt the legacy `Fill` contract: match fills by client OID/provider order ID, compute quantity-weighted average price, sum real negative Bitget fees as positive cost, preserve provider fill IDs, and persist one row per provider fill in `fills`. Do not classify a fill from requested quantity alone.

**Acceptance:** A fixture containing the current EUL/ETHFI/INJ response shapes populates `filled_qty`, `filled_price`, `fee`, `provider_fill_ids`, and `fills` deterministically. Missing order detail plus matching fills is `FILLED`; missing both is `UNKNOWN`, never a fabricated fill.

### Task 2.2: Close the durable `orders`/`positions`/snapshot gap

**Files:**
- Modify: `src/fatty_trader/execution/bitget_dispatch_execution.py`
- Modify: `src/fatty_trader/execution/bitget_dispatcher.py`
- Modify: `src/fatty_trader/exchanges/bitget/reconciliation_live.py`
- Modify: `src/fatty_trader/storage/reconciliation.py`
- Test: `tests/e2e/test_bitget_dispatch_monitor_cycle.py`, new `tests/e2e/test_provider_readback_ledger.py`

**Design:** Keep `live_order_intents` as the POST idempotency boundary, then write provider-confirmed entry/close outcomes into the existing `orders`, `positions`, and `fills` tables. Write account and position snapshots from the monitor/reconciliation pass. Do not create a second legacy ledger schema.

**Acceptance:** A protected fake lifecycle leaves durable intent, order, fill, position, protection, and snapshot rows; a restart rehydrates the same state; a provider-flat read closes the local position.

### Task 2.3: Enforce transition/intent consistency

**Files:**
- Modify: `src/fatty_trader/execution/bitget_dispatch_repository.py`
- Modify: `src/fatty_trader/execution/bitget_dispatcher.py`
- Test: new `tests/unit/test_dispatch_transition_consistency.py`

**Rules:**
- Every direct state mutation must go through the repository transition method.
- `dispatches.state` must equal the last transition's `to_state`.
- Reconciliation from `UNKNOWN`/`SUBMITTING` to `FILLED`/`RECONCILED` must write its own transition and notification.
- Provider order/fill read-back must update the linked intent in the same logical reconciliation pass.

**Acceptance:** WLD/ETHFI/INJ-style `UNKNOWN → FILLED` paths are represented in both state and transition history; a test fails if code changes the final state without a transition.

### Task 2.4: Reconcile external/manual provider activity

**Files:**
- Modify: `src/fatty_trader/exchanges/bitget/reconciliation.py`
- Modify: `src/fatty_trader/execution/bitget_monitor.py`
- Test: new `tests/e2e/test_external_close_reconciliation.py`

**Design:** If Bitget is flat but the local position/fallback row is active, mark the local record `externally_closed`/`reconciled_external` with the provider order/fill evidence. Never submit a close POST merely because a stale local fallback threshold is breached.

**Acceptance:** A fixture matching the WEB ETHFI/EUL close makes the local fallback/position terminal without a second POST; the evidence stores provider order source and close fill.

---

## Phase 3: Rebuild protection around a durable bot-side fallback

### Task 3.1: Normalize protection outcomes and native-plan quirks

**Files:**
- Modify: `src/fatty_trader/exchanges/bitget/reconciliation_live.py`
- Modify: `src/fatty_trader/exchanges/bitget/async_execution.py`
- Test: `tests/unit/test_bitget_native_protection.py`, new `tests/unit/test_bitget_protection_quirks.py`

**Rules:**
- `400172` is an endpoint/symbol read quirk; use position `stopLossId`/`takeProfitId` fallback only when those fields are present.
- `43011` means native TPSL unsupported for that symbol; register bot-side fallback and mark `DEGRADED`, not `VENUE_PROTECTED`.
- Never pass `plan.quantity` as `entry_price`; carry the actual filled/entry price in the intent or position record.
- Native acknowledgement alone is not confirmation.

**Acceptance:** A GRASS/INJ-style `43011` creates a complete fallback record with the real entry price; a 400172 read does not falsely claim native protection; a missing provider protection result cannot silently pass.

### Task 3.2: Replace direct fallback POST/SQL with the current intent boundary

**Files:**
- Modify: `src/fatty_trader/execution/bitget_fallback_protection.py`
- Modify: `src/fatty_trader/execution/bitget_monitor.py`
- Modify: `src/fatty_trader/storage/live_intents.py`
- Test: new `tests/unit/test_fallback_monitor_safety.py`

**Design:** Keep a durable fallback table or fold it into the current position/protection model, but use parameterized repository calls and the existing Bitget client/intent path. Before a fallback close:
1. fresh `get_single_position(symbol)`;
2. if flat, mark external/manual close and issue zero POSTs;
3. if open, persist one deterministic reduce-only close intent;
4. POST once;
5. GET order/fill/position read-back;
6. mark terminal only after evidence.

**Acceptance:** The ETHFI stale-row reproduction issues zero provider POSTs when the account is flat; a timeout or unknown close cannot be retried blindly; a successful close is persisted with real fill data.

### Task 3.3: Persist armed protection across restart and clean it on close

**Files:**
- Modify: `src/fatty_trader/execution/bitget_fallback_protection.py` or a new `src/fatty_trader/storage/protection.py`
- Modify: `src/fatty_trader/execution/bitget_monitor.py`
- Test: new `tests/e2e/test_fallback_restart_recovery.py`

**Rules:**
- A confirmed live entry with native protection unavailable must persist exact SL/TP and entry price before monitor-only operation.
- On restart, load only protections whose provider position is still open.
- On provider-flat or confirmed close, mark the protection terminal and remove it from the active monitor set.
- Repeated monitor cycles must not create repeated close intents.

**Acceptance:** Restart with an open fallback position re-arms monitoring; restart with a flat provider does not re-arm or close; duplicate cycles produce one close intent.

---

## Phase 4: Fix account preflight, canary reservations, and dead-man behavior

### Task 4.1: Port legacy preflight semantics without changing the current margin contract

**Files:**
- Modify: `src/fatty_trader/exchanges/bitget/async_venue.py`
- Modify: `src/fatty_trader/exchanges/bitget/read_model.py`
- Test: `tests/unit/test_bitget_async_venue.py`, new `tests/unit/test_bitget_symbol_preflight.py`

**Rules:**
- Read account state per symbol, not only BTCUSDT.
- Require one-way mode and the configured per-symbol margin mode.
- Repair mode only when the provider is flat; after `set_margin_mode`, perform delayed read-back once before rejecting.
- Preserve explicit failure reasons for crossed symbols instead of silently changing an open position.

**Acceptance:** Symbol matrix fixtures reproduce current crossed/isolated behavior; no mode mutation occurs with a provider position open; a successful delayed read-back passes preflight.

### Task 4.2: Release terminal canary reservations safely

**Files:**
- Modify: `src/fatty_trader/execution/bitget_dispatch_repository.py`
- Add additive migration only if a reservation terminal timestamp/state is needed: `src/fatty_trader/storage/migrations.py`
- Test: new `tests/unit/test_canary_reservation_lifecycle.py`

**Design:** Preserve all historical rows. Add a transactional release/reconciliation operation keyed by dispatch ID. A reservation for a terminal dispatch must no longer block a new slot, while an active/unknown dispatch continues to reserve capacity. Do not delete history to make the cap look healthy.

**Acceptance:** With five historical terminal reservations and zero active intents, a new bounded signal can reserve one slot; with five active/unknown reservations it is rejected. The current database can be audited and repaired with a backup-first, explicit operation.

### Task 4.3: Port dead-man semantics to the current monitor

**Files:**
- Modify: `src/fatty_trader/execution/bitget_monitor.py`
- Modify: `src/fatty_trader/execution/service.py` or runtime wiring
- Test: new `tests/unit/test_bitget_deadman.py`, `tests/e2e/test_closed_cutover_zero_post.py`

**Rules:**
- Stale feed/provider reads halt new entries.
- Cancel only working entry orders.
- Preserve all reduce-only SL/TP/close intents.
- Notify once per incident and once on recovery.
- Do not flatten automatically unless a separately approved policy enables it.

**Acceptance:** A stale feed test produces a kill/degraded state, zero new entry POSTs, preserved protection, and deduplicated alerting.

---

## Phase 5: Add a provider event stream only after REST recovery is correct

### Task 5.1: Implement a Bitget public/private WS adapter from the legacy pattern

**Files:**
- Add: `src/fatty_trader/exchanges/bitget/ws.py`
- Modify: `src/fatty_trader/execution/bitget_monitor.py`
- Modify: `src/fatty_trader/service.py`
- Tests: new `tests/unit/test_bitget_ws.py`

**Scope:** Port only the verified pieces: V2 URLs, object subscribe args, `ticker` channel, literal `ping`/`pong`, login-before-private-subscribe, fill fee normalization, real liquidation price, reconnect/backoff, and a valid liveness sentinel. Route events into the same reconciliation/ledger functions used by REST.

**Acceptance:** Fakes cover public marks, private fills/positions/account, reconnect, heartbeat, empty-symbol sentinel, and shutdown. WS disconnect falls back to REST and never enables blind execution.

### Task 5.2: Wire a feed dead-man to the current kill switch

**Files:**
- Modify: `src/fatty_trader/execution/bitget_monitor.py`
- Modify: `src/fatty_trader/storage/reconciliation.py`
- Test: `tests/e2e/test_bitget_restart_recovery.py`, `tests/unit/test_bitget_deadman.py`

**Acceptance:** WS loss or stale REST reads set degraded/kill state; recovery is explicit and read-back verified; open fallback protection remains armed.

---

## Phase 6: Restore operator/source telemetry and historical auditability

### Task 6.1: Make one analysis notification exist for every consumed source revision

**Files:**
- Modify: `src/fatty_trader/analyzer/postgres_worker.py`
- Modify: `src/fatty_trader/notifications.py`
- Test: `tests/unit/test_analyzer_worker.py`, new `tests/unit/test_analysis_outbox_completeness.py`

**Acceptance:** Explicit signal, management message, commentary, parser failure, and fallback analysis each create exactly one deduplicated, sent-or-terminal-failed analysis outcome with source reference and reason.

### Task 6.2: Build a read-only legacy/current differential replay

**Files:**
- Add: `scripts/replay_legacy_bitget_fixtures.py`
- Add sanitized fixtures under `tests/fixtures/bitget/`
- Test: parser, management, entry, fill, close, protection, and restart cases

**Design:** Feed identical sanitized source/provider fixtures through the legacy reference expectations and current pipeline. Compare canonical signal count, management symbol, dispatch count, fill fields, protection state, and terminal state. Do not call Bitget or mutate production DB.

**Acceptance:** The fixture suite catches XPL loss, ETHFI→SLUSDT, GRASS duplicate execution, stale fallback close, missing fee/fill persistence, and transition mismatch.

### Task 6.3: Add a sanitized operator report contract

**Files:**
- Modify: `scripts/health_report.py`
- Modify: `src/fatty_trader/notifications.py`
- Test: `tests/unit/test_health_report_contract.py`

**Required sections:** runtime gates, source counts, canonical/dispatch/intent counts, actual provider positions/orders, current protection, stale local rows, realized order-level PnL, fees, reconciliation mismatches, and last source reference. Do not report `ANALYZED`, `FILLED`, or `PROTECTED` from one table alone.

---

## Phase 7: Verification and staged rollout

### Task 7.1: Make the local suite green without weakening assertions

Run:
```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
python -m compileall -q src scripts
python -m pytest tests/e2e -q
```

Do not delete tests, skip the 17 current failures, or rewrite expected behavior merely to match the current implementation.

### Task 7.2: Run closed-cutover Docker verification

1. Create/verify a PostgreSQL backup.
2. Build the intended SHA.
3. Start the stack with `BITGET_EXECUTION_ENABLED=0` and kill switch active.
4. Replay sanitized source fixtures.
5. Verify zero provider POSTs, complete transitions, no duplicate canonical/dispatch, and all notifications terminal.
6. Verify the image hashes match the source SHA; do not rely only on `git rev-parse HEAD`.

### Task 7.3: Run live read-only reconciliation

Required evidence:
- account and symbol matrix
- server time/clock skew
- contracts
- positions
- pending orders and plan-read quirks
- fills/order details
- local intents/positions/fallback rows
- provider/local mismatch report

No provider mutation is allowed in this task.

### Task 7.4: Bounded canary only after explicit approval

Only after the suite, closed-cutover run, deployed-image verification, read-only reconciliation, and reservation cleanup are green:

- keep global cap bounded;
- use one explicitly selected symbol with isolated/one-way preflight;
- persist intent before POST;
- read back fill, fee, position, protection, and close;
- verify local/provider terminal parity;
- re-latch the gate on any unknown POST, protection mismatch, duplicate intent, stale feed, or close read-back failure.

This plan does not authorize a live order by itself.

---

## Files likely to change

### Current bot

- `src/fatty_trader/analyzer/deterministic_parser.py`
- `src/fatty_trader/analyzer/trade_management.py`
- `src/fatty_trader/analyzer/postgres_worker.py`
- `src/fatty_trader/intake/persistence.py`
- `src/fatty_trader/execution/bitget_dispatch_repository.py`
- `src/fatty_trader/execution/bitget_dispatcher.py`
- `src/fatty_trader/execution/bitget_monitor.py`
- `src/fatty_trader/execution/bitget_fallback_protection.py`
- `src/fatty_trader/execution/source_management.py`
- `src/fatty_trader/exchanges/bitget/async_execution.py`
- `src/fatty_trader/exchanges/bitget/async_venue.py`
- `src/fatty_trader/exchanges/bitget/reconciliation.py`
- `src/fatty_trader/exchanges/bitget/reconciliation_live.py`
- `src/fatty_trader/storage/live_intents.py`
- `src/fatty_trader/storage/reconciliation.py`
- `src/fatty_trader/storage/source_management.py`
- `src/fatty_trader/storage/schema.py`
- `src/fatty_trader/storage/migrations.py`
- `src/fatty_trader/notifications.py`
- `scripts/health_report.py`

### Tests/fixtures

- `tests/unit/test_bitget_reconciliation.py`
- `tests/unit/test_bitget_native_protection.py`
- `tests/unit/test_bitget_async_venue.py`
- `tests/unit/test_analyzer_worker.py`
- `tests/unit/test_source_management_execution.py`
- `tests/e2e/test_bitget_dispatch_monitor_cycle.py`
- `tests/e2e/test_bitget_restart_recovery.py`
- new sanitized fixtures/tests listed in the phase tasks

### Legacy files used as references only

- `/home/valarion/bitget-listener/src/services/orchestrator.py`
- `/home/valarion/bitget-listener/src/services/order.py`
- `/home/valarion/bitget-listener/src/services/position.py`
- `/home/valarion/bitget-listener/src/services/preflight.py`
- `/home/valarion/bitget-listener/src/services/repair.py`
- `/home/valarion/bitget-listener/src/exchanges/bitget.py`
- `/home/valarion/bitget-listener/src/streaming/bitget_ws.py`
- `/home/valarion/bitget-listener/src/store/pg_ledger.py`
- `/home/valarion/bitget-listener/src/sources/parser.py`
- `/home/valarion/bitget-listener/src/sources/telegram.py`

---

## Definition of done

- No duplicate canonical signal or dispatch for an unchanged source revision.
- XPL-style explicit entry ranges and plural targets are parsed or explicitly rejected with a reason.
- Management symbols never resolve to parser words such as `SLUSDT`.
- Every provider fill is durably linked to an intent with price, quantity, fee, and provider fill ID.
- Current provider/local state can be reconciled after restart and after an external/manual close.
- Fallback monitor never POSTs against a fresh flat provider position and never duplicates an ambiguous close.
- Native protection is confirmed by read-back; unsupported native protection becomes durable, restart-safe, honestly degraded bot-side protection.
- Terminal canary reservations do not block new capacity.
- Every dispatch final state has a matching transition; every consumed source revision has a terminal analysis outcome.
- Full tests, Ruff, format, Mypy, compile, closed-cutover Docker verification, and read-only Bitget reconciliation are green.
- No live canary is run without a separate explicit approval after all gates pass.
