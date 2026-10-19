# Live finding — WLD 2026-10-19: protection unverifiable, lane wedged, healthcheck lying

Read-only investigation of the running deployment plus one operator protection repair.
Live root `~/apps/fatty-multi-exchange-trader`, revision `aceed74`, account `5536885345`,
environment LIVE. No deployment, no order, no latch, no risk-limit change was performed.

## What happened (all times UTC)

| Step | Evidence |
| --- | --- |
| Signal | Telegram msg `16332` received 09:19:52, `WLD SHORT 0.509`, SL `0.5192`, TP `0.4509`; canonical signal `418d71a7-…` |
| Dispatch | `4b007ed8-8093-493f-822e-b5e3df638880` created 09:20:11 |
| Entry route | SPLIT_MARKET_LIMIT: market 10 + limit 30 @0.509 (`reason: recent-source-passed-entry`) |
| Entry fill | intent `live-bitget-WLDUSDT-18554b4e90b75ed7`, provider order `1496006445349093377`, filled **10 @ 0.5078**; limit child cancelled 09:21:11 |
| SL/TP POST | `place-pos-tpsl` accepted: SL plan `1496006449426849792` (`…-sl`), TP plan `1496006449372323840` (`…-tp`), both `planStatus=live`, `triggerType=mark_price` |
| Verification | `confirm_native_protection` → `missing-stop-loss` after `grace:5`; capability row `WLDUSDT FAILED missing-stop-loss\|grace:5`; dispatch → `PARTIALLY_FILLED` reason `locally-managed-protection` 09:21:11 |
| Recovery | `PARTIALLY_FILLED → PARTIALLY_FILLED recovery-missing-protection:missing-stop-loss` every ~34 s from 09:21:43; issues `provider-position-not-uniquely-owned-and-protected,recovery-missing-protection:missing-stop-loss,unresolved-protection`; alerts `protection-alert` 09:21:21 and `execution-alert dispatcher-recovery-blocked` 09:21:46 |
| Health | container `(unhealthy)`: `check=failed runtime-progress`; health file frozen at the entry minute although the loop kept cycling |

## Venue truth (read-only GETs, 09:4x)

- `position[WLDUSDT]`: `short`, `total=10`, `openPriceAvg=0.5078`, `marginMode=isolated`,
  `posMode=one_way_mode`, `liquidationPrice=0.5294`, `cTime=1792401651761`,
  **`stopLossId=null`, `takeProfitId=null`, `stopLoss=null`, `takeProfit=null`**, `uTime=cTime`.
- `pending plan orders`: both plans present and live with the exact client OIDs and triggers.
- Plan rows echo **`marginMode=crossed`** although the account (`marginMode=isolated`) and the
  position are isolated. The official `place-pos-tpsl` schema has no `marginMode`/`posMode`
  request fields (<https://www.bitget.com/legacy-docs/classic/contract/plan/Place-Pos-Tpsl-Order>),
  so this echo cannot be controlled from the placement request.
- Capability history: **no `VERIFIED` row has ever existed** for this account — every filled
  entry so far ended `missing-stop-loss` (WLD 10-18, NEAR 10-18, WLD 10-19).

## Root-cause assessment

1. **Native-protection verification cannot pass on this venue.**
   `_confirm_exact_native_protection` (`exchanges/bitget/reconciliation_live.py`) treats the
   position-level form as authoritative (`position_level=True`, no `stop_loss_size`), so a null
   `position.stopLossId` returns `missing-stop-loss` before the live plan list is ever consulted.
   Even if the plans were consulted, `_verify_exact_leg` requires the plan's `marginMode` to be
   `isolated`; the venue echoes `crossed`. Whether a `crossed`-echoed plan still triggers for an
   isolated position is **[INFERENCE]** — unproven, and it gates any relaxation of that check.
2. **No durable local-management row was written at entry.** The F-9 containment path in
   `async_execution.py::_contain_or_local_management` is supposed to `register_fallback`; no
   `fallback_protection` row existed, yet the status was still classified `-locally-managed`
   (`_unprotected_status` only inspects the reason suffix) and the dispatcher reported
   `locally-managed-protection`. The registration failure reason (exception type) is never
   persisted — the C3 silent-failure class again.
3. **The account inventory veto ignores local management.**
   `GetOnlyProtectionRecovery.inventory()` (execution/bitget_protection_recovery.py:150-170) flags
   `provider-position-not-uniquely-owned-and-protected` for **any** open position that a
   successful readback has not put into `_verified`. A locally-managed position never enters
   `_verified`, so one such position blocks the dispatcher lane forever (and only clears when the
   position is flat). This contradicts the per-dispatch policy comment ("native protection is not
   required and must not block readiness").
4. **Blocked cycles never refresh worker progress.** `service.py:948-960`: the
   `recovery_ready is not True` branch prints, alerts, sleeps and `continue`s *before*
   `worker_progress()`, so `WORKER_HEALTH_MAX_AGE_SECONDS` (180 s) expires and the container is
   reported unhealthy (`runtime-progress`) although it is cycling normally. The blocked lane — not
   a dead worker — is what the healthcheck shows.

Net effect on this account: **every entry wedges the bot** (no new dispatches, unhealthy
container) until its position closes, and the entry's protection state is reported as missing
even though the venue holds live SL/TP plans.

## Operator repair performed

Registered close-capable local protection for the open position (the F-9 remedy the entry failed
to establish), via `register_fallback` with proven identity:

- `position_key live-bitget-WLDUSDT-18554b4e90b75ed7`, epoch `1792401651761`, `environment=LIVE`,
  quantity 10, entry 0.5078, SL 0.5192, TP [0.4509];
- approval reference `owner-chat-20261019-trigger-wld-local-protection` (owner instruction in chat;
  no console job was created — the console action `protection.register_fallback` writes the same
  row through its own effect-authority path);
- verified after: dispatch transition reason `recovery-protection-locally-managed` at 13:38:54
  and later cycles; issue set reduced to `provider-position-not-uniquely-owned-and-protected` only;
  `fallback_protection` row `active` (both the stream engine and the 30 s REST lane accept it, so
  the local stop/TP now own a close at 0.5192 / 0.4509).

**Still blocking:** the venue-inventory veto (finding 3) keeps the lane blocked and the container
unhealthy until the WLD position is flat; the dispatch then needs the usual settlement/baseline
path if no close fill lands under its root intents.

## Implemented fixes (local branch, NOT deployed)

1. **Plan evidence verifies when the position row omits IDs**
   (`exchanges/bitget/reconciliation_live.py`). A null `stopLossId`/`takeProfitId` no longer
   short-circuits; the live position-level plan (type `pos_loss`/`pos_profit`, matching order id
   and client OID, trigger, `mark_price`, `live`) is the authority. A plan search that finds
   nothing while the position ID is absent still reports `missing-stop-loss`/`missing-take-profit`
   so the alert vocabulary is unchanged. An exposed position ID that disagrees with the placement
   response still fails `*-id-mismatch`.
2. **The venue margin echo is not proof for position-level plans**
   (same module, `_verify_exact_leg`). `place-pos-tpsl` has no `marginMode` request field and the
   venue echoes account metadata (`crossed`) on `pos_loss`/`pos_profit` rows even for an isolated
   account and position (live evidence above); the position row is the margin authority. Partial
   plans (`loss_plan`/`profit_plan`) keep the strict `isolated` check.
3. **The `-locally-managed` marker is reserved for a durable row**
   (`exchanges/bitget/async_execution.py`). Lane-disabled, missing entry price and registration
   failure now return an unmarked verdict (the dispatcher classifies them `UNPROTECTED` /
   `missing-protection-escalated`) and persist the exact failure reason on the capability row.
   A registration that succeeded keeps the marker and the pre-containment `|grace:N` evidence.
4. **Account inventory honours local management**
   (`execution/bitget_protection_recovery.py`, `execution/bitget_dispatch_repository.py`). An open
   position owned by an `active`/`closing`, environment-matching `fallback_protection` row is not
   flagged `provider-position-not-uniquely-owned-and-protected`; the seam fails closed when the
   registry cannot be read.
5. **Blocked cycles refresh worker progress** (`service.py`). `worker_progress()` runs on the
   `recovery-blocked` branch before the sleep, so a live blocked worker no longer reports a false
   `runtime-progress` health failure; the blocked issue set stays visible in the log line and the
   owner alert.

Tests: new `tests/unit/test_bitget_live_reconciliation.py` cases (missing position IDs, margin
echo, partial-plan strictness), `tests/unit/test_bitget_native_protection.py`
(`PositionWithoutPlanIdsClient` end-to-end through the adapter; failure verdicts are unmarked and
durable), `tests/unit/test_get_only_protection_recovery.py` (fallback-aware inventory), and
`tests/unit/test_bitget_operator_gateway.py` (margin echo on a legitimate stop modification).
The outdated "crossed echo is a mismatch" invariants were replaced by the live-evidence contract.
Local checks: `ruff` clean, `mypy src` clean (125 files), full suite green except one pre-existing
Windows-only websocket heartbeat test (unrelated module, fails identically without these edits).

## Auto-rollout armed (owner instruction 2026-10-19: "rollout immediately once my WLD is closed")

- Branch pushed: `fix/protection-plan-evidence` @ `549dec8` (origin, GitHub).
- The plan workspace on the host (`~/apps/fatty-plan`) is detached at the same commit, so the
  rollout tooling includes the F-8 `is-active` fix.
- Watcher: transient user unit `wld-rollout-watch.service` running
  `/home/valarion/wld_rollout_watch.sh`, log `/home/valarion/wld-rollout-watch.log`.
  It (1) proves the account flat with a read-only venue GET every 60 s (heartbeat every 30 min),
  (2) polls the deploy gate for up to 5 minutes so the recovery can settle on its own,
  (3) if the gate is still blocked, runs the audited baseline for the closed lifecycle
  (`--dry-run` digest → `--apply --expected-digest`; the tool recomputes and re-checks every
  precondition under its DB lock), approval reference `owner-chat-20261019-rollout-after-wld-close`,
  (4) rolls out with the standard tool (`scripts/rollout.py --branch fix/protection-plan-evidence`).
  A gate that still cannot approve stops the watcher with an owner notification; it never forces.
- Baseline candidate set previewed read-only before arming: exactly the WLD lifecycle
  (intent `live-bitget-WLDUSDT-18554b4e90b75ed7`, reservation `51952067-…`, dispatch `4b007ed8-…`).
  The baseline also requires the venue plans/pending orders to be gone, so a local-lane close that
  leaves venue plans behind stops the watcher rather than guessing.

## Non-claims

No source change was deployed; the live revision is unchanged. No venue order was placed by this
repair. The venue plans' trigger behaviour was not exercised (read-only evidence only).
