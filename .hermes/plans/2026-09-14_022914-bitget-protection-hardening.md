# Bitget Protection Hardening and WebSocket Fallback Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task, with spec-compliance review and code-quality review after every task.

**Goal:** Prevent another unprotected Bitget LIVE position from reaching liquidation by making native protection provider-verifiable, adding a WebSocket-driven fallback with a REST watchdog, enforcing per-symbol protection admission, and reconciling provider exits without regressing the current project state.

**Architecture:** Keep exchange-native Bitget position TP/SL as the primary protection. Add an event-driven WebSocket protection stream for symbols that genuinely cannot use native plans, backed by a bounded REST watchdog and idempotent reduce-only close intents. Protection failures remain symbol-local and must not become a global kill-switch event; account-wide provider/auth/clock uncertainty remains fail-closed.

**Tech Stack:** Python 3.11+, asyncio, `httpx`, new `websockets` dependency, Bitget Classic V2 REST/WebSocket APIs, PostgreSQL additive migrations, Docker Compose, `uv`, pytest/pytest-asyncio, Ruff, mypy.

---

## 1. Non-regression contract

This plan is deliberately staged. Writing this plan does not change code, database, containers, runtime gates, or the Bitget account.

### Immutable baseline

- Repository baseline: `a53f514` (`origin/main` matches, worktree clean).
- The provider is currently flat: `0` positions and `0` open orders.
- Last verified account read: `7.68593629 USDT` equity and available balance, `0` unrealized PnL.
- Bitget probe passes with `787` contracts, `0` provider positions, and `0` provider open orders.
- `venue_kill_switches.bitget.active = false`; the existing release record is preserved.
- The previous WLD fallback row is preserved as historical evidence with state `cancelled` and reason `position-already-flat`.
- The provider-first health report and its systemd scheduler are already shipped and must not be replaced by the legacy shell report.
- Existing fallback behavior that avoids latching a global kill switch for a healthy symbol-local fallback must remain intact.

### State-preservation rules

- Do not replay WLD or any historical signal.
- Do not delete reservations, intents, fills, fallback rows, transitions, or old reports to make the state look clean.
- Do not rewrite the current `.env` during implementation.
- Do not alter current live leverage, allocation, canary, or kill-switch values implicitly.
- Do not change the existing health-report scheduler while implementing protection.
- Do not run a provider POST, close, cancel, leverage change, margin-mode change, or live canary as part of local implementation or tests.
- Any provider-mutating canary is a separately approved cutover step after all local and observe-only gates pass.
- Additive database migrations only. Never restore an old database dump over a newer database as a normal rollback.
- Stage only intended paths; preserve unrelated worktree edits if they appear.

### Runtime-environment distinction

The current Compose contract intentionally places `BITGET_FALLBACK_MUTATIONS_ENABLED` on `monitor-bitget`, not `dispatcher-bitget`:

```text
dispatcher: LIVE/LIVE, EXECUTION=1, fallback variable absent/0, operator=0
monitor:    LIVE/LIVE, EXECUTION=1, FALLBACK=1, operator=0
```

Do not “fix” the snapshot by copying the monitor-only variable to the dispatcher. Verify each service separately.

---

## 2. Incident evidence and confirmed failure

The WLD entry was `91` LONG at `0.3971`. Native Bitget SL/TP was absent. The bot fallback levels were SL `0.388` and TP `0.427`.

Bitget later returned a close fill with:

```text
SELL 91 @ 0.3841
reduceOnly=YES
state=FILLED
enterPointSource=SYS
tradeSide=burst_sell_single
profit=-1.17999853 USDT
fee=-0.02097366 USDT
```

The position liquidation price was approximately `0.38668665`. Bitget's official enum classifies `burst_sell_single` as a liquidation sell in one-way mode:

- https://www.bitget.com/api-doc/uta/enum
- https://www.bitget.com/docs/catalog/classic-contract-plan/classic-contract-plan#simultaneous-stop-profit-and-stop-loss-plan-orders

The market data around the event showed:

```text
02:36 WIB candle low  0.3881
02:37 WIB candle open 0.3882
02:37 WIB candle low  0.3833
```

The monitor ran about every 32 seconds. Its last degraded cycle was `02:36:51 WIB`; Bitget liquidated at `02:37:14 WIB`; the next cycle at `02:37:22 WIB` saw the position flat. There was no WLD fallback close intent and no bot close order; the fallback row was marked `position-already-flat`.

The root failure is therefore two-layered:

1. Native protection was not proven/installed.
2. The fallback protection was a 30-second REST polling loop, which is not a hard stop-loss guarantee during a fast gap.

The current adapter also needs contract correction before native protection can be trusted:

- `client.py:488-538` sends a generic `size`, `holdSide=long|short`, `delegateType=normal`, and uses the trigger price as execute price.
- The official Classic V2 combined endpoint documents `holdSide=buy|sell` for one-way mode, `stopLossSize`/`stopSurplusSize` only for partial plans, and `0`/empty execute price for market execution.
- The official placement response is an array under `data`; the current client rejects any non-dict response.
- `get_pending_plan_orders()` omits `planType=profit_loss` and plan identity.
- `reconciliation_live.py:55-143` accepts weak plan/ID evidence and does not verify exact prices, trigger types, execution mode, or client OIDs.
- `service.py:307` defaults the protection monitor interval to 30 seconds.
- The repository has no WebSocket implementation or WebSocket dependency.

---

## 3. Desired protection state machine

Every filled position must move through an explicit durable state:

```text
ENTRY_FILLED
    -> PROTECTION_PENDING
    -> NATIVE_PROTECTED

PROTECTION_PENDING
    -> FALLBACK_ARMED       (only if symbol capability is allowlisted and stream is healthy)
    -> CONTAINMENT_PENDING  (if no safe protection path exists)

NATIVE_PROTECTED / FALLBACK_ARMED
    -> CLOSE_PENDING
    -> CLOSED
    -> RECONCILED

Any state
    -> PROTECTION_UNKNOWN   (provider/read/stream uncertainty; no blind retry)
```

Rules:

- `NATIVE_PROTECTED` requires exact provider read-back, not a successful POST alone.
- `FALLBACK_ARMED` requires a fresh mark-price stream, a healthy watchdog, a registered durable fallback row, and a close path that is idempotent.
- `PROTECTION_UNKNOWN` blocks new entries for that symbol and emits one deduplicated alert; it does not automatically latch the global kill switch for a symbol-local issue.
- Account-wide auth, provider-read, clock, or margin-mode uncertainty remains an account-wide admission failure.
- A close trigger creates one durable close intent before the close POST. Unknown POST results are reconciled by GET only.
- A provider close without a local client OID is represented by a durable reconciliation record/synthetic provider intent, never by deleting or rewriting historical entry evidence.

---

## 4. Implementation tasks

Each task must follow TDD: write the failing test, run it to prove RED, implement the smallest change, run GREEN, run adjacent tests, then commit the task separately. Do not combine unrelated refactors.

### Task 1: Establish a protected implementation branch and baseline evidence

**Objective:** Create an isolated implementation branch and record evidence without touching live state.

**Files:**
- Read: `docs/hermes-context/AGENTIC-OPS-FASTPATH.md`
- Read: `scripts/agentic_ops_snapshot.py`
- Read: `scripts/backup_postgres.sh`
- No production code changes.

**Steps:**

1. Confirm `git status --short --branch` is clean and `git rev-parse HEAD origin/main` both equal `a53f514`.
2. Run `python3 scripts/agentic_ops_snapshot.py` and store the redacted output outside tracked source or in an operator evidence directory that is explicitly ignored.
3. Read provider account, positions, orders, fills, monitor env, dispatcher env, service health, and kill-switch state separately.
4. Create a feature branch from the verified baseline, for example `feat/bitget-protection-ws-hardening`.
5. Do not rebuild, restart, migrate, or change `.env`.

**Verification:**

```bash
git status --short --branch
python3 scripts/agentic_ops_snapshot.py
docker compose config --quiet
docker compose ps
```

Expected: clean baseline, provider flat, authenticated read PASS, no provider mutations.

**Commit:** No code commit; branch creation only.

---

### Task 2: Capture official Bitget protection contracts as fixtures

**Objective:** Make the provider contract testable without network access or live mutation.

**Files:**
- Create: `tests/fixtures/bitget/place_pos_tpsl_response.json`
- Create: `tests/fixtures/bitget/pending_profit_loss_response.json`
- Create: `tests/fixtures/bitget/position_native_protection.json`
- Create: `tests/unit/test_bitget_protection_contract.py`
- Reference: official Classic V2 trigger-order documentation.

**Steps:**

1. Add fixtures for a one-way isolated LONG with `holdSide=buy`, both position-level plans, `mark_price` triggers, market execute prices, live plan status, and returned plan IDs/OIDs.
2. Add fixtures for a SHORT (`holdSide=sell`), partial plans, empty pending plans, malformed envelopes, and provider error responses.
3. Write failing tests asserting the exact fields required by the official schema.
4. Run:

```bash
uv run pytest -q tests/unit/test_bitget_protection_contract.py
```

Expected RED until the normalizers/builders exist.

5. Keep fixture values provider-shaped and do not substitute values from a different Bitget account/environment.

**Commit:** `test: capture Bitget native protection contracts`

---

### Task 3: Implement a pure native-protection payload builder

**Objective:** Separate provider payload construction from network transport and eliminate undocumented/unsafe defaults.

**Files:**
- Create or modify: `src/fatty_trader/exchanges/bitget/protection_contract.py`
- Modify: `src/fatty_trader/exchanges/bitget/client.py:488-538`
- Test: `tests/unit/test_bitget_protection_contract.py`

**Contract:**

For a one-way isolated LONG, the documented position-level baseline is equivalent to:

```python
{
    "marginCoin": "USDT",
    "productType": "USDT-FUTURES",
    "symbol": "WLDUSDT",
    "holdSide": "buy",
    "stopLossTriggerPrice": "0.388",
    "stopLossTriggerType": "mark_price",
    "stopLossExecutePrice": "0",
    "stopSurplusTriggerPrice": "0.427",
    "stopSurplusTriggerType": "mark_price",
    "stopSurplusExecutePrice": "0",
    "stopLossClientOid": "...-sl",
    "stopSurplusClientOid": "...-tp",
}
```

For position-level plans, do not send a generic `size`. If partial plans are intentionally selected, send the documented `stopLossSize`/`stopSurplusSize` fields and require partial-plan read-back.

**Steps:**

1. Write tests for LONG/SHORT hold-side mapping, omitted generic `size`, explicit market execute price `"0"`, `mark_price` triggers, and deterministic client OIDs.
2. Test that unsupported payload variants are rejected before any POST.
3. Keep `delegateType` behind an explicit provider payload profile. Do not blindly remove or retry it: the current operational assumption conflicts with the current official schema and must be decided by one controlled provider canary.
4. Make the client accept a normalized protection response model rather than assuming `dict`.
5. Run the focused tests and the existing adapter tests.

**Commit:** `fix: build documented Bitget protection payloads`

---

### Task 4: Normalize placement and pending-plan responses

**Objective:** Make documented Bitget response envelopes usable and make pending-plan reads precise.

**Files:**
- Modify: `src/fatty_trader/exchanges/bitget/client.py`
- Modify: `src/fatty_trader/exchanges/bitget/reconciliation_live.py`
- Modify: `src/fatty_trader/exchanges/bitget/read_model.py` if the normalized model belongs there
- Test: `tests/unit/test_bitget_protection_contract.py`
- Test: existing native protection/reconciliation tests.

**Steps:**

1. Normalize `place-pos-tpsl` `data: [...]` into a typed list containing each plan ID and client OID.
2. Add `planType="profit_loss"` to pending-plan reads.
3. Support lookup by the returned `orderId`/client OID; do not read an unbounded symbol-wide list and infer identity.
4. Normalize `data.entrustedList` and a null/empty list into a known empty result.
5. Preserve provider error code/message separately from transport errors.
6. Treat `400172` as an error in the current request contract until the corrected request is proven; do not label it “symbol unsupported” solely from the old under-specified request.
7. Add bounded GET-only propagation retries. Never re-POST an unknown placement.

**Verification:**

```bash
uv run pytest -q tests/unit/test_bitget_protection_contract.py tests/unit/test_bitget_live_reconciliation.py
```

Expected: documented array/object envelopes pass; malformed and wrong-shape responses fail closed.

**Commit:** `fix: normalize Bitget protection readbacks`

---

### Task 5: Strengthen exact native-protection verification

**Objective:** Only mark a position `NATIVE_PROTECTED` when every requested protection attribute is provider-proven.

**Files:**
- Modify: `src/fatty_trader/exchanges/bitget/reconciliation_live.py:55-143`
- Modify: `src/fatty_trader/exchanges/bitget/read_model.py`
- Test: `tests/unit/test_bitget_live_reconciliation.py`
- Test: `tests/unit/test_bitget_native_protection.py`

**Required read-back checks:**

- exact symbol;
- exact one-way direction (`buy` LONG / `sell` SHORT);
- `posMode=one_way_mode`;
- `marginMode=isolated`;
- exact confirmed filled quantity;
- non-empty SL and TP provider IDs;
- exact requested trigger prices;
- `mark_price` trigger type for both legs;
- execute price `0`/market mode for both legs;
- live plan status;
- `pos_loss` and `pos_profit` plan types for position-level protection, or exact partial-plan types when partial protection is intentional;
- matching returned client OIDs/order IDs.

**Steps:**

1. Add a typed `NativeProtectionSpec` and `NativeProtectionObservation`.
2. Write RED tests for every mismatch: wrong symbol, wrong side, wrong quantity, wrong price, wrong trigger type, limit execution, non-live plan, missing ID, and provider read failure.
3. Implement exact comparison with no truthy-ID shortcuts.
4. Return distinct states: `VERIFIED`, `MISSING`, `INVALID`, `PROVIDER_READ_FAILED`, and `UNSUPPORTED_PARAMETER`.
5. Run all protection tests.

**Commit:** `fix: require exact native protection readback`

---

### Task 6: Persist protection identity and capability additively

**Objective:** Make native/fallback protection restart-safe and record which provider contract was actually proven.

**Files:**
- Modify: `src/fatty_trader/storage/schema.py`
- Modify: `src/fatty_trader/storage/migrations.py`
- Modify: `src/fatty_trader/storage/reconciliation.py` or a new protection repository module
- Modify: `src/fatty_trader/execution/bitget_fallback_protection.py`
- Create: `tests/unit/test_bitget_protection_persistence.py`
- Create: an additive migration test for an existing v0/v1 schema.

**Migration design:**

Extend the existing `protection_states` contract or add a dedicated `bitget_protection_capabilities` table. Prefer an additive capability table with fields equivalent to:

```text
exchange, environment, symbol, position_mode, margin_mode,
native_state, fallback_allowed, payload_profile,
last_verified_at, last_error, updated_at
```

Extend protection state with exact position/protection identity where needed:

```text
symbol, direction, quantity, sl_trigger_price, tp_trigger_price,
trigger_type, execute_mode, sl_provider_order_id, tp_provider_order_id,
sl_client_oid, tp_client_oid, stream_state, last_provider_read_at
```

**Rules:**

- Unknown capability defaults to no new LIVE entry, not to an assumed fallback.
- Existing historical rows remain untouched.
- Migration is additive and restart-idempotent.
- Capability evidence is environment-specific; DEMO cannot silently authorize LIVE.

**Verification:**

```bash
uv run pytest -q tests/unit/test_bitget_protection_persistence.py tests/unit/test_migrations.py
```

Expected: old schema upgrades without data loss; second migration run is idempotent.

**Commit:** `feat: persist Bitget protection capability and identity`

---

### Task 7: Repair the post-fill native/fallback decision flow

**Objective:** Prevent an entry from remaining open without a provable protection path.

**Files:**
- Modify: `src/fatty_trader/execution/bitget_dispatch_execution.py`
- Modify: `src/fatty_trader/exchanges/bitget/async_execution.py:216-300`
- Modify: `src/fatty_trader/execution/bitget_monitor.py`
- Test: `tests/unit/test_bitget_native_protection.py`
- Test: `tests/unit/test_bitget_monitor.py`
- Test: `tests/unit/test_bitget_dispatch_execution_adapter.py`

**Decision logic:**

1. Fill entry and persist the confirmed quantity.
2. Submit the one selected native payload.
3. Perform exact GET-only read-back.
4. On verified success, persist `NATIVE_PROTECTED`.
5. On a corrected, provider-proven unsupported-symbol result, arm fallback only if the symbol capability record permits it and the protection stream/watchdog is healthy.
6. If no safe protection path exists, create exactly one containment intent and reconcile it; do not leave the position open merely because native protection failed.
7. Keep this failure symbol-local. Do not use the global kill switch for a known symbol-local unsupported-plan case.
8. On unknown POST result, perform GET-only reconciliation before any retry.

Add tests for:

- native success;
- native response acknowledged but read-back missing;
- corrected unsupported-symbol fallback;
- fallback registration failure;
- stream unavailable at fill time;
- exactly-once containment;
- account-wide provider failure still failing closed.

**Commit:** `fix: require a safe protection path after fills`

---

### Task 8: Add Bitget WebSocket transport and event normalizers

**Objective:** Provide a reconnecting, observable, mutation-free stream for mark price and private state.

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `src/fatty_trader/exchanges/bitget/websocket.py`
- Create: `src/fatty_trader/exchanges/bitget/ws_models.py`
- Create: `tests/unit/test_bitget_websocket.py`
- Create: `tests/fixtures/bitget/ws/` message fixtures.

**Protocol scope:**

- Public market stream for the exact mark-price field used by the fallback threshold.
- Private position, order, and fill channels for state confirmation and reconciliation.
- Authenticate private channels using the official Bitget signature/login contract.
- Verify the exact Classic V2 endpoint/channel names from official documentation before coding; do not copy UTA channel names into the Classic V2 adapter without evidence.

**Transport requirements:**

- bounded connection timeout;
- ping/pong or provider heartbeat handling;
- automatic reconnect with capped exponential backoff;
- resubscribe after reconnect;
- last-event timestamp and stream freshness;
- duplicate/out-of-order event handling;
- malformed-message isolation;
- explicit `CONNECTED`, `STALE`, `RECONNECTING`, `FAILED` states;
- no order/cancel/close calls from the WebSocket reader;
- secrets never appear in logs or exception text.

**TDD steps:**

1. Add the dependency and lock update.
2. Write fake-transport tests for login, subscription, ticker normalization, position/order/fill normalization, heartbeat, malformed JSON, disconnect, reconnect, and duplicate events.
3. Implement the transport behind `BITGET_PROTECTION_STREAM_ENABLED=0` by default.
4. Run:

```bash
uv run pytest -q tests/unit/test_bitget_websocket.py
```

**Commit:** `feat: add Bitget protection WebSocket transport`

---

### Task 9: Implement event-driven fallback protection

**Objective:** Replace 30-second threshold polling with WebSocket mark-price events while retaining idempotent REST close execution.

**Files:**
- Create or modify: `src/fatty_trader/execution/bitget_protection_stream.py`
- Modify: `src/fatty_trader/execution/bitget_fallback_protection.py:279-374`
- Modify: `src/fatty_trader/execution/bitget_monitor.py`
- Test: `tests/unit/test_bitget_fallback_protection.py`
- Create: `tests/unit/test_bitget_protection_stream.py`

**Behavior:**

- Arm only after a durable fallback registration and a fresh stream event.
- Evaluate LONG/SHORT thresholds against mark price, not last trade price.
- On threshold hit, create one deterministic reduce-only `CLOSE` intent before the POST.
- Submit a market close through the existing provider client boundary.
- Read back the position and provider fill; mark `closing`, `filled`, or `unknown` durably.
- Never issue a second POST for the same fallback position/client OID.
- If the stream is stale, stop treating fallback as armed and block new entries for that symbol.
- Keep fallback mutation behind `BITGET_FALLBACK_MUTATIONS_ENABLED`.

Do not claim WebSocket protection is absolute: a provider price gap can cross both stop and liquidation levels. The system must therefore still enforce the liquidation buffer and use native plans whenever possible.

**Commit:** `feat: drive fallback protection from Bitget WebSocket marks`

---

### Task 10: Add a REST watchdog and safe stream failover

**Objective:** Detect WebSocket failure quickly and prevent stale-stream exposure.

**Files:**
- Modify: `src/fatty_trader/service.py:278-318`
- Modify: `docker-compose.yml:135-187`
- Modify: `src/fatty_trader/execution/bitget_monitor.py`
- Create: `tests/unit/test_bitget_protection_watchdog.py`
- Modify: `tests/unit/test_service.py` or the nearest service-loop tests.

**Configuration contract:**

Add explicit, default-off rollout flags and bounded intervals, for example:

```text
BITGET_PROTECTION_STREAM_ENABLED=0
BITGET_PROTECTION_STREAM_MODE=observe
BITGET_PROTECTION_STREAM_STALE_SECONDS=<bounded value>
BITGET_PROTECTION_REST_WATCHDOG_SECONDS=<bounded value, target <=5>
BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED=0
```

The final numeric values must be load-tested against Bitget rate limits before LIVE enablement. The existing `BITGET_MONITOR_POLL_SECONDS=30` may remain the legacy reconciliation interval, but it must not be described as a hard fallback stop once the new stream lane is introduced.

**Watchdog behavior:**

- Verify stream freshness and private position state.
- On stale/failed stream, mark affected symbols `PROTECTION_UNKNOWN`.
- Block new entries for affected symbols.
- Use REST position/ticker reads to reconcile and, if the threshold is already hit and mutations are explicitly enabled, execute exactly one idempotent reduce-only close.
- Restore `FALLBACK_ARMED` only after a fresh stream snapshot and provider position read-back.
- Do not globally latch the kill switch for a stream failure affecting only one fallback symbol; do apply account-wide admission containment if provider reads/auth/clock are globally untrusted.

**Commit:** `feat: add Bitget protection stream watchdog`

---

### Task 11: Enforce a real liquidation buffer and fallback risk policy

**Objective:** Prevent a protection level from being technically valid but too close to liquidation for the actual latency/slippage budget.

**Files:**
- Modify: `src/fatty_trader/risk/liquidation.py`
- Modify: `src/fatty_trader/risk/live_policy.py:184-194`
- Modify: `src/fatty_trader/config/bitget.py`
- Modify: `src/fatty_trader/domain/models.py`
- Modify: `docker-compose.yml`
- Test: `tests/unit/test_bitget_liquidation.py`
- Test: `tests/unit/test_bitget_live_policy.py`

**Current evidence:**

For WLD, the configured span-relative guard passed only because the SL was about `12.61%` of the entry-to-liquidation span. The absolute SL-to-liquidation distance was only `0.00131335`, and a one-minute crash crossed it before the 30-second poll could act.

**Policy design:**

Keep the existing `liquidation_buffer` semantics for backward compatibility, then add an explicit minimum absolute/percentage gap and a fallback-specific latency allowance:

```text
LONG:  liquidation < SL < entry
      SL - liquidation >= max(
          (entry - liquidation) * relative_buffer,
          mark * minimum_gap_pct,
          minimum_ticks * price_tick,
          latency_slippage_allowance,
      )
```

Use the inverse inequalities for SHORT. Make every term explicit, provider-backed where possible, and unit-tested. A fallback-only symbol may require a stricter gap or lower leverage than a native-protected symbol.

Do not silently alter current production values in this task. First record the proposed values, run historical/candle sensitivity analysis, and require owner approval before changing LIVE defaults.

**Commit:** `fix: enforce latency-aware liquidation buffer`

---

### Task 12: Reconcile provider system liquidations and unmatched fills

**Objective:** Ensure provider exits and PNL cannot disappear because they have no bot-generated client OID.

**Files:**
- Modify: `src/fatty_trader/storage/schema.py`
- Modify: `src/fatty_trader/storage/migrations.py`
- Modify: `src/fatty_trader/storage/live_intents.py`
- Modify: `src/fatty_trader/exchanges/bitget/reconciliation_live.py`
- Modify: `scripts/agentic_ops_snapshot.py`
- Modify: `scripts/health_report.py`
- Create: `tests/unit/test_bitget_provider_fill_reconciliation.py`

**Reconciliation shape:**

When Bitget returns a provider fill such as `enterPointSource=SYS` and no matching local client OID:

1. Match by provider order ID/fill ID, symbol, side, quantity, and time window.
2. Create one deterministic provider-reconciliation intent or event keyed by provider order/fill ID.
3. Mark it `filled` with the provider order ID, fill ID, price, fee, and realized PNL.
4. Insert the fill idempotently while satisfying the existing FK contract.
5. Close or reconcile any local protection state without deleting the entry history.
6. Emit a deduplicated operator event identifying `SYSTEM_LIQUIDATION` versus `BOT_FALLBACK_CLOSE` versus `NATIVE_SL`.

The health report must distinguish:

```text
Provider fills     N
DB fills           N
Unmatched provider N
Realized PNL source provider/ledger status
```

A provider read failure must remain `UNKNOWN`, never zero.

**Commit:** `fix: reconcile provider-only Bitget exits`

---

### Task 13: Add operator telemetry for protection freshness

**Objective:** Make the next incident diagnosable from one report and one snapshot.

**Files:**
- Modify: `scripts/health_report.py`
- Modify: `scripts/agentic_ops_snapshot.py`
- Modify: `src/fatty_trader/execution/bitget_monitor.py`
- Modify: `docs/hermes-context/AGENTIC-OPS-FASTPATH.md`
- Modify: `docs/ARCHITECTURE.md` if the architecture description remains current.
- Test: `tests/unit/test_health_report_provider_source.py`
- Test: `tests/unit/test_health_report_contract.py`

**Report fields:**

- provider position/order read state;
- stream state and age of last mark event;
- native protection state and exact levels;
- fallback state and exact levels;
- liquidation price and SL/liq gap;
- provider fills versus DB fills;
- unmatched provider events;
- active symbol quarantine/capability state;
- monitor last successful cycle and last error;
- kill switch state without implying that fallback monitoring is native protection.

Keep the current mobile-readable stacked-card format and the provider-first scheduler. Do not reintroduce the wide table or the old shell scheduler.

**Commit:** `feat: expose Bitget protection freshness and reconciliation`

---

### Task 14: Build the complete no-network and fake-provider test matrix

**Objective:** Prove correctness without placing orders.

**Files:**
- Modify/add protection, WebSocket, service, reconciliation, and health-report tests under `tests/unit/`.
- Add fake provider/transport fixtures under `tests/fixtures/`.

**Required cases:**

1. Native LONG/SHORT payloads match the official contract.
2. Native placement response array normalizes correctly.
3. Pending plan object/entrustedList normalizes correctly.
4. Exact read-back rejects every mismatch.
5. `43011` is not treated as unsupported until the corrected payload profile is tested.
6. Unknown POST result performs GET-only reconciliation and never re-POSTs blindly.
7. Native plan limit execution is rejected when market execution is required.
8. WS login, subscription, heartbeats, reconnect, stale detection, malformed messages, duplicate events, and stream recovery.
9. Mark-price threshold triggers exactly one reduce-only close intent.
10. Close POST timeout/read failure remains `unknown` and is reconciled without duplication.
11. Stream stale blocks new entries for that symbol.
12. Account-wide provider failure remains fail-closed.
13. Symbol-local protection failure does not latch the global kill switch.
14. Provider-only liquidation creates one reconciled fill and correct PNL.
15. Current health report still shows provider state when DB `positions` is empty.
16. Existing canary/reservation semantics, signal lifecycle, and scheduler tests do not regress.

**Verification commands:**

```bash
uv run pytest -q
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src
git diff --check
```

Expected: all existing tests plus new tests pass. Any unrelated pre-existing Ruff failure must be reported separately and must not be hidden.

**Commit:** `test: cover Bitget native and streaming protection matrix`

---

## 5. Safe rollout sequence

No rollout is part of the plan-writing turn. When implementation is complete, use this sequence exactly.

### Phase A: Local verification

1. Confirm branch contains only intended commits and no unrelated changes.
2. Run the full local verification commands from Task 14.
3. Run `docker compose config --quiet`.
4. Verify Dockerfile packages `src`, `scripts`, and `uv.lock`; add the new WS module to the image automatically through the existing `COPY src` path.
5. Do not touch provider state.

### Phase B: Database backup and additive migration

1. Create and verify a PostgreSQL backup:

```bash
BACKUP_DIR=backups /bin/bash scripts/backup_postgres.sh
```

2. Record backup path and byte count.
3. Run migrations as one-shot services only after the backup is verified.
4. Read back migration versions, new tables/columns, row counts, and current kill-switch state.
5. Confirm no historical rows were deleted or rewritten.

### Phase C: Rebuild with all risky gates held closed

1. Rebuild affected images.
2. Recreate services with:

```text
BITGET_EXECUTION_ENABLED=0
BITGET_PROTECTION_STREAM_ENABLED=0
BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED=0
BITGET_OPERATOR_MUTATIONS_ENABLED=0
```

3. Verify container source, image/runtime identity, migrations, healthchecks, and service env per service.
4. Run authenticated provider reads: account, contracts, positions, open orders, fills, server time.
5. Baseline must remain flat and unchanged.

### Phase D: Observe-only WebSocket soak

1. Enable only `BITGET_PROTECTION_STREAM_ENABLED=1` with `BITGET_PROTECTION_STREAM_MODE=observe` while execution and protection mutations remain closed.
2. Do not open a position merely to test the stream.
3. Observe public mark events, private channel authentication/connection behavior if authorized read-only, heartbeat age, reconnect behavior, and REST-vs-WS price/state comparisons.
4. Require a clean soak window and zero stale/unknown unresolved states.
5. Keep a report of event age, reconnect count, parsed symbols, and provider/API error rates.

### Phase E: Native protection capability canary

This is the first phase that may require an actual provider mutation and therefore requires an explicit owner approval reference. It is not automatic.

1. Keep global cap at `1` and use the smallest approved risk/size.
2. Select a symbol whose native payload profile has been reviewed.
3. Submit exactly one approved entry only after account/mode/contract preflight passes.
4. Immediately verify fill, exact native SL/TP payload result, exact position read-back, exact pending-plan read-back, plan IDs/OIDs, trigger types, market execution, and liquidation price.
5. Do not proceed if any read-back is ambiguous. Do not blind-retry another payload variant after an unknown POST.
6. Close/reconcile the canary only through the normal audited lifecycle if the owner explicitly authorizes it; otherwise leave no test position open.
7. Record provider order/fill IDs and all local lifecycle rows.

### Phase F: Fallback WebSocket canary

1. Run only on a symbol that has a provider-proven native-unsupported capability record.
2. Require WS stream healthy, REST watchdog healthy, fallback mutation gate explicitly enabled, and per-symbol quarantine/capability admission.
3. Use a controlled test or approved live canary, never a replay of WLD.
4. Prove threshold event → one close intent → one reduce-only close → provider flat read-back → local fill/PNL reconciliation.
5. Stop immediately on stale stream, duplicate intent, unknown close result, or provider/ledger mismatch.

### Phase G: Gradual expansion

Expand symbol-by-symbol only after repeated clean native/fallback cycles. Maintain the existing global canary/reservation fence separately from protection capability. Do not count intent and reservation twice, and do not use historical rows as current capacity.

---

## 6. Rollback and recovery contract

### Before provider exposure

- Revert the feature branch or image to the last known deployed image.
- Recreate services with the recorded prior env values.
- Keep database migrations forward-compatible; do not destroy the database.
- Verify provider flat/open orders, runtime gates, and kill-switch state.

### After provider exposure

- Do not roll back blindly while a position or close intent is active.
- First read provider positions, open orders, fills, plan orders, and account state.
- Reconcile every provider event into the durable ledger.
- If protection is unknown, keep new entries blocked for the affected symbol and contain only through the idempotent audited path.
- Only then roll code back or forward.
- Restore from the backup only for a proven database corruption scenario, with explicit approval and a new backup of the current state.

### Rollback acceptance

- No provider position is hidden by rollback.
- No duplicate close/entry POST is generated.
- No historical row is deleted.
- Health report still shows provider truth and any ledger drift.
- `git status` and remote commit state are explicit.

---

## 7. Final acceptance criteria

The implementation is not complete until all of these are proven:

- A new LIVE entry cannot remain open without either exact `NATIVE_PROTECTED` or explicitly allowlisted, fresh `FALLBACK_ARMED` state.
- Native payload and read-back match the current official Bitget Classic V2 contract for the selected environment/profile.
- Native execute price is explicitly market (`0`) unless a limit exit is intentionally and separately approved.
- WebSocket mark events are normalized, monitored for freshness, and recovered after disconnects.
- REST watchdog detects stale stream within the configured bound and blocks new entries for affected symbols.
- Fallback close is idempotent and reduce-only; an unknown POST is never blindly retried.
- Symbol-local protection failures do not latch the global kill switch; account-wide uncertainty still fails closed.
- Liquidation buffer includes a latency/slippage-aware absolute or percentage floor, not only the old span-relative ratio.
- Provider system liquidation/close fills are reconciled into the local ledger and PNL exactly once.
- Health report exposes provider positions, protection source/state, stream freshness, liquidation gap, and provider-vs-DB fill drift.
- Existing health-report formatting/scheduler, signal chain, dispatch lifecycle, canary reservations, and previous monitor behavior remain green.
- Full tests, Ruff, format, mypy, whitespace, Compose config, backup, migration, runtime health, and authenticated read-back all pass.
- No live mutation is performed before an explicit owner-approved canary step.

---

## 8. Open decisions to resolve before implementation reaches LIVE

1. Which exact Classic V2 payload profile (`delegateType` present or absent) does the target account accept after the documented fields are corrected?
2. Which exact Classic V2 WebSocket endpoint and channel names apply to this account/product type, versus UTA documentation?
3. What minimum SL-to-liquidation distance is acceptable for 30x/20x fallback-only symbols after observed spread, slippage, and stream/REST latency?
4. Should fallback-only symbols use a lower leverage cap, a larger position buffer, or both?
5. Should provider-only system exits be represented by synthetic `CLOSE` intents or a separate provider-events table? Choose one durable, idempotent shape before migration.
6. What observe-only soak duration and stale-event threshold are required before enabling fallback mutation for any symbol?
7. Which owner approval reference authorizes the first native protection canary and, separately, the first fallback WebSocket canary?

Until these decisions are answered with provider-backed evidence, keep the new WebSocket/native-protection implementation default-off and do not claim LIVE protection readiness.
