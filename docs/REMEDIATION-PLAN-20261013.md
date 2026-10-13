# Bitget reliability remediation: design, execution plan, and progress tracker

Date: 2026-10-13

Status: local implementation/acceptance COMPLETE (2095 passed, zero failures/errors/skips). Owner approved commit, push, and LIVE deployment; production rollout/recovery verification is in progress.

Workstation repository: `C:/Users/it26/AppData/Local/Temp/bitget-audit-20261009-7c92`

Production repository: `/home/valarion/apps/fatty-multi-exchange-trader` on `fspmi-hostinger`.

This document records the execution contract and observed progress in section 8. Runtime observations are point-in-time evidence; local source/checks do not prove deployment or live execution. Refresh financial and safety evidence before any production operation. The instruction to proceed started local implementation; production deployment, safety-latch release, risk changes, and exchange mutations remain separately authorized boundaries.

## 1. Outcome and non-negotiable boundaries

Deliver a bot that reports account and execution state truthfully, sizes the selected entry route correctly, measures exchange clock offset without confusing network latency with drift, rejects unsupported contracts before account-side preflight, and can be returned to operation through the existing evidence-led recovery process.

Success does not mean accepting every source signal. A contract that cannot support the approved risk budget and exact entry split must still be rejected.

The following requirements remain unchanged:

- Production stays `TRADER_MODE=LIVE`, `BITGET_MODE=LIVE`, `BITGET_EXECUTION_ENABLED=1`, with `FATTY_PRODUCTION_LIVE_ONLY=1` where prescribed. No DEMO or execution=0 rollout.
- The fixed 1 USDT maximum margin per trade, existing allocation, leverage, position limits, liquidation guard, isolated/position-mode requirements, source recency, and independent mutation gates remain in force.
- Preserve global/venue admission switches, final source fences, audited account-baseline exclusions, ownership, stable client order IDs, intent-before-POST, reservations, and ambiguous-result reconciliation.
- Preserve the exact 25% market / 75% working LIMIT contract when the eligible just-departed route applies. No approximate split or silent full-market conversion.
- Automatic first-target TP covers the full actual bot position. Explicit source `TP1_BOOKED` retains its distinct half-close/SL-to-entry policy. No changes to these semantics.
- Do not relax edited/expired source handling, infer a symbol from ambiguous chat, requeue rejected historical dispatches, or replay expired signals.
- Reporting, probes, and offline verification must not place, cancel, close, alter leverage/margin mode, or modify protection.
- Preserve historical admission evidence. Do not fabricate a recent balance timestamp or rewrite old snapshots to improve the health card.
- A reconnect or one successful GET is not authorization to clear a durable latch.
- Do not introduce a new account poller, generic retry framework, schema migration, trading strategy, or autonomous recovery service for this remediation. If implementation demonstrates a necessary contract/schema change, document it and resolve that prerequisite before proceeding.

Authoritative contracts: [AGENTS](../AGENTS.md), [trading rules](TRADING-RULES.md), and [production LIVE-only policy](PRODUCTION-LIVE-POLICY.md). Older operations text conflicts with the current LIVE-only directive in places; the current policy takes precedence.

## 2. Evidence baseline and limits

### 2.1 Confirmed observations

| Evidence ID | Observation | Meaning and limitation |
|---|---|---|
| E01 | All nine Compose services were running and healthy during the resumed audit | Process/dependency health, not proof of order execution |
| E02 | Three dispatches since the October 9 audit boundary were REJECTED: split step alignment, Bitget 40309 symbol removed, and kill-switch-latched | Durable reasons explain these attempts; they do not prove the entire trading lifecycle |
| E03 | Latest stored Bitget balance snapshot: available 9.97552049 USDT, captured October 1 at 07:11:20 UTC | Old admission evidence, not current account telemetry |
| E04 | Fresh authenticated account helper returned equity/available 9.17026367 USDT | Current configured Bitget futures account observation; not all Bitget wallets |
| E05 | Actual operator-container slash-health loader and renderer returned 9.170264 USDT, zero provider positions, zero pending orders, and both active latches | Read-only rendering path exercised; Telegram delivery of this invocation was not exercised |
| E06 | User-level `fatty-health-report.timer` is enabled; its last service invocation at October 13 03:57 UTC recorded SENT | Scheduled report works; cards remain point-in-time snapshots, not continuously updating messages |
| E07 | That scheduled card showed current account 9.170264 USDT, admission snapshot age 1025168.418400 seconds, 19 stored fills, and Codex usage HTTP 401 | Historical ledger totals and admission age are separate from current provider account balance |
| E08 | Current latches: bitget-protection-stream/socket-not-connected since October 12 06:08 UTC; bitget/clock-skew-exceeded since October 12 14:29 UTC | Current readback at audit time; reason/update can change before recovery |
| E09 | Prior runtime sizing replays produced 43.8 and 66.5 MAGIC total quantities; their exact 25/75 children failed step 0.1 alignment | Evidence of the quantity-grid mismatch; not proof that rounded replacements satisfy every venue constraint |
| E10 | Slash report showed fills UNKNOWN with zero fees/net PnL, omitted latest message despite message count 440, and defaulted unloaded metrics | Consumer-visible reporting defects; shared formatting alone does not establish equivalent loaders |

The active timer uses `scripts/health_report.py`, not `scripts/telegram_health_report.sh`. The shell report nevertheless remains a duplicate DB-snapshot path and must be removed after its callers are migrated.

### 2.2 Source-established behavior

- `storage/balance_reservations.py` inserts `balance_snapshots` through durable admission, after several source/ownership/freshness gates. There is no requirement that idle/rejected trading continually refresh that evidence table.
- `service.py` determines the split route before sizing, but currently passes no split constraint into the live sizing decision. It checks the resulting children only after the total has been selected.
- `exchanges/bitget/async_venue.py` currently reads symbol account/position state before resolving contract metadata. Despite its read-only description, `preflight()` can set margin mode. Never use that method as a supposedly GET-only smoke probe.
- `exchanges/bitget/client.py` evaluates local time before awaiting server time: `int(time.time() * 1000) - await self.get_server_time_ms()`. Latency/retry delay can therefore appear as clock offset.
- `execution/bitget_monitor.py` intentionally preserves active latches after current reasons disappear.
- Existing flat-baseline recovery admits only the exact approved scope/reason pairs, rejects other/global latches, binds account/baseline identity, requires dual-leg authenticated WebSocket evidence, checks ledger readiness, and validates a reviewed digest before apply.
- Current preflight and monitor clock limits differ. Preserve their effective limits unless a separate approved policy change is made; fixing measurement does not authorize widening thresholds.

### 2.3 Unproved claims and unresolved facts

- Network packet loss was not established as the cause of the WebSocket incident. A disconnect/connect error is not sufficient attribution.
- NTP synchronization does not establish zero exchange clock offset. The timing defect is a plausible contributor, not a proven explanation for the October 12 latch.
- Historical and current balance differences cannot be attributed to a particular trade, transfer, funding payment, or fee from these observations alone.
- Read-only inspection during implementation identified PUMPUSDT and PENGUUSDT fallback registrations with null environment and provider position epoch, dated September 29/30. Current provider USDT-FUTURES inventory returned zero positions/orders and the local open-position query returned zero rows. These legacy records are not verified live protection; no rows were deleted, rebound, or falsely assigned an epoch/environment.
- No newly admitted live trade has demonstrated entry, fill, native protection, remainder fill, source management, restart recovery, and close reconciliation under the proposed release.
- The prior API probe's fills count of 2 described a dictionary shape, not necessarily two fills. Do not treat response-container counts as financial transaction counts.
- The unavailable local Python prerequisite was resolved with uv-managed Python 3.12. The original timing bias was then reproduced offline, and corrected request-window scenarios were exercised. Later production SSH refusals did not require experiments or installation on production.

## 3. Design decisions

### 3.1 Account freshness versus admission history

The ACCOUNT section remains provider-first. Show venue/environment, futures product/margin coin, observation time, and provider-read outcome. Use a clearly defined successful-response observation timestamp for the display; distinguish it from the card render/send time. Do not change admission timestamp semantics as a side effect of reporting work.

The ledger section must label the old metric as `Last admission balance evidence` with captured time/age. Old evidence alone must not imply a broken live account reader or ready admission. Admission still requires its fresh independent account/position evidence and storage rechecks.

If the current provider read fails, show UNKNOWN and a sanitized reason. Do not silently use an old admission snapshot as live account data. Preserve numeric zero only when a successful read establishes zero. No new periodic balance writer is needed.

### 3.2 Loader coverage and truthful reporting

Keep `operator/health_report_format.py` as the single pure renderer. Reuse the existing package health-loading path for shared provider/ledger semantics; keep host-only service and Codex probes in the host script. Consolidate duplicated core loading into existing modules rather than introducing a second report subsystem.

Both loaders must supply the same normalized account, position/order, PnL, reservation, admission-evidence, and latest-message meanings. Host-only fields may remain unavailable in the operator container, but must say UNKNOWN/not probed instead of passing defaults as observations.

Preserve failure independently by section: failed PnL must not erase a successful account read, and failed DB queries must not fabricate flat provider state. Do not equate two unknown counts as a successful reconciliation. Record whether the data is provider-observed, ledger-observed, or not available.

### 3.3 Split-aware sizing

For venue quantity step `s`, exact 25/75 children require total quantity `Q = 4ks`, for positive integer `k`.

The route decision already exists in `service.py`. Carry an explicit split constraint into `LiveSizingInput` and the existing sizing path, using the current route as the authority. Use Decimal throughout. Select quantity on `4s` only for the split route; ordinary full-market sizing remains on `s`.

Apply the existing leverage/risk selection policy with route feasibility included. Never accept a total-only minimum-notional result whose child fails. Recompute final notional/margin after quantization/caps; require both child minimum sizes/notionals and their respective market/limit maximums. Preserve final child checks at the dispatcher boundary as defense in depth, not as the primary sizing algorithm.

For split pricing, preserve conservative combined admission valuation and check each leg against its actual planned execution price. Do not sum margins calculated from incompatible price assumptions. If no approved candidate works, reject with the specific infeasibility reason before reservation or mutation.

The earlier quantities illustrate grid correction only: 43.8 would floor to 43.6 and 66.5 to 66.4 on a 0.4 grid. These are not promises of accepted trades; all other constraints still apply.

### 3.4 Clock measurement

Replace pre-await subtraction with bounded request-window measurement. Capture local wall-clock times around the measurement and monotonic elapsed duration; estimate local-minus-server offset at the request midpoint. Reject an unreliable sample when request delay, retries, or a local wall-clock jump make offset attribution unsafe.

Use a one-attempt server-time measurement within the existing client so retry delays do not masquerade as offset. Preserve current general GET retry behavior for other endpoints and all existing no-blind-POST-retry rules. Do not create a separate retry client or broaden mutation behavior.

Use the existing configured clock policy to bound uncertainty conservatively. A high-latency/failed probe is `clock-skew-unavailable` or the existing equivalent unhealthy outcome, not proof of real clock drift and never permission to trade. Preserve thresholds. Test threshold boundaries, negative/positive offset, slow replies, wall-clock jumps, and invalid server timestamps.

### 3.5 Unsupported contracts

Resolve validated contract metadata at the beginning of `AsyncBitgetVenue.preflight()`, before symbol account/position reads and especially before margin-mode changes. Reuse `find_contract` and metadata normalization; do not maintain a handwritten denylist or add a contract cache for this fix.

Retain canonical source signals and dispatch history. Unsupported on Bitget is a venue rejection, not an analyzer parse failure. A failed/malformed contract read must remain provider-unavailable, not a fabricated unsupported-symbol result.

### 3.6 Latches and recovery

Report separately: current monitor/stream condition, durable latch scope/reason/timestamps, and whether approved recovery prerequisites are satisfied or unknown. Existing faults, historical latches, and completed provider execution are different states.

Use the existing approved flat-baseline recovery tool. This release does not add blanket automatic latch clearing. Sustained healthy reads are necessary evidence, not sufficient release authorization. New/global/unknown reasons, unresolved ownership, nonflat inventory, stale proof, or changed latch rows must refuse recovery.

Temporary interruption should not imply daily manual SQL work: the supported operation must be a documented, bounded proof-and-apply workflow with explicit evidence and approval. Any future autonomous release policy requires separate approval and its own safety contract.

## 4. Implementation work packages

### WP1 — Health account freshness and ledger coverage

Primary files: `src/fatty_trader/operator/health.py`, `src/fatty_trader/operator/health_report_format.py`, `src/fatty_trader/operator/bitget_gateway.py`, `scripts/health_report.py`, and affected wiring in `src/fatty_trader/service.py`.

Steps:

1. Enumerate normalized renderer inputs and which current loader supplies each. Locate all account/health callers; use language-server references when available.
2. Add observed-at/source/scope information to successful account DTOs without exposing raw credentials, UID, or account payloads in the report.
3. Load the existing DB totals, PnL/fees, reservations, last admission evidence, post-fill status, and latest source message in the slash path using existing connection/queries and package conventions.
4. Consolidate shared core meanings; preserve host-only probes and field-level failure distinctions. Do not introduce hidden provider rereads for a section whose successful snapshot was already loaded.
5. Correct renderer branches so unknown fill count cannot produce zero realized PnL/fees, an omitted message load cannot say no message exists, and absent reservation/order metrics cannot become empty/zero observations.
6. Expose last admission captured time separately from live account read time. Explain six-hourly cards as immutable point-in-time reports.
7. Treat Codex usage authentication as a separate section failure, not exchange account failure or blanket trade-readiness proof.

Acceptance:

- Same normalized successful observations produce equivalent financial meanings in both surfaces; host-only fields remain explicitly unprobed where appropriate.
- A fresh provider balance and old admission evidence coexist without a false stale-account verdict.
- Provider/DB failures do not become zeros, flatness, successful reconciliation, or successful execution.
- Latest source message appears when stored; no fabricated fill/close claims are introduced.
- Formatting remains Telegram-safe and within the existing message-size/delivery contract. Verify critical safety/account fields are not lost to truncation.

### WP2 — Exact route-aware position sizing

Primary files: `src/fatty_trader/risk/live_policy.py`, `src/fatty_trader/service.py`; review `execution/entry_routing.py`, `execution/bitget_admission.py`, and `execution/bitget_dispatch_execution.py` for affected contracts.

Steps:

1. Reproduce current total-grid/child-grid mismatch offline using the existing MAGIC observations and injected historical metadata/prices. Never replay actual dispatch IDs into provider execution.
2. Extend the existing sizing input with the explicit route constraint; migrate every caller rather than retaining an obsolete compatibility sizing branch.
3. Apply split quantum and leg feasibility within candidate selection, preserving the existing risk/leverage selection policy.
4. Recompute actual combined margin/notional after all quantity caps and rounding. Check the market child against market limits and LIMIT child against limit limits.
5. Retain exact final child validation before reservation. Persist total and children consistently with the existing submission/ownership contract.
6. Ensure an infeasible result creates neither a reservation nor an entry intent/provider mutation.

Acceptance:

- Exact 25/75 children sum to the admitted total and each satisfies its venue grid.
- Full-market routing is behaviorally unchanged outside the corrected shared invariants.
- No risk cap is widened; actual admission values agree with final quantities/prices.
- Minimum size/notional and max size can legitimately reject a trade, with a specific reason.
- Partial submission/unknown acknowledgement/restart recovery remain fenced by existing ownership and intent claims.

### WP3 — Reliable exchange clock evidence

Primary files: `src/fatty_trader/exchanges/bitget/client.py`, affected monitor/venue clock callers, and diagnostics in `execution/bitget_monitor.py` / `service.py` as necessary.

Steps:

1. Keep a deterministic failing-before reproduction of the original pre-await timing bias in the existing client regression suite.
2. Implement bounded one-attempt midpoint measurement within the existing client, using monotonic elapsed-time checks.
3. Preserve sanitized error handling and represent unreliable measurements as unavailable, not excessive offset.
4. Check every caller for the failure contract; prevent exception suppression from turning an unavailable probe into acceptable zero skew.
5. Expose the measured offset and uncertainty/availability through existing diagnostic output where needed to explain admission refusal; do not add a general telemetry subsystem.
6. Confirm effective monitor/preflight thresholds and leave them unchanged.

Acceptance:

- Synchronized clocks with a valid bounded request delay do not falsely report excessive skew.
- Genuine excessive positive/negative offset blocks admission.
- Slow/failed/retried-unreliable samples and wall-clock jumps remain fail-closed without misattributing the cause.
- Unrelated GET retry and POST ambiguity behavior remain unchanged.

### WP4 — Venue eligibility before account-side preflight

Primary files: `src/fatty_trader/exchanges/bitget/async_venue.py`, existing metadata/error handling, and dispatch rejection reporting.

Steps:

1. Move contract validation/metadata resolution ahead of symbol account/position reads and margin-mode mutation.
2. Retain existing provider validation and supported position/risk policies after successful eligibility.
3. Distinguish contract absent from contract lookup failed or malformed.
4. Preserve source/canonical signal traceability and durable terminal rejection.

Acceptance:

- Removed FLOW contract is explicitly rejected before account-side preparation or entry mutation.
- Temporary contract-provider failure is reported unavailable, not unsupported.
- Supported-contract preflight retains its existing approved behavior.

### WP5 — Explain latch readiness and make recovery operable

Primary files: existing health loader/renderer, `scripts/recover_bitget_live_latches.py`, `storage/bitget_live_latch_recovery.py`, and relevant operations documentation. Do not rewrite the existing recovery algorithm unless a concrete correctness gap is demonstrated.

Steps:

1. Present current stream/monitor health independently of persisted admission latches. Use existing evidence sources; unavailable worker evidence stays unknown.
2. Surface scope, current recorded reason, latch age, and the reason recovery is allowed/refused/unverified.
3. Verify the existing exact-reason whitelist, baseline/account binding, dual-leg WebSocket proof, ledger checks, row-change fences, digest, and transactional apply behavior with regressions.
4. Inspect the two active fallback records read-only against ownership, environment, position epoch, and provider inventory. Correct the report's interpretation; mutate/retire records only through an already-approved reconciliation lifecycle, never by deleting them to improve a count.
5. Document flat-baseline proof/apply and the different response when exposure exists. Do not run flat-baseline recovery on a nonflat account.

Acceptance:

- A healthy stream plus active historical latch is represented honestly as entry-blocked, not fully operational.
- A new/global/unsafe latch or changed proof refuses release.
- Existing positions retain protection/close recovery during normal admission blocking.
- No latch is released as part of report rendering, process restart, or deployment verification.

### WP6 — Clean reporting cutover and operational documentation

Primary files: `scripts/telegram_health_report.sh`, installed report callers, `deploy/systemd/fatty-health-report.service`, `deploy/systemd/fatty-health-report.timer`, `docs/OPERATIONS.md`, affected current runbooks, and this tracker.

Steps:

1. Inventory repository callers plus installed user/system units, cron, and external wrappers. The audited active timer is a user unit; verify it rather than assuming a system unit.
2. Migrate any remaining shell-report callers to the canonical Python report, then remove the obsolete shell implementation. Preserve the six-hour schedule and locking/bounded-send contract.
3. Update shared-renderer claims: rendering is shared; runtime-only data may legitimately differ when not probed. Remove claims of byte-identical snapshots collected at different times.
4. Reconcile active operations instructions with the current LIVE-only policy; keep historical audit documents clearly historical rather than rewriting evidence.
5. Add the completed release record to the repository's existing operations/release documentation. Separate local, deployed, and live-lifecycle evidence.

Acceptance:

- One active health-report implementation and renderer, with no dangling executable callers or obsolete compatibility entry points.
- Installed user timer targets the intended deployed script and retains its schedule.
- Current docs contain no conflicting DEMO/execution=0 remediation instructions.

## 5. Execution sequence, ownership, and dependencies

One integration owner owns shared `service.py`, the normalized health inputs, runtime cutover, and final verification. Default execution is sequential to avoid overlapping file changes. Parallel work is permissible only after interfaces/file ownership are fixed: clock-client work and venue eligibility can be independent; health loading/formatting and latch reporting share files and remain under one owner. All workers skip mid-flight builds/tests/linters/formatters; run integrated checks after the work lands.

### Stage A — Establish implementation baseline

1. Read current repository state and user changes; do not reset or overwrite unrelated work. Record the actual release/source identity instead of assuming the earlier SHA still applies.
2. Refresh code references, effective policy, test names, and report callers. Source inspection is sufficient here; do not rerun the user's stale-balance observation to dispute it.
3. Provision the supported local Python/uv development runtime, then `uv sync --all-groups`. Keep production credentials out of local tests.
4. Obtain any missing historical replay inputs from stored evidence/read-only metadata, or build a clearly labeled deterministic boundary fixture. Distinguish a mathematical fixture from a historical replay.
5. Freeze the WP1 normalized report meanings and WP2 route-aware sizing contract. No schema migration is planned.

Exit gate A: runnable isolated development environment, known file ownership, actual callsites mapped, evidence versus inference recorded.

### Stage B — Implement the integrated release

1. WP1 financial freshness/unknown semantics.
2. WP2 route-aware quantity and per-leg feasibility.
3. WP3 clock timing measurement and caller failure contract.
4. WP4 metadata-first eligibility.
5. WP5 latch/fallback interpretation and recovery regression coverage.
6. WP6 clean cutover, all callers, and current operations docs.
7. Preserve useful behavior regressions. Remove wording-only/implementation-copy tests instead of repinning them to new incidental wording.

Exit gate B: complete caller migration, no dead shims, no placeholders, no unintended risk/mutation-policy changes, no half-wired helper-only fix.

### Stage C — Verify locally and exercise changed paths

Run the selected relevant suites after integration, followed by full-suite/style/type checks. These are future execution commands, not checks already performed:

```bash
uv run pytest -q tests/unit/test_live_sizing.py tests/unit/test_entry_routing.py tests/unit/test_bitget_dispatch_execution_adapter.py tests/unit/test_bitget_client.py tests/unit/test_bitget_monitor.py tests/unit/test_bitget_live_latch_recovery.py tests/unit/test_shared_health_reader.py tests/unit/test_operator_health.py tests/unit/test_health_truthfulness.py tests/unit/test_health_report_provider_source.py tests/unit/test_report_execution_health.py tests/unit/test_live_health_scope.py
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
uv run mypy src
```

Update the command's explicit suite list if clean cutover removes obsolete test files. Keep relevant DB/e2e behavior in the full-suite verification and run database cases against an isolated local PostgreSQL instance according to existing fixture requirements; document any fixture requirement rather than bypassing those tests.

Smoke scenarios, separate from assertions:

- Run the actual package health loader/renderer with a populated isolated database and read-only fixture/provider adapter; inspect the account/admission timestamps, historical PnL, current positions, latest message, and independent unknown sections.
- Exercise the host report's data/render path without its send side effect. Do not invoke the script's sending main merely to inspect output.
- Run integrated dispatch preflight/admission offline with boundary inputs, then inspect the resulting quantities, margin/notional, reservation effects, and mutation record. No real provider credentials.
- Run delayed/offset/failed clock scenarios through the actual client implementation with controlled transport and deterministic time; no real sleeps.
- Exercise unsupported-contract preflight and prove no account-side preparation or order mutation occurs; assert consumer-visible terminal reason and preserved source history.
- Exercise duplicate/restart/ambiguous child outcomes through existing lifecycle fixtures; inspect durable ownership and no duplicate mutation behavior.

Exit gate C: all relevant/full-suite checks pass, changed paths are exercised, real output inspected, documentation updated, and throwaway local smoke scaffolds removed. Permanent regressions remain.

### Stage D — Prepare an approved production operation

1. Obtain scoped deployment approval. Latch recovery and live mutation approval are separate boundaries, not inferred from deployment approval.
2. Refresh provider account scope/UID, positions, ordinary and plan orders, active latches, unresolved intents/reservations, worker leases, baseline receipt, and fallback ownership using GET-only tools and DB reads. Never use mutating `AsyncBitgetVenue.preflight()` for this inventory.
3. Record current source/container digests, effective per-service LIVE policy/mutation flags, and user timer/service configuration. Capture secrets only in their existing secret store, never in the evidence report.
4. Establish a recovery/rollback-compatible database backup through the existing backup procedure. Record its location and readability; do not test destructive restore on production.
5. Build the release without disturbing running consumers. Run `bash scripts/check_production_live_policy.sh` before recreate commands.
6. Plan maintenance containment under the current LIVE-only policy. A latch blocks entries but does not necessarily stop existing fallback/source/operator mutations; identify every mutation consumer explicitly.
7. If the account is nonflat, do not stop all protection/close consumers or use the flat-baseline recovery runbook. Obtain a scoped maintenance procedure that preserves existing exposure protection before continuing.

Exit gate D: approved rollout, fresh inventory, verified policy, rollback identity, backup, and safe containment plan. Missing approval is a production gate, not a reason to stop reachable local implementation.

### Stage E — Deploy while preserving safety state

1. Rebuild/recreate the intended Python application images/consumers according to the approved maintenance plan. The current verifier compares the entire Python source tree in all eight application services, so updating only the dispatcher would leave source-lineage mismatches.
2. Application consumers to account for: intake, analyzer, dispatcher-bitget, monitor-bitget, operator-bot, notification-sender, source-management, and web. Do not recreate PostgreSQL or rerun migration/init merely to make counts green; handle them only if a reviewed migration becomes necessary.
3. Preserve production LIVE/LIVE/1 configuration and separate mutation flags. Do not clear latches, edit risk settings, or requeue rejected signals during rollout.
4. Verify actual container state, worker progress, dependencies, copied source digests, and installed host report code/timer path. An unchanged unit does not require needless timer reinstallation.
5. Exercise read-only account/health paths in the new containers. Verify source lineage before using the new code for clock/recovery diagnostics.
6. Run the runtime verifier without modifying it to ignore active latches. A `kill_switch_active` result is an expected admission block until approved recovery, not release acceptance. Because the verifier exits early at that gate, collect later lineage/provider checks separately before recovery rather than assuming they ran.

Exit gate E: correct deployed code and policy, safe existing exposure handling, truthful surfaces, and explicit blocked-entry state if latches remain. This is deployment completion, not operational acceptance.

### Stage F — Approved flat-baseline recovery

Execute only if the owner authorizes recovery and fresh inventory proves the existing tool's flat/ledger/baseline conditions. All account mutation consumers must remain stopped throughout proof and apply, as required by the tool. Prevent concurrent manual/external account mutations as well; DB table locks cannot fence them.

1. Identify and pause mutation consumers through the approved containment procedure. Keep LIVE configuration intact; do not replace containment with execution=0.
2. Use the existing CLI inside a one-off container built from the verified release if the normal consumers are stopped. Its runtime must retain the required LIVE/LIVE/1 and observe-only stream settings, and only the recovery command may execute.
3. Resolve the expected authenticated account UID and full audited baseline UUID from durable records. Do not use shortened IDs from chat or invent an approval reference.
4. Run proof with `--dry-run --confirm --approval-reference "$APPROVAL_REFERENCE" --account-id "$EXPECTED_ACCOUNT_ID" --baseline-id "$AUDITED_BASELINE_ID"`. These variables must hold reviewed, real values before invocation.
5. Review `applied=false`, exact allowed latches, account/baseline binding, current flat inventory, ledger readiness, connected/authenticated stream, both-leg pongs, fresh mark, and `recovery_digest`.
6. Apply only with the same approved binding and `--apply --expected-digest "$REVIEWED_RECOVERY_DIGEST"`. The tool rechecks fresh evidence and changed rows under locks; a refusal stops the operation.
7. Read back latch rows and recovery/audit notification evidence. A successful CLI exit alone is not a reason to skip readback.
8. Resume consumers in the approved order, observing monitor/protection readiness before normal admission. Run the complete runtime verifier again.
9. If any new fault/latch appears, retain it and investigate its evidence. Do not repeatedly release latches or widen thresholds to reach PASS.

Exit gate F: reviewed recovery applied to only the permitted latches, consumers resumed safely, and `runtime_check=PASS` observed with matching source/policy/ledger/provider evidence.

### Stage G — Operational acceptance without forced trading

1. Request authorized Telegram `/health` and `/balance` reads; inspect delivered output. Trigger/send one scheduled-format card only under the appropriate notification-operation approval, then confirm actual delivery and the installed timer's next invocation.
2. Verify the account read's venue/product/time, admission-history label, latest source message, ledger/PnL scope, and real unknown sections on the actual Telegram surface.
3. Inspect clock evidence and both WebSocket legs over multiple monitor cycles and at least one scheduled report boundary. A six-hour report interval is a reporting cadence, not a claim that one sample proves durable reliability.
4. Inspect naturally arriving eligible source dispatches through receipt, canonical parsing, eligibility, route decision, sizing, reservation, intent, provider acknowledgement/readback, and protection/reconciliation.
5. Do not force a trade, replay an old signal, raise risk caps, or treat no new eligible signal as a failure of the local fix.
6. Record lifecycle coverage honestly: entry accepted is not filled; filled is not protected; a waiting LIMIT leg is not closed; submitted close is not confirmed flat. Keep unobserved stages marked unverified.

Exit gate G: reporting and readiness accepted with evidence. End-to-end live-lifecycle acceptance requires naturally observed or separately authorized real lifecycle coverage; if no eligible event occurs, release readiness may be accepted while live-lifecycle verification remains pending.

## 6. Acceptance scenario matrix

| Case | Required observable result | Primary existing regression area |
|---|---|---|
| Fresh account, October 1 admission evidence | Live account timestamp/value shown; admission age independently labeled | Shared/operator/provider health |
| Account GET fails | Account UNKNOWN; no stale balance presented as live; other successful sections survive | Health truthfulness |
| DB/PnL query fails | Ledger/PnL UNKNOWN; no invented fees, fills, empty reservations, or flatness | Operator/report health |
| Successful zero-fill ledger | Zero reported as known zero, with correct scope | Report health |
| Stored messages exist | Latest stored message shown with source time; no false no-record statement | Shared health reader |
| Unknown provider and DB counts | No MATCH/OK reconciliation assertion | Health truthfulness |
| MAGIC-like split-grid mismatch | Exact child grid or explicit genuine infeasibility before mutation | Live sizing/entry routing |
| Split child below min notional/quantity | Rejected even if total alone satisfies the minimum | Live sizing/admission |
| Market/limit maximums differ | Each child respects the appropriate maximum and combined risk budget | Live sizing/dispatch execution |
| Full-market route | Existing approved sizing/routing behavior preserved | Live sizing/entry routing |
| Slow valid synchronized clock response | Offset estimate reflects clocks, not pre-await latency | Bitget client |
| High uncertainty, failed sample, or wall-clock jump | Unavailable/unhealthy; no zero-skew substitution | Client/monitor |
| Excessive real positive/negative skew | Admission blocked at existing threshold policy | Client/monitor |
| Removed contract | Clear venue rejection before account preparation; canonical signal retained | Venue/live dispatcher |
| Contract lookup malformed/unavailable | Provider failure, not unsupported-symbol fabrication | Client/venue |
| Stream healthy, historical latch active | Entry-blocked state and latch evidence remain visible | Monitor/report execution health |
| New/global latch or changed digest | Recovery refused atomically; no release | Live latch recovery/PostgreSQL |
| Nonflat provider account | Flat-baseline recovery refused; protection remains subject to approved procedure | Live latch recovery |
| Child timeout/crash/restart | Reconcile durable ownership, no blind duplicate entry POST | Entry lifecycle/dispatch execution |
| Unrelated/manual/protective orders | Remain outside exact-owned entry cancellation | Existing source-management lifecycle |
| Fallback record without verified live epoch | Not labeled effective protection merely from registration | Health scope/protection |
| Stale source copied in a container | Runtime acceptance fails; host SHA alone cannot pass | Production policy/runtime verifier |

Do not add new tests merely to assert copied text, helper forwarding, source layout, implementation defaults, or a mock echo. Retain deterministic behavioral/boundary/race tests that catch a plausible consumer-visible failure. Use existing source-management/entry regression suites for unchanged semantics rather than duplicating their cases.

## 7. Rollback and incident response

Rollback triggers: unexpected provider mutation, duplicate ownership/POST, risk-budget violation, lost protection evidence, changed production policy, new unexplained admission latches, false financial/flatness claims, or a release that fails actual source-lineage/readiness checks.

Procedure:

1. Use approved incident containment to stop new entries without violating LIVE-only configuration. Preserve protection and close recovery for any actual exposure.
2. Refresh provider exposure/orders and local intents/reservations. A newly opened position makes the earlier flat maintenance assumptions invalid.
3. Preserve the faulty release's evidence and durable state. Do not delete intents, release reservations blindly, undo fills, or requeue dispatches.
4. Restore the recorded compatible application image/source release under an approved maintenance plan. Reconcile claims created by the newer release before allowing older code to consume them.
5. Keep safety latches active unless a separately approved evidence-led recovery releases the exact eligible rows.
6. Do not automatically restore a database backup: it can erase exchange-side orders/claims created after the backup and cause duplicate execution. Database restore is a separate destructive recovery decision.
7. Rerun applicable policy, container-lineage, provider, ledger, and health checks. The older version retains known defects; rollback restores a prior operational state, not proof those defects are fixed.
8. Record the containment/release identity, provider state, preserved commitments, and unresolved work in this tracker.

Schema expectation: no migration. If that changes, add an explicit forward/backward compatibility and rollback decision before deployment; never improvise a schema downgrade after live mutations.

## 8. Progress tracker

### 8.1 Status rules and ownership

- DONE means the row's specified evidence was observed and recorded. Source changes alone do not complete verification/deployment rows.
- PENDING means not started. ACTIVE means work is underway. BLOCKED names an actual missing prerequisite. UNVERIFIED means implemented or operating but without the necessary behavioral/runtime proof.
- The integration owner updates this table immediately after a completed gate or encountered blocker. Record timestamp, release identity, exact command/scenario, result, and evidence location.
- Code/test work can proceed without production authorization. Deployment, notifications sent for verification, consumer shutdown, latch recovery, and provider mutations require their applicable separate authority.
- Do not calculate a project-completion percentage from discovery tasks. Investigation completion is not implementation completion.

### 8.2 Work-item register

| ID | Work item | Status | Depends on | Owner | Evidence / next action |
|---|---|---|---|---|---|
| D01 | Recover prior request and audit evidence | DONE | None | Integration owner | Original session recovered; prior verdict checked against raw output |
| D02 | Trace balance/report sources and active schedule | DONE | D01 | Integration owner | E03–E07; actual user timer and delivered report inspected |
| D03 | Exercise current slash-health path and identify gaps | DONE | D01 | Integration owner | E05/E10; live container read-only loader/renderer exercised |
| P01 | Publish comprehensive design and execution contract | DONE | D01–D03 | Integration owner | This document; no implementation/deployment claim |
| A01 | Establish supported local Python/uv environment | DONE | P01 | Integration owner | `uv sync --all-groups --python 3.12` succeeded locally; no production credentials used |
| A02 | Map current references, callers, release state, replay inputs | DONE | P01 | Integration owner | Current source/callers mapped; split contract fixed; baseline clock timing bias reproduced offline |
| I01 | Implement live account source/time/scope semantics | DONE | A01/A02 | HealthRemediation | Allowlisted normalized provider DTO; successful-read observation time distinct from render/admission time |
| I02 | Complete shared ledger/PnL/latest-message loading | DONE | I01 | HealthRemediation | Canonical ledger loader used by cron and slash; actual PostgreSQL and host JSON SQL adapter smoke passed |
| I03 | Correct unknown/default/reconciliation rendering | DONE | I02 | HealthRemediation | Independent UNKNOWN handling; no unknown-count match or fabricated fees/net PnL |
| I04 | Implement split-aware candidate sizing | DONE | A01/A02 | SizingRemediation | Joint split prices, 4-step grid, leg minima/maxima, recomputed admitted risk |
| I05 | Integrate child feasibility and final admission values | DONE | I04 | Integration owner | Actual service+PostgreSQL smoke: total 8.4, market 2.1, limit 6.3, notional 19.656, margin 0.9828; immutable old snapshot preserved |
| I06 | Implement bounded clock measurement and caller handling | DONE | A01/A02 | ClockVenueRemediation | 200ms synchronized sample => 0ms; real 15000ms offset preserved; 20s reply => unavailable |
| I07 | Implement metadata-first contract eligibility | DONE | A01/A02 | ClockVenueRemediation | FLOW-like absence refuses before account preparation; provider failures remain distinct |
| I08 | Correct latch/fallback interpretation and validate recovery contract | DONE | I03/I06 | Integration owner | Real disposable PostgreSQL proof/no-write, wrong-digest refusal, exact apply+outbox smoke passed; production legacy fallback bindings inspected GET-only |
| I09 | Migrate legacy callers and remove duplicate shell report | DONE | I01–I03/A02 | Integration owner | Local executable callers migrated/checked; obsolete shell and old renderer removed; installed production Python timer unchanged |
| I10 | Update affected behavioral regressions and contract docs | DONE | I01–I09 | Integration owner | Core docs/regressions and source-management cancellation wiring updated; final six affected suites passed (110 tests), lint/format/types passed |
| V01 | Run integrated relevant regression suites | DONE | I10 | Integration owner | Initial 457 pass plus one test-clock fixture issue; corrected route boundary passed; later fallback latch regression passed |
| V02 | Run full suite, format/lint/type checks, isolated DB cases | DONE | V01 | Integration owner | Full isolated suite: 2095 passed, failures/errors/skipped all zero; final lint/format/types, compileall and package builds passed |
| V03 | Exercise integrated report/admission/clock/recovery smoke scenarios | DONE | V02 | Integration owner | No-send cron/slash, real PostgreSQL admission/recovery/close release, clock/eligibility and independent failures observed; no live provider mutation |
| V04 | Close local acceptance and remove throwaway scaffolds | DONE | V03 | Integration owner | Local acceptance complete; temporary smoke/diagnostic scripts removed; disposable verification services stopped after evidence collection |
| O01 | Obtain scoped production rollout authority | DONE | V04 | Owner / operator | Owner explicitly approved commit, push and LIVE deployment, with clean signal processing and excellent health reporting |
| O02 | Refresh safety inventory, record rollback release, create backup | DONE | O01 | Operator | All three futures products and four pending families proved flat; no active dispatch lease; baseline matched; verified 1751209-byte backup and eight prior image rollback tags preserved |
| O03 | Build approved release and check rendered LIVE-only policy | PENDING | O02 | Operator | No running-consumer changes yet |
| O04 | Deploy application consumers and verify actual source lineage | PENDING | O03 | Operator | Stage E; preserve latches and effective flags |
| O05 | Verify deployed read-only health/clock/provider paths | PENDING | O04 | Integration owner | Gate E; distinguish blocked admission from deployed code correctness |
| R01 | Obtain recovery authority and establish safe flat containment | ACTIVE | O05 | Owner / operator | Approved return to LIVE operation includes evidence-led release of the exact eligible admission blocks; fresh flat proof collected, mutation-consumer containment still required |
| R02 | Collect and review flat-baseline recovery proof | PENDING | R01 | Operator | Exact UID/baseline/reasons/digest/stream/ledger evidence |
| R03 | Apply approved digest-bound recovery and read back | PENDING | R02 | Operator | Refusal or changed evidence stops; never repeat blindly |
| R04 | Resume consumers and observe full runtime verifier PASS | PENDING | R03 | Operator | Actual `runtime_check=PASS`, not healthy container count alone |
| S01 | Verify delivered Telegram account/health and scheduled card | PENDING | R04 | Integration owner | Actual surface/delivery under notification authority |
| S02 | Observe multiple monitor cycles and a report boundary | PENDING | S01 | Integration owner | No unexplained relapse; freshness meanings remain truthful |
| S03 | Observe a real eligible live lifecycle without forced replay | PENDING | S02 | Integration owner | Natural event or separate authorization; keep uncovered stages unverified |
| S04 | Publish release verdict and close tracker | PENDING | S01–S03 | Integration owner | Separate local, runtime readiness, and live-lifecycle evidence |

Local slices are assigned to HealthRemediation, SizingRemediation, and ClockVenueRemediation; the integration owner owns shared service wiring, cutover, documentation, and final verification.

### 8.3 Current next action and production gates

Current action: commit/push the isolated verified remediation, deploy under LIVE/LIVE/1, collect exact approved recovery proof with mutation consumers stopped, then read back admission and delivered health evidence.

Remaining production gates: verified new container/source lineage, digest-bound current recovery proof/apply, full runtime acceptance, delivered health report, sustained observation, and natural next-signal/lifecycle evidence. No forced trade or historical replay is authorized by deployment approval.

If no real eligible signal arrives, S03 remains pending/unverified; do not mark the project end-to-end complete. Record an explicit partial verdict of local verification plus operational readiness instead of implying live acceptance.

### 8.4 Evidence update format

For each transition, append a concise evidence entry with:

- Work-item ID and timestamp.
- Source/release identity and environment.
- Exact command or scenario exercised, with secrets redacted.
- Observed output/verdict and evidence artifact location.
- Any unexercised boundary, blocker, or operator approval used.

Initial planning entry: D01–D03/P01 completed from session recovery, source inspection, current DB/account reads, actual slash loader/render smoke, and installed timer/journal inspection. No remediation tests, deployment, recovery apply, or new live trade were performed.

Local implementation entry: the owner instructed proceeding with the plan. `uv sync --all-groups --python 3.12` completed in the isolated repository. A deterministic pre-edit clock smoke using the existing client expression produced `reported_skew_ms=-20000` for a simulated synchronized server delayed by 20000ms (`actual_offset_ms=0`). Three independent local implementation slices started; no builds/tests/linters/formatters are run against partial integration. Shared service split inputs and per-child maximum defenses are wired. The duplicate local shell reporting path and its incidental source-copy tests are removed; scheduled production reporting remains unchanged.

### 8.5 Historical intermediate verification and blocker resolution

Observed local evidence:

- Integrated remediation selection: 457 passed, one deterministic route-window test fixture failed because its datetime subclass did not match the patched isinstance check; that fixture was corrected and the exact test then passed.
- Actual disposable PostgreSQL smoke: cron no-send JSON SQL adapter and slash loader agreed on account/ledger semantics; stored historical PnL and latest message appeared; failed DB reads retained UNKNOWN without erasing successful account data. Admission persisted total 8.4, children 2.1/6.3, notional 19.656 and margin 0.9828, while preserving the old admission snapshot.
- Clock smoke: 200ms synchronized request returned 0ms offset; a real 15000ms offset was retained; a synchronized 20s reply was unavailable rather than false drift. Unsupported FLOW-like contract refused before account preparation.
- Existing recovery exercised against a disposable real database: dry-run left two latches and outbox unchanged; wrong digest refused; matching proof applied only the exact local fixture latches with audited outbox/readback. This was not production recovery.
- Ruff lint/format, mypy (107 source files), compileall, wheel/source packaging and remaining shell syntax passed at an earlier integrated checkpoint. Subsequent service/fixture edits require final rerun; those earlier results are not a claim about the final candidate.
- Full suite did not pass. Stale whole-schema/migration-version expectations and incomplete close-entry evidence fixtures were corrected without changing production migrations or weakening ownership constraints. A later close-release regression still raised `ValueError: bound close lacks exact real provider fill evidence` for contradictory ledger input instead of returning the expected rejected admission outcome.
- Unit-only run: 1785 passed, five failed. Three failures lacked Docker CLI/Compose in the isolated runner; those prerequisites were installed. One obsolete operator assembly mock-forwarding test was removed. One source-management regression exposed missing owned-entry cancellation adapter wiring; the local service now uses the existing gated execution graph and passes its entry execution to the real gateway, with the same fixed 1 USDT factory cap supplied in Compose. Final verification of those corrections remains necessary.
- Failed close-fixture teardown waited on idle-in-transaction intent reads. Tests were stopped/restarted in the disposable runner; no production database was involved. No transaction-cleanup change or rejection exception suppression has been applied to production code merely to force the suite green.

Read-only production observations during this implementation: PUMPUSDT and PENGUUSDT fallback registrations have null environment and provider position epoch; provider USDT-FUTURES inventory and local open-position query were flat. No fallback rows, safety latches, orders, risk settings, source queues, or deployed containers were changed.

The candidate is not deployment-ready until the remaining full-suite failure and transaction behavior are resolved and verification reruns pass. Production authorization, deployment/source-lineage acceptance, approved latch release, actual Telegram delivery, and real lifecycle observations remain pending.

Report checkpoint: after restoring the isolated Docker runtime, six current affected suites passed: source-management execution, service startup integration, production LIVE policy, Compose owner-health isolation, operator health, and service admission wiring (110 tests). Final `ruff check src tests scripts` passed, `ruff format --check src tests scripts` reported 289 files formatted, and mypy passed all 107 source files. This does not supersede the unresolved full-suite close-evidence failure. Temporary smoke and failure-diagnostic source files were removed; test artifacts remain evidence.

Close-blocker implementation checkpoint: `get()` and standalone residual reads now deterministically close their read connections on success and failure. Close binding/release reuse the locked caller cursor for residual evidence. Close identity/state/economics are validated before residual calculation; an explicit `OwnedQuantityEvidenceError` refuses binding or capacity release, while SQL errors still roll back and propagate.

Targeted verification: 139 tests passed across verified-close release/lifecycle, live-intent read/admission, and operator gateway suites. Ruff lint and formatting passed (289 files); mypy passed all 107 source files. A real disposable PostgreSQL smoke proved contradictory close rejection with consumed ownership/fill economics preserved, genuine SQL failure propagation after rollback, exact verified release, subsequent admission, and no idle-in-transaction sessions from corrected reads/release. The complete suite is still running; no production provider or database mutation occurred.

### 8.6 Final local acceptance

- Complete command: `docker exec omp-bitget-runner-20261013-0959 uv run pytest -q --junitxml=artifacts/remediation-final-20261013.xml`.
- Observed result: **2095 passed in 158.07 seconds**. JUnit readback independently confirmed tests=2095, failures=0, errors=0, skipped=0. Disposable real PostgreSQL cases ran; no database acceptance cases were skipped.
- Targeted close/read/gateway suite: **139 passed**; evidence in `artifacts/close-blockers-targeted-20261013.xml`.
- Final Ruff lint/format passed (`289 files already formatted`), mypy passed all 107 source files, compileall passed, and `uv build` produced the source distribution and wheel.
- Actual close smoke proved: correct intent/residual reads close all owned connections; wrong-side close rejects with consumed ownership and immutable fill economics; injected SQL failure propagates after rollback; exact verified close releases only its reservation; new admission then succeeds; transaction readback finds no idle-in-transaction sessions from the corrected paths.
- Temporary smoke scripts were removed and disposable verification services were stopped. Permanent behavioral regressions and XML evidence remain. No production database/provider state, notifications, safety latches, or deployed containers were mutated.

The former local acceptance blockers are resolved. These results support an approved production rollout candidate, not deployed readiness or successful live trading. O01–O05, R01–R04, and S01–S04 remain pending and require their production evidence/approval boundaries.

### 8.7 Approved production rollout preparation

Owner approval: “I approve, commit, push and deploy it live. Next signal must be processed cleanly and health check is excellent.” Execution preserves risk/source/ownership/protection gates and uses the supported recovery procedure, not blanket SQL latch clearing.

Fresh authenticated GET-only inventory matched the audited account and proved no position rows or pending ordinary/normal-plan/profit-loss/trailing-plan orders across USDT-, USDC-, and COIN-FUTURES. Current account available/equity remained 9.17026367 USDT. No dispatch worker lease was active. Exact latches remained `bitget/clock-skew-exceeded` and `bitget-protection-stream/socket-not-connected`.

Rollback base: deployed main `69a4581870d94ebf3e99895b40faee91b426ee0d`. Eight existing application images were retained under `fatty-remediation-rollback-<service>:69a4581`. Database backup `/home/valarion/apps/fatty-multi-exchange-trader/backups/fatty_trader_20261013T154125Z.dump` passed `pg_restore --list` readability verification, size 1751209 bytes. Backup retention deletion was disabled for this operation.

Unrelated workstation changes are excluded from the release: FOMO-Pulse documents and the pre-existing pyproject blank-line delta. No production code/provider mutation or latch release has occurred at this preparation checkpoint.

## 9. Final release definition of done

The release can be called fully complete only when all applicable local, deployment, operational, and live-lifecycle evidence is recorded:

1. Provider balance has truthful source/scope/observation time; admission history is distinct and immutable.
2. Both report loaders have complete shared meanings and independent unknown handling; the legacy executable path has no remaining callers.
3. Exact split sizing satisfies both legs and approved total risk, or rejects legitimately before mutation.
4. Clock offset is not inferred from request latency; uncertain evidence remains fail-closed.
5. Unsupported contracts are rejected before account-side preparation.
6. Durable safety latches and active exposure are handled through the approved evidence/recovery procedure, not blanket clearing.
7. Existing entry/source-management/protection/ownership/restart behavior remains intact.
8. Relevant/full-suite/DB checks and integrated smoke outputs pass and are recorded.
9. Actual deployed code, per-service LIVE policy, worker readiness, ledger/provider state, and scheduled/on-demand Telegram surfaces are verified.
10. Real lifecycle observations support the claimed execution/protection/reconciliation coverage; remaining unobserved stages are explicitly identified rather than marked done.
