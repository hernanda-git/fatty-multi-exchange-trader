# Bot Console UX: Page-by-page specification

## Status and reading contract

**Planning/documentation only — SPECIFIED, not implemented, tested, accepted or released.**
All redesign implementation phases R0–R7 are NOT STARTED. Existing frontend/backend source is evidence of interfaces, not production observation or UX acceptance. The user-reported poor UX is the redesign baseline; screenshots, passing automation or this specification cannot establish human usability.

Read with [MASTER PLAN](BOT-CONSOLE-UX-MASTER-PLAN.md), [EXECUTION](BOT-CONSOLE-UX-EXECUTION.md) and [PROGRESS](BOT-CONSOLE-UX-PROGRESS.md). The master owns visual tokens, navigation, state/action principles and scope; execution owns sequencing, evidence and release gates. This document owns page composition, retained capabilities and scenario IDs. All acceptance boxes below are intentionally unchecked.

The redesign is FRONTEND-ONLY. Reuse existing reads, typed action schemas, capability responses, server policy, previews, jobs and consent. A missing read field or route binding is a named prerequisite, not permission to implement a backend endpoint, synthesize data or remove the capability. No deployment, trades, sends, latch release, credential change or live runtime verification is authorized by this plan.

## Evidence register and interpretation

Paths below are relative to the repository root. Named symbols/sections are stable lookup anchors; source line numbers may move.

| Ref | Inspected reference | Authority for this specification |
|---|---|---|
| F1 | `frontend/src/main.tsx`: `screens`, `tabs`, `route`, `Value`, `App` | Fifteen existing screens; tabs, path/query handling, login, overview evidence, SSE, generic action/review rendering. |
| F2 | `frontend/src/domain.tsx`: `columns`, `panels`, `DomainDetail`, `LatchBanner` | Existing tables, original source/media, linked evidence, financial fields and safety presentation. |
| F3 | `frontend/src/forms.tsx`: `serverFilters`, `Filters`, `SecretStage`; `frontend/src/api.ts` | Allowlisted filter UI, write-only secret staging and same-origin client. |
| B1 | `src/fatty_trader/console/api.py`: `create_router` | Source-registered `/api/v1` routes, login/consent, scopes, media, exports, session revocation, nested resources and jobs. |
| B2 | `src/fatty_trader/console/reads.py`: `_read`, `_table`, `_detail`, `_positions`, `_pnl`, `_media`, `_FILTERS` | Actual read composition, nullable evidence, snapshot provenance, linked records, schedule, ownership and ledger limitations. |
| B3 | `src/fatty_trader/console/commands.py`: `catalog`, `capabilities`, `CommandService`; `console/trading.py`: `ACTION_SCHEMAS` | Exact action vocabulary, runtime capability gating, manual/owned trading arguments and distinct-approver prohibition. |
| B4 | `src/fatty_trader/console/operations.py`: `CONFIG_FIELDS`, `ACTION_SCHEMAS`, `ACTION_POLICIES` | Configuration/source/notification/maintenance/backup/recovery schemas and tiers. |
| D1 | [UI/API SPEC](BOT-CONSOLE-UI-API-SPEC.md), §§2–9, particularly §3 pages and §4 endpoint catalog | Financial semantics, truth model, intended contracts and historical design context. |
| D2 | [CAPABILITIES](BOT-CONSOLE-CAPABILITIES.md), §§2–7 | Ownership boundaries, action-specific exclusions and named historical prerequisites. |
| D3 | [USER GUIDE](BOT-CONSOLE-USER-GUIDE.md), private access, screen matrix, preview flow, incidents and maintenance | Current password-owner model and retained user workflows. |

D1/D2 contain older baseline statements that every console endpoint is proposed and manual entry is unsupported. Current B1–B4 contain source implementations and action schemas. Do not repeat the older baseline as a current absence, and do not infer deployment or availability from source presence. For every action, `/api/v1/actions`, exact preview and execution authorization remain authoritative. Historical identity-provider/WebAuthn language is superseded by D3 and the current password-session implementation, not reintroduced by this redesign.

## Navigation and legacy coverage

Five top-level destinations: **Overview** `/overview`, **Trading** `/positions`, **Signals** `/signals`, **Activity** `/audit`, **Settings** `/config`. Account/performance is secondary under Overview; notifications and management are secondary under Activity; recovery, services, backups and access are advanced Settings. New trade is an explicit Trading action, not a sixth top-level destination. Preserve existing paths and query entry points; change labels/grouping rather than gratuitously rewrite routes.

| Existing screen/tab or entry | New destination and page ID | Preserved entry contract |
|---|---|---|
| Unauthenticated session/password form | Sign in — UX00 | Current requested supported path remains the return destination. |
| Overview | Overview — UX01 | `/overview` |
| Positions and exact record | Trading > Positions — UX02 | `/positions`, `/positions/{id}` |
| Orders & intents; orders/fills tabs | Trading > Orders / Fills — UX03 | `/orders`, `/orders?view=orders`, `/orders?view=fills`, `/orders/{id}`, `/orders/fills/{id}` |
| Protection and exact record | Trading > Protection — UX04 | `/protection`, `/protection/{id}` |
| Trading desk | Trading > New trade — UX05 | `/trading` |
| Signals list | Signals > Inbox — UX06 | `/signals` |
| Signal detail; linked dispatch detail | Signals > Trace — UX07 | `/signals/{id}`, `/signals/dispatches/{id}` |
| Audit & jobs: jobs, approvals, audit tabs | Activity > Jobs / Approvals / Audit — UX08 | `/audit`, `/audit?view=jobs`, `/audit?view=approvals`, `/audit?view=audit` |
| Management and exact record | Activity > Management — UX08 | `/management`, `/management/{id}` |
| Exact job and steps | Activity > Job — UX09 | `/audit/jobs/{id}` |
| Notifications and exact record | Activity > Notifications — UX10 | `/notifications`, `/notifications/{id}` |
| Configuration: effective, history, drafts; secret staging | Settings > Configuration / Integrations — UX11 | `/config`, `?view=config`, `?view=config/history`, `?view=config/drafts`, `/config/history`, `/config/drafts/{id}` |
| Recovery: recovery, baselines and baseline detail | Settings > Advanced > Safety & recovery — UX12 | `/recovery`, `?view=recovery`, `?view=recovery/baselines`, `/recovery/baselines/{id}` |
| Services & deployment: services, releases | Settings > Advanced > Services & releases — UX13 | `/services`, `/services/{id}`, `/services?view=services`, `/services?view=releases` |
| Backups and exact record; maintenance preparation action | Settings > Advanced > Backups & maintenance — UX14 | `/backups`, `/backups/{id}`; contextual maintenance flow |
| Access: access, principals, sessions, policy | Settings > Advanced > Access & sessions — UX15 | `/access`, `?view=access`, `?view=access/principals`, `?view=access/sessions`, `?view=access/policy`; named nested list paths |
| Account & PnL: account, account/pnl, reservations, admission-snapshots | Overview > Account & performance — UX16 | `/account`, matching `?view=` values, `/account/pnl`, `/account/reservations`, `/account/admission-snapshots` |

### N: navigation prerequisites, not solved claims

- **N1 — incomplete frontend route registry:** F1 recognizes only fifteen first segments. `Value` generates `/releases/{id}` and `/approvals/{id}` while those are not screen roots; direct `/fills`, `/dispatches` and `/jobs` are also not current screen roots. Keep existing valid nested links; define frontend mappings to existing B1 resources during R0/R2. Do not claim every emitted link currently works.
- **N2 — list/detail asymmetry:** B1 registers details for releases/approvals/audit, but F1 has no dedicated view-aware detail binding for all of them. B1 has no arbitrary account reservation/snapshot/history/session/principal detail endpoint. Use existing list/read payload in a detail panel where sufficient; a new standalone read is a prerequisite, not an assumed route.
- **N3 — query and return state:** F1 carries same-screen queries through `Link`, resets filters on tab changes and has in-memory previous cursors. Specify supported filters per destination, explicitly retain valid return state, remove incompatible cursor/view values, and test reload/back without mutation. Do not promise cross-screen filter preservation is already correct.
- **N4 — hosting/deep-link entry:** source path parsing is not evidence that the deployed server serves the app for every nested URL. Record authenticated direct-load/refresh behavior and the host fallback prerequisite before accepting deep links; no deployment change belongs to this plan.
- **N5 — identity of details:** use opaque resource IDs, ownership root and position epoch, never symbol alone. An unavailable/expired resource needs a safe return route, not silent Overview fallback. Explicit revision IDs must survive signal trace navigation.

## Shared implementation rules for every page

- Apply the master's restrained neutral light first-visit theme, saved preference/system option, type/spacing tokens, semantic status colors and tabular financial numbers. No gradients, marketing copy, decorative charts, pill soup or primary raw-object dump.
- Header contains account, LIVE/environment, compact connection/actionable status and identity only. Venue is contextual text, not an invented exchange selector. Theme/timezone live under identity/preferences; UTC default and original UTC instant remain inspectable.
- One compact actionable safety summary links to exact blocking evidence. Do not repeat the full latch/config/clock/stream/account panel on every page. A connected event stream is not market-data freshness or trading admission. The independent clock latch is not reobserved or cleared by planning.
- Each financial value identifies currency/quantity unit and source; price uses quote currency per base unit, quantity uses contract/base unit supplied by the contract, leverage uses `x`, rates explicitly use `%` when applicable, duration uses seconds/hours, bytes are labeled. Decimal strings are formatted, not financially recomputed with browser floats.
- First viewport prioritizes user question, scope, actionable state, key values/table and primary action. IDs, policy versions, raw safe fields and full evidence move into progressive disclosure, remain selectable/copyable and never substitute for plain-language meaning.
- **State pack SP:** loading names the read and preserves layout; confirmed-empty requires successful scoped complete evidence; filtered-empty offers clear filters; stale retains original observation time and says last known; partial identifies missing sources; error keeps useful authorized last-known content and request ID; permission explains denied scope without leaking hidden resources; unavailable/unsupported names the missing capability/data prerequisite. Null is never zero; stale never fresh; complete ledger query never complete provider inventory.
- Every page below implements SP with its page-specific cases. Read refresh does not imply provider polling, admission refresh or an authorized mutation. Keep backend-native state and freshness separate; never flatten all failures into a green/amber status.
- **Action flow AF:** dedicated named form/context action → exact read-only server preview → effect/exclusion/ownership/gate review → backend-required reason, phrase, approval reference and digest-bound session consent → enqueue once → UX09 readback. No second password/factor prompt. Shared owner cannot supply distinct Tier 3 approval; explain that barrier rather than offering self-approval.
- AF preserves idempotency receipt recovery and identical-operation retry after lost response, invalidation after edits/expiry/version changes, server permission checks and no generic mutation retry. Accepted/queued is not completed. No new economic action to resolve an already claimed effect.
- **Interaction pack IK:** semantic headings/forms/tables, descriptive links, visible focus, keyboard-only completion, dialogs trap/restore focus and Escape only dismisses view, not work. Navigation uses links; true local tabs use arrow/Home/End semantics. No color-only states or click-only rows. Announce meaningful read/action changes without announcing every clock tick.
- Meet WCAG 2.2 AA, 200% zoom and 320 CSSpx reflow except explicitly labeled horizontally scrollable table regions. Primary touch targets 44px; dense row controls at least 24px. Mobile keeps identity, critical state/freshness and financial units visible; secondary columns become labeled detail, never disappear from access. Five-second comprehension is a future human test, not an automation claim.

## UX00 — Password sign-in

**Goal / phase:** enter the private scoped console with the existing sole shared password; R2.
**Entry/navigation:** unauthenticated supported routes show this form without changing the intended safe return path. Session expiry returns here and explains that durable jobs retain their outcomes. No username, invitation, OTP, passkey, account creation or password reset flow is introduced.

**First viewport and fields:** product name, “Sign in”, one Console password input, one Sign in button, brief authorized-origin/private-tunnel guidance. Use password/current-password semantics; show an accessible error near the form. No marketing slogan, fake account summary or exposure data before authorization.
**Actions:** primary Sign in; secondary retry session check after a network failure. Do not invent credential recovery; refer to the existing owner/operations setup documentation if needed.
**Flow:** check session → submit existing login → clear password component state on submission → establish scoped session/CSRF → safe original route. No credential values in URLs, persisted form state, analytics, logs, screenshots used as test artifacts or copied errors.
**Contracts:** F1 `Auth`/`session`; F3 client; B1 `GET /api/v1/session`, `POST /api/v1/auth/login`, `/auth/logout`; D3 private access. Origin, rate limit and session failures are server policy, not client overrides.

**States:** SP specialized: checking session/loading login prevents duplicate submit; empty password has a field error; invalid password stays generic; rate-limit/origin/network errors retain useful recovery guidance without echoing input. Expired/revoked session clears protected views. Stale/partial session evidence is not authenticated access; permission/unavailable private origin never presents account data.
**Responsive/keyboard:** single-column form at all sizes; Enter submits, focus moves to error summary then input, busy state announced once; no automatic focus stealing after entry.

**Critical scenarios:**
- **UX00-S1:** valid password on an existing nested route opens the authorized target, password clears, no extra factor appears.
- **UX00-S2:** revoked session during job follow-up requires sign-in but does not resubmit or lose the durable job identity.

**Acceptance / edge checklist:**
- [ ] One credential field only; origin/rate-limit/network errors and submit clearing preserve D3 semantics.
- [ ] Keyboard/mobile sign-in and return path work; invalid/expired route and denied resource disclose no private data.

## UX01 — Overview

**Goal / phase:** understand actual exposure, account observation and the next actionable issue; R3.
**Entry/navigation:** default `/overview`; primary nav Overview. Secondary Account & performance opens UX16. Cards link to exact filtered Trading/Activity/Settings evidence, not new top-level destinations.

**First viewport:** page title and observation time; compact “Needs attention” summary; provider equity/available/uPnL if observed; open exposure/working entry summary with source/completeness; short positions table. Recent signals/jobs and system detail follow. No giant safety dashboard above useful account information.
**Fields / priorities:** P1 provider equity, available balance and uPnL in margin coin; P1 position symbol/side, provider quantity and ownership, protection exception; P2 ledger recorded intents/fills/open-position count explicitly historical/persisted; P2 recent signal receipt/classification/dispatch outcome and job state/time. Keep provider and ledger counts separate.
**Actions:** primary New trade → UX05 when capability permits, otherwise explanation; secondary View positions, Account & performance, Activity; contextual Inspect blocker → exact recovery/protection/service detail. No account-wide emergency close/cancel or “make healthy” shortcut.
**Detail/review:** card click preserves scope and appropriate filters. Only contextual supported actions enter AF on their owning page; Overview does not duplicate full mutation forms or host command catalog.
**Contracts:** B1 `GET /api/v1/overview`; B2 `_read('overview')` includes provider snapshots, ledger summary, latches, services/effective config/clock/stream, recent signals/dispatches and job counts. F2 overview panels; D1 §3.1; D2 §2.
**Prerequisite O1:** `admission_verdict` is currently null; do not fabricate “ready to trade”. Protection rollups, true waiting-entry counts and financial totals need exact existing evidence/complete scope before presentation. A missing metric is unavailable, not a client-estimated live KPI.

**States:** SP specialized: confirmed flat requires complete provider positions/orders evidence, not zero ledger rows; loading cards do not show zeros; stale provider balance stays last known; partial ledger/provider failures remain independently labeled; denied/unavailable panels do not make the entire account safe or unsafe by inference; error keeps unaffected panels.
**Responsive/keyboard:** stack summary then core values then short table; no mobile carousel hiding a blocker. Card links have meaningful names; evidence disclosure is keyboard reachable without traversing all raw fields.

**Critical scenarios:**
- **UX01-S1:** complete empty provider inventory with active latch shows no observed exposure and the active blocker independently.
- **UX01-S2:** provider account refresh fails while ledger/SSE succeed; last-known values retain age and no readiness/net-account claim appears.

**Acceptance / edge checklist:**
- [ ] First viewport answers account/exposure/attention questions without raw objects or repeated safety panels.
- [ ] Partial, unknown and complete-zero fixtures remain distinct; every count/value has source, scope and unit.

## UX02 — Positions and position detail

**Goal / phase:** inspect actual open exposure and manage only the exact supported owned residual; R4.
**Entry/navigation:** Trading landing `/positions`; detail `/positions/{id}`. Secondary links Orders, Protection; primary New trade. Preserve position identity/epoch and return filters; never route by symbol alone.

**First viewport:** dense positions table with scope/observation age and ownership filter; no chart prerequisite. P1 columns: contract, LONG/SHORT, provider quantity, bot-owned residual, entry/mark price, uPnL in margin coin, protection state/freshness. P2 leverage `x`, margin mode, liquidation price, ledger quantity, ownership category (`bot`, `account_baseline`, `external`, `unresolved`).
**Fields/filters:** use existing symbol/direction/ownership/protection_state/state filters. Historical closed-position browsing and unsupported drift/time-specific filters are not assumed from the current provider-backed list; retain any existing records and expose missing historical read as prerequisite P1.
**Actions:** primary inspect row; detail primary Close position only for eligible exact owned root; secondary partial close with quantity, change SL/TP, move SL to entry, cancel owned remainder, reconcile existing intent. Source TP1 management is a different workflow, not a synonym for manual partial close.
**Detail/review:** detail panel/full page shows provider vs owned vs ledger, root/epoch, native protection, entry legs/fills/reservation/snapshot, source/manual dispatch, close bindings and related jobs. AF review lists reduce-only quantity, pending entry cancellation/fill races, affected/excluded roots and fresh readback requirements. Full provider size is not automatically owned size.
**Contracts:** B1 `GET /api/v1/positions[/{id}]`; B2 `_positions`, ownership helpers; B3 `position.close`, `position.close_partial`, `entry.cancel_owned`, protection actions, `intent.reconcile`; D1 §3.4; D2 §§3/6; F2 positions.
**Prerequisite P1:** source-present provider list is not a complete closed-position history or complete provider-vs-ledger historical reconciliation. Add no invented history tabs or browser financial attribution.

**States:** SP specialized: “No open positions observed” only for complete provider observation; empty filtered list is not flat account; missing ownership blocks owned actions but preserves visibility; stale quantity/protection prohibits unsupported conclusions; partial plan read leaves coverage unknown; read/permission errors retain last-known authorized row; unsupported action explains capability reason.
**Responsive/keyboard:** table region can scroll; mobile row retains contract/side, quantity/unit, uPnL, ownership and critical protection status. Explicit Open position link, not clickable-row-only; action menu reachable by keyboard; review full-height on narrow screens.

**Critical scenarios:**
- **UX02-S1:** same symbol has baseline/external and bot evidence; only exact eligible root is reviewable and residual/quantity remain distinct.
- **UX02-S2:** partial close loses response; same operation receipt leads to UX09 reconciliation without a second close.

**Acceptance / edge checklist:**
- [ ] Exact root/epoch, decimal units, source distinction and retained external/baseline visibility survive filtering and narrow layout.
- [ ] Pending fills, version changes, unknown ownership and missing plan evidence cannot authorize stale/overbroad close or protection changes.

## UX03 — Orders, intents, fills and details

**Goal / phase:** understand waiting work versus provider effects and cancel/reconcile only exact owned entries; R4.
**Entry/navigation:** Trading > Orders at `/orders`; preserve `?view=orders`, `?view=fills`, `/orders/{id}`, `/orders/fills/{id}`. Fills remains discoverable, not relegated to diagnostics. Provider orders/plan observations are distinct sections using existing payload, not presumed independent tabs/endpoints.

**First viewport:** orders/fills view switch; concise working/ambiguous filter; table. Orders P1 contract/side, ENTRY/protective/reduce-only role, native intent state and provider-terminal separately, requested/filled/residual quantities, order type/limit price and update time. P2 client/provider ID, root/child/dispatch, reservation, cancellation claimant/claim time.
**Fills table:** contract/side, executed quantity/unit, price/quote unit, realized PnL and fee with fee currency, fill UTC/display time; secondary provider fill ID, client order binding, source/ownership. Missing fee never zero. Current regular-order snapshot and plan-order snapshot must not be silently combined as identical inventory.
**Actions:** primary Open order/fill; exact eligible entry Cancel; secondary Cancel owned remaining entries (preview enumerates every ENTRY ID), reconcile existing intent, open source/position/protection/job. Protective cancellation/reduce-only cancellation and symbol-wide cancel-all are not generic entry cancellation.
**Detail/review:** requested vs provider ack/fill/remaining; linked provider orders, plan orders, fills, entry reservation, close binding, baseline and reconciliation. AF cancel names exact IDs, versions, exclusions and races. A claimed/ambiguous cancel offers readback, not fresh cancel POST/replacement entry.
**Contracts:** B1 `GET /api/v1/orders[/{id}]`, `/fills[/{id}]`; B2 `_table` provider observations, `_detail` orders/fills; B3 `order.cancel_exact`, `entry.cancel_owned`, `intent.reconcile`; D1 §3.5; D2 §§3/6; F3 filters.
**Prerequisite OR1:** do not promise all-provider history, arbitrary exchange filters or new server-side role/history views beyond B2 allowlists. Distinguish source-present snapshots from inventory completeness; N1/N2 apply to alternate list/detail paths.

**States:** SP specialized: empty intents/fills is not no provider working orders; loading table has stable columns; stale order observation preserves terminal uncertainty; partial fill/plan source flags affected sections; missing exact version/ownership disables cancellation; error/permission/unavailable keep known record IDs without claiming hidden provider results.
**Responsive/keyboard:** sticky contract/role where practical; mobile preserves role/state/quantity and an explicit details link. Selection count lists exact targets; no selection of unloaded rows or hover-only cancellation.

**Critical scenarios:**
- **UX03-S1:** owned split ENTRY legs coexist with SL/TP/manual orders; remainder preview lists only eligible exact ENTRY legs.
- **UX03-S2:** timeout after cancellation claim exposes reconciliation_pending and partial fills, never “all cancelled” from queue acceptance.

**Acceptance / edge checklist:**
- [ ] Orders, intents, regular/plan observations and fills remain separately intelligible; provider_terminal is not inferred from intent state.
- [ ] Protected/unowned rows, null fees, cancel/fill races, unknown readbacks and invalid cursor have safe explainable states.

## UX04 — Protection and protection detail

**Goal / phase:** establish what is actually enforced and diagnose/repair supported owned protection; R4.
**Entry/navigation:** Trading > Protection `/protection`; `/protection/{id}` shares exact position/root identity with UX02 and returns to selected exception filters.

**First viewport:** exception-focused matrix, not an overall “protected” percentage. P1 contract/side, actual provider quantity, owned residual, native SL/TP proof/reason, missing SL/TP and observation freshness; P2 native plan IDs/triggers/coverage quantity, capability enum, fallback registration/enforcement/epoch, stream/worker state.
**Fields/filters:** symbol/direction/ownership/native_state/fallback_state/stream_state/missing_sl/missing_tp from F3/B2. Show trigger price units, coverage quantity units, liquidation price and observed mark age when present. Registration, configured price and worker liveness are not enforcement proof.
**Actions:** primary Diagnose/readback (`protection.observe`); eligible contextual Repair native, Set SL, Set TP, Move SL to entry; advanced fallback register/update/disable only if advertised. Disable fallback is Tier 3 and unavailable to sole shared owner; retain explanation.
**Detail/review:** separate native facts, persisted capability and fallback/stream evidence. AF includes exact root/epoch/environment, current vs proposed level/quantity, retained TP/SL identities and independent gates. Automatic first TP covers full actual position; TP1_BOOKED source half-close is separate. Provider `43011` means failed/degraded, not proof of unsupported capability.
**Contracts:** B1 `GET /api/v1/protection[/{id}]`; B2 `_positions` protection proof/capability/fallback composition; B3 `protection.*` schema catalog; D1 §3.6; D2 §3; F2 protection columns.
**Prerequisite PR1:** coverage ratios or liquidation-distance metrics need exact compatible source fields; no approximate browser percentage, invented safe distance or optimistic proof. Missing native plan evidence stays missing even when fallback registration exists.

**States:** SP specialized: complete empty positions differs from failed plan read; loading native/fallback independently; stale mark/stream/proof cannot appear enforced-fresh; partial native read is unknown; capability unavailable/permission failure exposes precise reason and retains diagnostics; repair acceptance shows job, not restored coverage.
**Responsive/keyboard:** mobile retains exception reason ahead of secondary IDs; evidence sections are independently labeled. Action labels include intended change; keyboard focus returns to the exact row after closing diagnosis/review.

**Critical scenarios:**
- **UX04-S1:** fallback row exists while stream/mark evidence is stale and native read fails; UI never says protected.
- **UX04-S2:** eligible SL change preview preserves retained TP identity; epoch/version change invalidates prior review.

**Acceptance / edge checklist:**
- [ ] Native proof, persisted capability, fallback registration and actual enforcement remain four distinguishable concepts.
- [ ] Missing TP/SL, 43011, mismatched quantity, stale stream and unavailable Tier 3 action are explicit and navigable.

## UX05 — New manual trade

**Goal / phase:** enter a deliberate manual order through the existing canonical admission/ownership path; R4.
**Entry/navigation:** Trading primary New trade → `/trading`; preserve old Trading desk deep link. No separate exchange/account selector without actual selectable scope. Cancel returns to Trading without submitting.

**First viewport:** “New trade — manual” plus account/LIVE, dedicated form and concise supported/readiness explanation. Fields: symbol/contract, LONG/SHORT, MARKET or LIMIT, entry price required for LIMIT, stop-loss price, take-profit price, optional server-supported margin and leverage. Financial units appear on every input; effective pinned margin/leverage policy is explanatory, not an override.
**Priorities:** P1 required order fields and first blocking reason; P2 observed contract precision/minimum/notional and quote time if present; P2 server-normalized quantity/notional/margin/route and liquidation/SL checks in preview. No synthetic ticker, browser sizing engine, fake chart or unbacked projected profit.
**Actions:** primary Review trade (read-only preview), secondary Clear/cancel; after AF, Follow job and exact entry legs. No “force”, ignore pending orders, duplicate source dispatch or one-click live submit.
**Detail/review:** manual provenance remains explicit on job/dispatch/position. Server preview owns sizing/admission/reservation/legs. Source-only recency and 25% market/75% LIMIT split must not silently govern manual orders. Original source signal cannot be rewritten or freshness renewed to make manual submission look automated.
**Contracts:** F1 currently maps `/trading` to a positions read plus preselected generic `trade.open`; B3 `trade.open` schema in `console/trading.py`; B1 `/actions`, `/previews`, `/jobs`; D1 §3.3 historical constraints and D3 current Trading desk semantics.
**Prerequisite TR1:** source action presence is not proof it is currently executable; use advertised schema and authoritative preview. No dedicated quote/contract-search endpoint is assumed. Existing overview contract snapshot may supply validated metadata; any missing bounded quote/contract data is unavailable, not a frontend-created market feed. Schema range is not permission to relax effective isolated/leverage/LIVE pins.

**States:** SP specialized: initial loading waits for scope/capability; empty symbol is input guidance, not empty market; stale/missing contract/quote blocks unsupported claims; partial sizing stays incomplete; validation errors attach to fields; permission/gate/unsupported reason remains visible with read-only inspection; ambiguous enqueue enters same-operation recovery, not a blank new form.
**Responsive/keyboard:** one-column form on narrow screen; review separates current inputs from normalized server values; keyboard error summary links to fields. Numeric inputs accept canonical decimal strings without rounding/preset floats; editing invalidates preview/consent.

**Critical scenarios:**
- **UX05-S1:** manual LIMIT review shows exact scope, price/SL/TP and normalized quantity; editing price requires new preview/consent.
- **UX05-S2:** gate refusal or lost enqueue response cannot produce duplicate trade, fabricated readiness or a source-freshness change.

**Acceptance / edge checklist:**
- [ ] Dedicated manual form replaces command dropdown while preserving canonical schema, preview, consent and ownership provenance.
- [ ] Unsupported contract, step/minimum failure, unavailable quote, policy pins and unknown operation outcome have safe field/action states.

## UX06 — Signals inbox

**Goal / phase:** find a source receipt and understand its classification/dispatch result without treating a message as a trade; R5.
**Entry/navigation:** Signals `/signals`; each row opens immutable receipt/revision UX07. Keep channel/message IDs separate from opaque console resource ID.

**First viewport:** practical filter row and dense inbox. P1 received time, source/channel, readable sanitized text excerpt, symbol/direction if persisted, classification, dispatch outcomes and freshness; P2 message ID, revision hash, media indicator, terminal reason. No invented confidence score or “AI accuracy” metric.
**Fields/filters:** source/channel/message, intake/state, symbol/direction, revision, dispatch_state, terminal_reason, has_media, classification, bounded from/to/search/sort. Collapse advanced filters, preserve URL state and server cursor; do not request unsupported client-defined predicates.
**Actions:** primary Open trace; secondary Clear filters/Refresh; contextual Annotate selected revision and Reanalyze analysis-only lead into UX07/AF. Existing `report.export` can export bounded signals JSON/CSV; its schema does not accept arbitrary trace/filter selectors, so do not promise exact filtered exports.
**Detail/review:** excerpt never replaces original source. Multi-venue dispatches, if returned, remain independently identified, but no new exchange switcher is introduced. Reanalysis cannot trade historical messages, replace canonical history or reset received time.
**Contracts:** B1 `GET /api/v1/signals`; B2 `_table`, `_expression` classification/dispatch filters; B4 `signal.annotate`, `signal.reanalyze`, `report.export`; D1 §3.2; D2 §§2/4; D3 Signals & trace.
**Prerequisite SI1:** absent persisted classification/explanation is unknown; no client inference from words, media or missing dispatch. Exact trace export and complete historical explanation require an existing contract before being offered.

**States:** SP specialized: complete no-matching receipts offers filter reset, not “no trading activity”; loading preserves filter focus; stale inbox age differs from original message expiry; partial dispatch relation stays partial; failed/permission-denied receipt/media lookup does not expose raw source; reanalysis unavailable explains reason.
**Responsive/keyboard:** mobile keeps receipt/source/excerpt/outcome, collapses IDs; no auto-loading media or row hover dependency. Search/filter controls and pagination work keyboard-only and do not erase focus on refresh.

**Critical scenarios:**
- **UX06-S1:** edited Telegram message has multiple immutable revisions; inbox opens the selected receipt, not silently newest text.
- **UX06-S2:** media-only/unknown-classification receipt remains inspectable and does not become an inferred valid entry.

**Acceptance / edge checklist:**
- [ ] Receipt time, classification, dispatch state and freshness are distinct; server filters and bounded cursor pagination survive return navigation.
- [ ] Long/untrusted text, media markers, empty filters and missing explanation never fabricate signal/trading facts.

## UX07 — Signal and dispatch trace

**Goal / phase:** explain exactly what was received and what downstream work is evidenced; R5.
**Entry/navigation:** `/signals/{id}`, `/signals/dispatches/{id}`. Breadcrumb to Signals with valid return filters, explicit revision selector and related dispatch/management/order/job links. Apply N1/N5 rather than claim every existing generated path is valid.

**First viewport:** source/channel, original received UTC/display time, selected immutable revision and truthful outcome summary; sanitized original text/media beside concise canonical setup when present. Timeline follows below, not a giant raw JSON block.
**Fields/sections:** revision hash/message ID; original escaped text preserving line breaks; authenticated media MIME, bytes and opaque ID; persisted analysis runs/annotations; canonical symbol/direction/entry/SL/TP levels; per-dispatch native state/reason, route, transition times, reservation/snapshot and exact child intent/fill refs. Manual dispatch shows manual_entry provenance rather than a fake Telegram source.
**Actions:** primary Inspect next unresolved step; secondary switch revision, open safe original-source link if available, view full authenticated media; contextual Annotate and Analysis-only reanalyze with exact message ID/revision. No replay dispatch, edit original, refresh signal age or “trade this old signal” action.
**Detail/review:** immutable original remains visible separately from annotations/new analysis. Timeline explicitly distinguishes received → analysis → canonical → admission/dispatch → intents → ack/fills → protection/management → notification; missing sections are “not recorded/not observed”, not invented events. AF for annotation/reanalysis states no trading effect; exports only current supported bounded report kinds.
**Contracts:** B1 `GET /api/v1/signals/{id}`, `/dispatches/{id}`, `/signals/{id}/media/{media_id}`; B2 `_detail` revisions/canonical/dispatch/analysis/annotations and media allowlist; B4 annotation/reanalysis; F2 `DomainDetail`; D1 §3.2.
**Prerequisite ST1:** current linked queries do not guarantee a complete end-to-end chronological narrative or analyzer rationale. Compose only recorded relationships; unavailable steps and unsupported exact-trace export remain explicit.

**States:** SP specialized: missing canonical signal is not analysis failure by inference; loading selected revision clears incompatible prior details; stale downstream evidence never changes original receipt; partial relationship reads label gaps; media failure has text fallback and safe retry; denied/unavailable media/source reveals no path/credential; removed resource keeps safe breadcrumb.
**Responsive/keyboard:** stacked source then outcome/timeline; image fits width with authenticated full-size link and meaningful caption, not image-derived instructions. Revision selector and disclosures have labels; large IDs/text wrap; focus remains on chosen revision on update.

**Critical scenarios:**
- **UX07-S1:** source text contains HTML/script-like content and media lookup fails; text stays escaped, no host path leaks, timeline remains usable.
- **UX07-S2:** analysis-only reanalysis of old revision records a separate result/annotation without changing receipt, canonical history or dispatch admission.

**Acceptance / edge checklist:**
- [ ] Original text/media/revision, separate analysis/notes and manual-vs-source provenance are retained end to end.
- [ ] Missing trace steps, safe external links, denied media and revision switching never assert invented execution or freshness.

## UX08 — Activity, audit and management

**Goal / phase:** find operational work and explain historical actions, including obscure source-management outcomes; R5.
**Entry/navigation:** Activity `/audit`; preserve jobs/approvals/audit `?view=` tabs. Secondary Management `/management[/{id}]` and Notifications UX10; job detail UX09. Existing approvals/audit detail bindings require N1/N2, not invented new routes.

**First viewport:** Jobs/Approvals/Audit/Management subnavigation, bounded time/search filters and selected table. Jobs P1 action, state/phase, created time, actor/channel, reconciliation need; P2 tier, request/resource/job IDs and effect claim. Approvals show job/digest, actor/approver, decision/time; sole-owner Tier 3 barrier remains explicit.
**Audit fields:** action/outcome, actor/channel, exact resource, job/request IDs and UTC/display time. Keep denied/failed outcomes searchable within authorization; safe evidence details must not expose credentials or inaccessible resource existence.
**Management fields:** source/revision, original instruction, action enum, symbol, state/reason, claim owner/time, cancellation and terminal readback, post-cancel position, close/SL refs and notification. Preserve raw action names with plain-language explanation, not a generic “executed”.
**Actions:** primary Open job/record; secondary bounded export JSON/CSV for supported jobs/audit report kinds; Management contextual reconcile existing request or dedicated manual management form if advertised. Preserve `management.apply` with `CLOSE`, `TP1_BOOKED`, `TP_BOOKED`, `SL_TO_ENTRY`; do not manufacture source instructions.
**Detail/review:** CLOSE cancels owned remaining ENTRY then closes fresh full owned size/readback; TP1_BOOKED cancels first, confirms lot-safe half reduction, then requests remaining-entry SL; TP_BOOKED cancels only; SL_TO_ENTRY does not imply waiting-entry cancel. Show cancelled-flat, entries-cancelled, reconciled, reconciliation-pending and failed accurately. AF manual actions bind exact scope/targets; source records stay immutable.
**Contracts:** B1 `GET /api/v1/jobs`, `/approvals`, `/audit`, `/management[/{id}]`, `/exports`; B2 `_detail` management and table filters; B3 management actions; B4 `report.export`; D1 §§3.7/3.13; D2 §4.
**Prerequisite AC1:** export schema supports report kind/format/limit, not arbitrary current filters; approval detail routing and unified mixed-event timeline are frontend/read-composition prerequisites. Retain native records if common chronology is not evidenced.

**States:** SP specialized: no jobs != no provider activity; loading one tab cannot show prior tab as current; stale work state/time stays last known; partial management cancellation/close remains partial; denied audit stays scoped; unsupported export/management action has explanation; errors retain known claim boundary.
**Responsive/keyboard:** compact table with filters progressively disclosed; mobile keeps action/outcome/time and reconciliation flag. Tabs/links reflect actual routing semantics; exact record links and per-row menus work without hover.

**Critical scenarios:**
- **UX08-S1:** TP_BOOKED outcome is entries-cancelled without a close, while TP1_BOOKED partial failure shows reduction and unconfirmed SL separately.
- **UX08-S2:** sole shared owner views Tier 3 approval requirement but cannot self-approve or convert an approval reference into second-person authority.

**Acceptance / edge checklist:**
- [ ] All legacy audit tabs, management source/manual actions and exact terminal meanings remain discoverable.
- [ ] Export limits/redaction, denied reads, partial effects and unsupported detail routes are explicit; no generic retry or hidden broad cancel.

## UX09 — Job detail, review, approval and reconciliation

**Goal / phase:** inspect exact approved work and resolve ambiguity without replaying effects; foundation R2, complete R5/R7.
**Entry/navigation:** `/audit/jobs/{id}` from every submitted operation, Activity and same-operation recovery. Return breadcrumb retains safe originating page/filter state without secrets/confirmation values in URL.

**First viewport:** action and exact job ID, native state/phase, requested scope/actor/time, prominent “accepted is not completed” or reconciliation need as applicable; one safe next action. Below: reviewed effects/exclusions, normalized arguments, target versions, preview digest/expiry, policy/config/adapter versions and authorization facts.
**Tables/timeline:** ordered durable steps: enqueue, approval/consent, worker claim/checkpoint, effect claim/POST boundary, acknowledgment/readback, partial/final result and notifications/host verification. Preserve sequence/time/source, claimed state, cancellation request and exact domain refs; absent steps remain unobserved.
**Actions:** primary Inspect/readback/reconcile where permitted; secondary Refresh, eligible cancel unclaimed work, reject/approve only when actual policy permits, reopen exact review and authorized export download for report.export success. No universal retry job, undo accepted provider effect or abandon reconciliation action.
**Review flow:** AF rendered as plain-language sections, full effect/exclusion evidence accessible. Retain backend exact phrase, approval reference and digest-bound session consent without new password/factor. Tier 3 approval unavailable for shared owner. Changing anything invalidates prior consent; dismissal is not cancellation.
**Contracts:** B1 `GET /api/v1/jobs/{id}`, `/jobs/{id}/steps`, `/previews/{id}`, `/jobs/by-idempotency/{key}`; `POST /jobs/{id}/{approve|reject|cancel|reconcile}`, `/auth/consent`; F1 `JobControls`, `ActionPanel`; B3 authorization; D1 §§6–8, D3 preview flow.
**Prerequisite JB1:** current steps/readback are evidence, not proof every adapter emits every ideal stage. Render supported facts; follow related domain/host records for action-specific success. Download only actual authorized artifact route, not a browser-made report.

**States:** SP specialized: loading exact job never displays another job; unknown receipt stays recoverable; stale job state has last update time; partial success lists targets/effects separately; permission/not-found does not leak existence; readback unavailable leaves reconciliation_pending; preview expired/version changed cannot enqueue new work under old consent.
**Responsive/keyboard:** review dialog full-height on small screens; sticky action area must not conceal evidence or errors. Exact digest/targets selectable; focus trap/return and announced queue/claim state; confirmations never rely on color or phrase placeholder alone.

**Critical scenarios:**
- **UX09-S1:** lost enqueue response/reload recovers original idempotency receipt and follows existing claimed operation without duplicate economic effect.
- **UX09-S2:** cancellation races with claim; UI reports actual state and continued readback instead of “cancelled” from browser abort.

**Acceptance / edge checklist:**
- [ ] Immutable review, exact authorization fields, job acceptance/effect/readback and partial outcomes remain distinct.
- [ ] Expiry, stale versions, disconnect, session revocation and unknown receipts cannot create new operation keys as financial retry.

## UX10 — Notifications and report schedule

**Goal / phase:** distinguish trading-event delivery from actual trading outcomes and manage supported report delivery; R5.
**Entry/navigation:** Activity > Notifications `/notifications[/{id}]`; integration/source/secret links go to UX11, not a second token form. Report schedule is discoverable here even if current top-level generic table hides it.

**First viewport:** delivery exceptions and outbox table; separate report-schedule summary with observed interval/timer freshness. P1 event kind, relay state, attempts, created/sent/failed time, next attempt; P2 dedup key, lease, source/intent/job and destination summary when available. No synthetic delivery rate/uptime percentage.
**Detail fields:** sanitized rendered content and current attempts/lease/error facts from the outbox record; provider acceptance vs DB acknowledgment only where recorded; exact outbox identity and linked actual trading result when available. A full token-fenced attempt timeline is a proposed composition subject to NT1, not an existing detail-read claim. Schedule shows requested/observed interval separately; default six-hour history is not a claim it is currently effective.
**Actions:** primary Inspect failure; secondary Send configured test, Send health report, Configure report interval using allowed `1/2/3/6/12/24` hours; contextual Requeue eligible exact notification. AF identifies configured recipient/content and duplicate-delivery risk; explicit `duplicate_risk_accepted` required by schema. Retry never replays trade.
**Detail/review:** active lease/unknown provider acceptance is not simple failure; preserve original dedup/attempt audit. No arbitrary recipient URL, token input, quiet-hours/routing editor without actual schema support. Schedule acceptance is not observed timer application or proof of successful report delivery.
**Contracts:** B1 `GET /api/v1/notifications[/{id}]`; B2 `_table` `schedule` snapshot and `_record` derived relay state/current outbox facts; B4 `notification.test`, `.send_health`, `.configure_schedule`, `.requeue`; D1 §3.12; D3 notifications.
**Prerequisite NT1:** B2 does not provide a dedicated enriched notification attempt-history branch in `_detail`. A full attempt timeline, actual destination, linked trade result, acceptance/ack granularity and timer next/last-run fields require confirmed existing payload/host evidence; absent fields are unavailable. D1 quiet-hours/routing prose is not a current mutation schema. No new scheduling service or attempt-history endpoint is authorized.

**States:** SP specialized: no outbox rows is not no periodic reports; schedule loading/unavailable independent of table; stale lease/delivery evidence does not confirm failure; partial attempts keep unknown acceptance; denied test/retry explains gate; errors preserve duplicate warning and current operation receipt.
**Responsive/keyboard:** mobile keeps event/state/attempt/next retry and detail link; schedule form is labeled in hours, not an unlabeled switch. Rendered message is escaped; retry review reachable without hover and returns focus to outbox row.

**Critical scenarios:**
- **UX10-S1:** sent notification links a failed/partial trade outcome without presenting notification sent as trade succeeded.
- **UX10-S2:** accepted-but-unacknowledged relay requires explicit duplicate-risk review; changing interval shows requested vs observed timer independently.

**Acceptance / edge checklist:**
- [ ] Outbox, direct health reports, test delivery, eligible retry and schedule configuration are all retained and separated.
- [ ] Lease, duplicate risk, unavailable schedule/recipient and partial attempts remain truthful; no token readback or arbitrary routing scope.

## UX11 — Configuration and integrations

**Goal / phase:** understand effective settings and prepare supported versioned changes without exposing secrets; R6.
**Entry/navigation:** Settings `/config`; preserve effective/history/drafts tabs and named nested paths; Integrations is a frontend composition of existing source/credential configuration, not a new backend settings API.

**First viewport:** sections General/runtime settings, Sources, Integrations, History/drafts; effective vs requested status and pinned-policy explanation. Field table: human label, observed effective value, requested value/revision, source/consumer/time, apply/restart requirement and drift only where evidenced. No wall of environment variables.
**Editable fields:** dispatcher poll seconds `1–300`, monitor poll seconds `1–30`, notification retry seconds `10–300`, max attempts `1–20`, allowlisted analyzer channel IDs and intake channels. Source configuration separates live analyzer IDs from intake channels; keep IDs as exact values, not “trusted source” inference.
**Integrations:** observed Bitget/Telegram/source connection/configuration evidence, required credential kinds/reference state if returned. Stage replacement BITGET_API_KEY/SECRET/PASSPHRASE or TG_BOT_TOKEN through existing write-only form; memory-only input, opaque stage ID/kind/expiry result, no masked dummy value, secret echo or token testing through arbitrary endpoint.
**Actions:** primary Save draft, secondary field diff/history, then separately reviewed Apply if available; source.configure dedicated form; Stage replacement secret separate from apply. AF shows base version, diff/consumers/policy/restart impact. LIVE/LIVE/1 and risk/freshness/clock/ownership/protection policy are read-only constraints, not generic editable switches.
**Detail/review:** saving draft != runtime application; host effective != desired DB revision. View immutable draft/history and related job. Secret blank means no change; never send credentials in reason/general settings or persist them as normal drafts. Integration details use references, not credential disclosure.
**Contracts:** B1 `GET /api/v1/config`, `/config/history`, `/config/drafts[/{id}]`, `POST /secret-stages`; B2 requested/effective/host composition; B4 `CONFIG_FIELDS`, `config.save_draft`, `config.apply`, `source.configure`; F3 `SecretStage`; D1 §3.11, D3 configuration.
**Prerequisite CF1:** no dedicated integration health endpoint or complete per-field source/consumer/apply metadata is assumed. Use existing observed payload/catalog; missing connection proof stays unknown. History-detail route limitations N2 remain; do not expose unsupported arbitrary key edits.

**States:** SP specialized: empty history != default effective config; initial loading does not prefill invented defaults; stale/partial host/consumer observations stay separate; version conflict keeps safe nonsecret draft and requires rereview; secret-stage failure never echoes secret; permission/unavailable apply preserves read access and explains gate.
**Responsive/keyboard:** narrow layouts stack effective/requested labels; field-level errors and diff anchors support keyboard. Secret input has clear label/clear control and no browser persistence; confirmation focus returns to initiating draft/field.

**Critical scenarios:**
- **UX11-S1:** saved desired draft differs from observed consumer configuration; UI never labels it applied until authoritative readback.
- **UX11-S2:** replacement secret stages to opaque ID and separately reviewed apply; no value appears in URL, history, errors or clipboard artifact.

**Acceptance / edge checklist:**
- [ ] Source/intake configuration, typed runtime settings, draft/history/base versions and write-only integration staging remain accessible.
- [ ] Policy pins, version conflicts, expired stage, missing effective evidence and denied apply cannot become arbitrary config/credential scope.

## UX12 — Safety, recovery and baselines

**Goal / phase:** diagnose independent containment and inspect exact approved recovery prerequisites; R6.
**Entry/navigation:** Settings > Advanced > Safety & recovery `/recovery`; preserve recovery/baselines tabs, `/recovery/baselines/{id}` and links from actionable global summary. Not a permanent repeated dashboard on all pages.

**First viewport:** active global/Bitget/protection-stream rows with exact reason/latched/updated time, scoped admission pause separately, next safe diagnostic step. Independent clock, stream, config/provider/ledger evidence follows. Show observation time; no fresh production-health assertion from this document.
**Fields/sections:** unresolved intents/ownership, reservations, provider positions/regular/plan orders, current reconciliation, acknowledgment actor/time, historical cutoff/baseline ID/approval/uncertainty policy and exact excluded refs. Preserve `unresolved-history-not-verified-closure` meaning; retirement is not verified closure or PnL repair.
**Actions:** primary Inspect evidence; supported Acknowledge incident (exact reason, no clear), Pause entries, Resume entries only if permitted, Contain admission, read-only baseline retirement preview and recovery proof. Advanced apply/release remains visible with unavailable distinct-approver reason; no boolean “kill switch off”.
**Detail/review:** recovery wizard binds audited baseline/account UID/exact latch rows/reasons, stopped-consumer attestation, complete authenticated flat inventory, ready ledger, bounded clock proof and observe-only V2 stream proof where required. Existing proof/digest is not current permission. AF apply reruns canonical proof/locks; sole-owner Tier 3 baseline apply/latch release cannot proceed.
**Contracts:** B1 `GET /api/v1/recovery`, `/recovery/baselines[/{id}]`; B2 `_read('recovery')` explicitly `proof_status='unknown'`; B4 `incident.acknowledge`, `admission.pause/resume`, `safety.contain`, `baseline.preview_retire/apply_retire`, `latch.prove/release_approved`; D1 §3.8; D3 incident handling.
**Prerequisite RC1:** current inventory is not approved flat-recovery proof. Render proof from actual job/runner results if available; do not promote read-model snapshot to release authorization. No account-wide flatten/adopt/mark-filled action exists in this redesign.

**States:** SP specialized: no returned latch row != ready to trade; loading and stale clock show age independently; partial provider/ledger inventory blocks flat claim; changed reason invalidates acknowledgment/review; read/permission/unavailable proof retains incident; nonflat/unknown refuses flat-only recovery and preserves protection.
**Responsive/keyboard:** critical reason remains first on mobile; long exact reasons/IDs wrap and copy; wizard has labeled steps/back without side effects; no color-only incident or unreachable advanced prerequisites.

**Critical scenarios:**
- **UX12-S1:** acknowledge current clock reason records acknowledgment without clearing latch; reason changes invalidate old preview.
- **UX12-S2:** seemingly flat list has incomplete plan inventory; proof is unknown/refused and sole owner cannot bypass release approval.

**Acceptance / edge checklist:**
- [ ] Acknowledge, entry pause, containment, proof, release and baseline retirement retain separate semantics/authority.
- [ ] Independent clock/stream evidence, unknown inventory, historical exclusions and unavailable Tier 3 remain visible without a broad recovery shortcut.

## UX13 — Services and releases

**Goal / phase:** inspect actual service/runtime identity and understand supported allowlisted maintenance impact; R6.
**Entry/navigation:** Settings > Advanced > Services & releases `/services`; preserve services/releases views and `/services/{id}`. Existing release-generated detail links require N1 mapping; no new route availability claim.

**First viewport:** service table with service name, observed running/health, worker progress age, observed release/image identity, read time and actionable exception. Requested release SHA and actual container/source digest remain distinct. Dependency explanation is secondary, not a fabricated live topology graph.
**Detail fields:** allowlisted service ID, healthcheck verdict, worker attestation, image/source digest, configured/effective gate evidence, last progress/restart/log observations where returned. Logs are bounded/redacted with time/cursor; resource units bytes/%/seconds only when actual values exist. Releases list immutable ID/digests/schema compatibility/approval metadata.
**Actions:** primary Inspect service/logs; secondary Prepare maintenance with exact consumers and no-replay; contextual Start/stop/restart if capability permits. Deploy/rollback select registered immutable release and maintenance plan, remain Tier 3 unavailable to shared owner. No shell, SSH, filename, arbitrary image tag/Git ref or Compose fragment.
**Detail/review:** AF dependency/consumer/exposure/protection continuity, fresh flat evidence for disruptive operations, exact image/source/Compose/schema digests and rollback limitations. Running container != ready worker; host Git SHA != actual container source. Application rollback != database restore; host result may succeed while trading remains contained.
**Contracts:** B1 `GET /api/v1/services[/{id}]`, `/services/{id}/logs`, `/releases[/{id}]`; B2 services/service_logs snapshots, releases catalog; B4 `maintenance.prepare`, `service.start/stop/restart`, `release.deploy/rollback`; D1 §3.10; D3 maintenance and [OPERATIONS](OPERATIONS.md#console-host-operations-boundary).
**Prerequisite SV1:** no guaranteed restart history/resource telemetry/all-service readiness from generic snapshots; show only returned evidence. Detailed release approval/schema metadata must come from catalog record, not guessed from tag. Preserve unknowns rather than add a second monitoring backend.

**States:** SP specialized: no host snapshot != no services; loading logs independent of service status; stale progress/runtime digest marked last known; partial topology/readiness cannot become all-services healthy; denied logs remain redacted; unavailable runner/maintenance/second approver explains action barrier and keeps reads.
**Responsive/keyboard:** mobile keeps service/state/progress/digest summary; long digests selectable/wrapped with exact copy; log region labeled and scrollable, no automatic scroll stealing; service/release actions have explicit names.

**Critical scenarios:**
- **UX13-S1:** requested SHA differs from observed image/source digest; page reports drift, not successful deployment from host checkout.
- **UX13-S2:** restart requires allowlisted consumers/maintenance and fresh flat evidence; release deploy cannot be self-approved by shared owner.

**Acceptance / edge checklist:**
- [ ] Services, bounded logs, releases, maintenance prepare and all supported host actions remain discoverable without raw commands.
- [ ] Host ambiguity, unavailable telemetry, nonflat inventory, digest mismatch and rollback/schema constraints stay explicit.

## UX14 — Backups and maintenance

**Goal / phase:** inspect catalog/integrity and prepare exact safe backup operations without assuming restorable runtime state; R6.
**Entry/navigation:** Settings > Advanced > Backups & maintenance `/backups[/{id}]`; maintenance plans link to UX09 and UX13. Keep create/verify/delete/restore support visible even when currently gated.

**First viewport:** backup catalog and Create backup primary action if allowed. P1 opaque ID, created time, bytes/human-readable size, integrity/verified time, schema/release; P2 checksum, encryption/key reference, retention/hold/deletion and restore-drill evidence if available. Successful dump != proven restore.
**Detail fields:** exact artifact checksum, catalog state, verified readability, known provider-mutation relation, release/schema compatibility and linked maintenance/job/runner evidence. Never expose host paths, key material or database content as normal evidence.
**Actions:** Create backup; Verify exact backup; Prepare maintenance; contextual Delete and Restore with explicit consequences/prerequisites. Restore remains Tier 3 unavailable under shared owner; no downloaded DB button, automatic restore fallback or old-schema downgrade. Deletion respects hold/retention rules and actual capability result.
**Detail/review:** AF for verify/delete/restore binds backup ID/checksum/maintenance plan. Restore review shows destructive target scope, stopped consumers, protection/flat requirements, schema compatibility, no-replay and post-restore provider reconciliation. Changed trading evidence after backup refuses restore; reconcile forward instead. Ambiguous host receipt is reobserved, not replayed after DB restore.
**Contracts:** B1 `GET /api/v1/backups[/{id}]`; B2 backup catalog `_table/_detail`; B4 `backup.create/verify/delete/restore`, `maintenance.prepare`; D1 §3.14 historical design; D3 backups/maintenance; OPERATIONS host boundary.
**Prerequisite BK1:** catalog does not guarantee all retention/hold/drill/last-mutation facts exist. Show unavailable and use preview refusal; never derive restore eligibility from checksum alone. No new artifact download or backend restore capability is authorized.

**States:** SP specialized: successful empty catalog means no matching catalog entries, not absence of filesystem backups; loading verify is not verified; stale evidence cannot establish current restore safety; partial integrity/runner evidence stays incomplete; permission/unsupported delete/restore explains exact barrier; error retains catalog/job identity and receipt recovery.
**Responsive/keyboard:** mobile keeps ID/time/verification/schema; checksum disclosure/copy accessible; destructive review has clear target and danger text, no ambiguous icon-only delete. Keyboard can inspect full plan before any queue button.

**Critical scenarios:**
- **UX14-S1:** created backup lacks restore-drill evidence; UI distinguishes created, integrity verified and restorable claim.
- **UX14-S2:** later provider claim/fill or unknown inventory makes restore unavailable; sole-owner second-approver barrier cannot be bypassed.

**Acceptance / edge checklist:**
- [ ] Create/catalog/verify/delete/restore and maintenance/no-replay prerequisites are retained with exact IDs/checksum, not paths.
- [ ] Retention/hold unknowns, failed verification, schema mismatch, newer trading evidence and ambiguous host receipts never offer unsafe restore/replay.

## UX15 — Access and sessions

**Goal / phase:** inspect sole-owner scope/session policy and revoke owned sessions; R6.
**Entry/navigation:** Settings > Advanced > Access & sessions `/access`; preserve access/principals/sessions/policy tabs and named nested lists. Identity menu provides Sign out plus preferences, not a second authentication owner.

**First viewport:** “Shared-password owner” policy summary; account/environment/roles as server-reported scope; owned sessions table and current-session marker where ID matches. Secondary policy details, not invitation/enrollment controls or unused database identity concepts.
**Fields:** owner display name, enabled/scope/roles; session ID, authenticated time, last seen, absolute expiry, revoked time; policy idle/absolute/consent durations in seconds/minutes and version. Existing consent/step-up metadata is historical authorization evidence, not proof of a separate factor or a user-account system.
**Actions:** primary inspect owned sessions; contextual Revoke owned session with exact identity/time review; secondary Sign out current session. Preserve server restriction to current owner's sessions; no role grant, invitations, OTP/passkey setup, password/verifier readback, credential reset or external identity bridge setup.
**Detail/review:** revoking current session returns UX00 and clears protected content/SSE; other-session revoke refreshes actual session state. Financial approval remains exact session consent elsewhere; shared password never establishes two people. Present simple confirmation for revocation without inventing a job path where API is direct.
**Contracts:** B1 `GET /api/v1/access`, `/access/principals`, `/access/sessions`, `/access/policy`; `POST /access/sessions/{id}/revoke`, `/auth/logout`; B2 access composition; F1 password policy banner; D1 §3.15 and D3 private access.
**Prerequisite AS1:** no arbitrary session/principal detail GET is registered; use authorized list data for panel. Last-seen timestamp is not proof a different human/device is active; IP/device identity should not be invented. Current-session marker comes from actual session ID.

**States:** SP specialized: no returned sessions under scoped read is not proof of zero global sessions; loading owner/session separately; stale last-seen remains timestamped; partial policy/session reads do not infer privilege changes; denied other-owner revoke reveals no existence; revoke failure stays unsucceeded; expired session redirects without replay.
**Responsive/keyboard:** session cards retain current marker/time/expiry and exact revoke label; policy disclosure keyboard accessible; focus and announcement make current-session revocation/sign-out transition understandable.

**Critical scenarios:**
- **UX15-S1:** current-session revoke closes authorized UI/event stream and returns to password form without creating an invitation/factor flow.
- **UX15-S2:** attempted out-of-owner session action is denied; UI never treats shared owner role as a second independent approver.

**Acceptance / edge checklist:**
- [ ] All original access tabs map to useful owner/scope/session/policy views; obsolete enrollment vocabulary is not promoted.
- [ ] Direct revoke semantics, scope denial, session expiry and missing metadata remain truthful with no credential disclosure.

## UX16 — Account and performance

**Goal / phase:** understand authenticated balances and recorded trading economics without overstating account-wide performance; R3.
**Entry/navigation:** Overview > Account & performance `/account`; retain account/pnl/reservations/admission-snapshots views and named nested paths. Baseline/external reconciliation links UX12; positions/fills link UX02/UX03.

**First viewport:** provider balance observation card and scope/time; explicit separation of Current account from Recorded ledger results. P1 provider equity/available/uPnL in margin coin; secondary locked/isolated/cross balances only if returned. No return %, win rate, balance chart, daily profit or account-wide net from incomplete records.
**Ledger table:** per fee currency `fill_count`, `gross_realized_pnl`, `fees`, first/last fill times, coverage/exclusions. Gross realized PnL and fees remain separate; B2 currently returns `net_pnl=null`, `complete_account_history=false`. Funding, conversion and account-wide economics remain unknown unless an existing authoritative read supplies them; no double-subtraction/browser conversion.
**Other tables:** reservations exact client/root/dispatch/symbol, reserved amount in margin coin, lifecycle/time and snapshot binding; admission captures account/margin coin/equity/available/captured time; exclusions baseline/cutoff/uncertainty policy/record IDs, labeled unresolved history rather than verified closure. Current provider GET does not renew admission capture.
**Actions:** primary Inspect account/source evidence; secondary navigate Ledger results, Reservations, Admission snapshots, Excluded history; contextual open fill/order/baseline. No transfer, withdrawal, wallet action or blanket account-position adoption. Current export schema lacks account/PnL kind; do not add a fake download action or promise a new endpoint.
**Detail/review:** each value links to correct provenance/coverage. Filter recorded economics with existing symbol/client order/ownership/fee currency/source/intent/time fields; no financial mutation form on this page. Any future supported export uses authorized existing job contract only after coverage/schema is available.
**Contracts:** B1 `GET /api/v1/account`, `/account/pnl`, `/account/reservations`, `/account/admission-snapshots`; B2 `_read('account')`, `_pnl`; F2 account panels; D1 §3.9; D2 §2; D3 Account & PnL.
**Prerequisite AP1:** source-current ledger aggregation is explicitly incomplete; account-wide net, funding/fee valuation, performance history/chart and account export are not available by inference. Detailed reservation/snapshot panel uses returned list data; N2 governs unsupported independent detail reads.

**States:** SP specialized: complete provider zero balance is distinct from unknown balance; empty ledger groups means no recorded matching fills, not zero lifetime PnL; stale balances retain original time; partial fee currencies/exclusion failures keep economics incomplete; read/permission errors affect only relevant pane; unavailable performance metrics are explained, not replaced with zero.
**Responsive/keyboard:** mobile orders provider current values before recorded results; currency labels remain attached to amounts and multi-currency groups never merge. Tables expose labeled horizontal regions; filter/errors, snapshot and exclusion links work keyboard-only.

**Critical scenarios:**
- **UX16-S1:** provider equity changes while immutable admission capture remains older; both times/source meanings stay visible and no fresh-admission claim appears.
- **UX16-S2:** mixed/unknown fee currencies and baseline exclusions keep net/account-wide performance unknown while showing recorded gross/fees accurately.

**Acceptance / edge checklist:**
- [ ] All four legacy account views, provider identity/positions and baseline exclusions remain discoverable with actual units/coverage.
- [ ] No invented performance chart/net/export; null amounts, stale captures, incomplete ledger and absent funding/conversion remain explicit.

## Implementation handoff and evidence boundary

Use each page's two scenario IDs as critical executable-flow references in [EXECUTION](BOT-CONSOLE-UX-EXECUTION.md); SP/IK and the page's two acceptance boxes are the edge matrix, not evidence of completion. R0 must inventory actual payload examples and all N/O/P/OR/PR/TR/SI/ST/AC/JB/NT/CF/RC/SV/BK/AS/AP prerequisites before implementation claims; missing backend data remains outside this frontend redesign unless separately authorized.

Page-to-phase ownership: R2 UX00/shared UX09 primitives; R3 UX01/UX16; R4 UX02–UX05; R5 UX06–UX10 and completed UX09; R6 UX11–UX15; R7 integrated route/state/keyboard/responsive/authorization/readback evidence. R1 establishes compositions before those implementation slices. Progress tracking lives only in [PROGRESS](BOT-CONSOLE-UX-PROGRESS.md); no page here is accepted by writing the specification.

Future verification must distinguish deterministic frontend fixtures/contract tests, browser navigation/accessibility evidence and real human comprehension review. Financial/host side effects, provider probes, credential handling tests against real values and production latch observation require their own explicit authorization; none were exercised for this document.
