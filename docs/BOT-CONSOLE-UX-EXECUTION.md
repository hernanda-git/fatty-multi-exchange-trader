# Bot console UX redesign — executable delivery plan

**Scope:** FRONTEND-ONLY redesign planning. This document specifies future work; it does not implement, verify, deploy or authorize it. Every R0–R7 implementation package starts **NOT STARTED**. The written plan is **SPECIFIED**, not implementation acceptance. Historical P0–P6 receipts do not accept any R-phase.

Read together: [master plan](BOT-CONSOLE-UX-MASTER-PLAN.md), [page specifications UX00–UX16](BOT-CONSOLE-UX-PAGES.md), [progress and evidence register](BOT-CONSOLE-UX-PROGRESS.md). The master owns product decisions; pages own page-level behavior; this file owns delivery sequencing and promotion evidence; the progress register owns current package state. If they disagree, record a decision before implementing the conflicting behavior.

## 1. Outcome and immutable boundaries

The result should feel like an excellent, simple trading console: know what account is being observed, understand what matters now, find the next useful action, and inspect the detail only when needed. Success is not a prettier generic JSON viewer or a larger compliance checklist. A compact actionable status summary replaces repeated safety panels; plain-language tables, focused details and dedicated forms replace raw objects and the normal-use command dropdown. Real evidence remains available through progressive disclosure.

- Five primary destinations only: Overview `/overview`; Trading `/positions`; Signals `/signals`; Activity `/audit`; Settings `/config`. Orders/protection and New trade belong under Trading; Account under Overview; management/notifications under Activity; recovery/services/backups/access under Settings advanced.
- Preserve existing routes and deep-link entry points. Navigation labels may change without gratuitously renaming URLs. `/trading` remains the New trade entry point, not a second competing Trading destination.
- Reuse the current API and existing backend authority. No invented endpoint, metric, exchange selector, chart, emergency action or unsupported operation. Missing read data is a named prerequisite, not synthetic account data or a client-side financial estimate.
- One shared-password owner session; no usernames, invitations, passkeys, OTP or repeated password entry for financial review. Retain exact server-required confirmation phrases, digest-bound consent, resource/policy checks, permissions, capability gates, idempotency and recovery. The sole shared owner cannot act as a distinct Tier 3 approver.
- Unknown is not zero; stale is not fresh; partial is not complete; an accepted job is not a completed provider effect. Provider, ledger, immutable admission evidence and baseline exclusions remain distinct.
- Do not weaken ownership, risk, clock, protection, source freshness, admission, mutation, approval or LIVE-only boundaries. The clock latch is an independent incident. This plan supplies no fresh production observation and no authorization to clear it.
- No frontend verification trade, provider mutation, latch change, credential rotation, production session revocation, host operation or deployment. Isolated local command scenarios are credential-free and explicitly non-LIVE. Production follow-up, if separately authorized, is read-only for this redesign's verification scope.

Authoritative existing constraints: [agent workflow](../AGENTS.md), [trading rules](TRADING-RULES.md), [LIVE policy](PRODUCTION-LIVE-POLICY.md), [operations](OPERATIONS.md). Historical documents may describe superseded login methods; current password-only source and current operations guidance take precedence for this redesign.

## 2. Source map and discovery obligations

The following are observed source locations, not a mandate to retain today's monolith. Re-read affected sections at package start because the repository can change.

| Source | Current responsibility / bounded discovery target |
|---|---|
| `frontend/src/main.tsx` | 15-screen registry; custom history routing; `Link`; generic `Value`; `Evidence`; password `Auth`; `SchemaField`; `ReviewDialog`; `ActionPanel`; `JobControls`; `RelatedRead`; session/data/SSE lifecycle; app chrome. Discover by symbol rather than trusting old line numbers. |
| `frontend/src/domain.tsx` | Current domain column definitions, `DomainTable`, `DomainDetail`, `LatchBanner`; linked signal/order/management evidence. Existing paths are evidence of frontend expectations, not proof every API field exists. |
| `frontend/src/forms.tsx` | `serverFilters`, delayed filter application, UTC cutoffs, owner-only `SecretStage`. Preserve supported filter semantics and staging-not-activation distinction. |
| `frontend/src/api.ts` | `/api/v1` client; same-origin credentials; CSRF; `Envelope`, `Capability`, `ApiError`; password login; digest-bound session consent. |
| `frontend/src/style.css` | Current layout, themes, tables, responsive states and focus styling; scope styles before replacing them. |
| `frontend/package.json`, `frontend/package-lock.json`, `frontend/tsconfig.json`, `frontend/vite.config.ts` | Actual installed stack and commands. React 19, TypeScript and Vite; no declared router, state framework, UI kit or frontend test runner. Dev API proxy targets `http://127.0.0.1:18081`. |
| `src/fatty_trader/console/reads.py`, `api.py` | Read-only contract discovery: actual DTO fields, collections, detail reads, authenticated media, filters, cursors, SSE, static fallback and error semantics. Backend edits are outside this plan. |
| `src/fatty_trader/console/commands.py`, `auth.py`, `store.py`, `trading.py`, `operations.py` | Read-only discovery of exact action schemas, session/consent, durable receipts, ownership and host-operation limitations. Do not redesign these through frontend convenience. |
| `tests/unit/test_console_auth.py`, `test_console_reads.py`, `test_console_store_jobs.py`, `test_console_trading.py`, `test_console_operations.py`; `tests/e2e/test_console_jobs_postgres.py` | Existing backend regression evidence and fixture patterns; not browser coverage. Tests remain subject to their isolated environment prerequisites. |
| `.github/workflows/ci.yml`, `docs/OPERATIONS.md` | Existing verification commands and isolated PostgreSQL/credential-free expectations. CI is not a frontend interaction test runner. |

### 2.1 Route compatibility is work, not an assumption

Observed current routing recognizes the first segment in the 15-screen registry; an unknown first segment selects Overview. Remaining segments are decoded and joined into an ID. Nested dispatch/fill/job paths and selected nested collection paths are resolved separately. Collection tabs also use `?view=`, filters and `cursor` are in the query, and `Link` conditionally carries the current query for same-section links. This leaves a discovery obligation for invalid paths, encoded delimiters, tab/query aliases, detail-to-list return, filter leakage, cursor history and logged-out deep-link return. Do not claim these are already correct.

`console/api.py` has an index fallback when static output exists and excludes API/health prefixes; that source observation does not prove any reverse proxy, deployed asset path or reload behavior. R0-01 must build a route ledger with canonical destination, existing alias, query contract, parent navigation, current observation, expected behavior and future browser receipt.

Required route families to inventory from the current registry/callers: `/overview`, `/positions`, `/orders`, `/orders/fills`, `/protection`, `/trading`, `/signals`, `/signals/dispatches`, `/management`, `/audit`, `/audit/jobs`, `/notifications`, `/config`, `/config/history`, `/config/drafts`, `/recovery`, `/recovery/baselines`, `/services`, `/services/releases` or current release-tab representation, `/backups`, `/access`, `/access/principals`, `/access/sessions`, `/access/policy`, `/account`, `/account/pnl`, `/account/reservations`, `/account/admission-snapshots`, supported `?view=` forms and actual resource links. This is an inventory list, not a promise that every apparent nested path currently resolves. For example, verify how release-tab links are represented before preserving an imagined route.

### 2.2 Boring, bounded decomposition

Retain React/TypeScript/Vite. Do not add a state library, router, charting package or UI framework merely to split a file. Use the existing history mechanism unless a demonstrated compatibility requirement cannot be met simply; any new dependency requires a recorded need, alternatives and owner decision.

The R2 integration owner may propose small modules under `frontend/src/` for app shell/session/navigation, reusable view primitives, domain adapters, command review/recovery and page components. These are **proposed responsibilities**, not files that already exist. Select final filenames after R0 discovery and record ownership before creation. Keep `main.tsx` as the entry/composition boundary; keep `api.ts` the sole transport/auth boundary; migrate every affected caller at cutover. Avoid a second transport, competing routing parser, duplicate global event stream or compatibility shim layer.

A domain view adapter maps evidenced DTO fields to explicit display models: value, unit, source, observation time, completeness, freshness and missing reason. It must not turn null into zero, add together unrelated scopes, do money calculations through binary floating-point, manufacture denominators or infer native protection from a generic healthy badge. Preserve canonical decimal strings and existing server-produced financial values. Adapters provide readable views, not a new financial engine. Keep raw redacted evidence in secondary disclosure where useful, never the primary happy-path layout.

Separate purpose-built page forms from the shared command lifecycle. A New trade form or exact position action supplies its approved action/arguments/target to one review/recovery implementation; it does not copy enqueue logic. Keep page state local unless navigation/session ownership requires shared state. Use an available language server for symbol definitions/references and supported refactors; when none is configured, record that limitation and inspect source with repository tools rather than inventing tooling availability.

## 3. Package state, evidence and promotion rules

Track each package in the [progress register](BOT-CONSOLE-UX-PROGRESS.md) with its owner and these independent axes:

| Axis | Allowed states | Meaning |
|---|---|---|
| Work state (central tracker) | NOT STARTED / ACTIVE / BLOCKED / DONE | DONE means the assigned bounded work and its stated gate are finished; it does not imply any unclaimed evidence or human/production acceptance. Implementation and verification facts live in the evidence fields. |
| Documentation/design | NOT STARTED / DRAFT / SPECIFIED / REVIEWED | A written design is not working UI. REVIEWED names the reviewer and decisions, not a browser pass. |
| Automated verification | NOT RUN / RUNNING / PASSED / FAILED / BLOCKED | Record exact command, revision and environment. Build success proves compilation/bundling only. |
| Browser verification | NOT RUN / PARTIAL / PASSED / FAILED / BLOCKED | PASSED requires the package's real rendered scenarios with actual assertions; screenshots alone are insufficient. |
| Human acceptance | NOT REVIEWED / CHANGES REQUESTED / ACCEPTED | Names reviewer and exercised criteria. Five-second comprehension is human evidence only. |
| Production | NOT REQUESTED / NOT AUTHORIZED / AUTHORIZED / OBSERVED / REJECTED | Never derived from any local state. Authorization is separately scoped; observation does not prove financial effects. |

The central evidence vocabulary is **NONE / SPECIFIED / DESIGNED / IMPLEMENTED / VERIFIED / RELEASE READY / DEPLOYED / HUMAN ACCEPTED**. These are independently supported claims, **not an automatic ladder**. Map written contracts to SPECIFIED, reviewed visual/interaction designs to DESIGNED, integrated code to IMPLEMENTED, current required local automated/browser receipts to VERIFIED, a completed compatibility/readiness dossier to RELEASE READY, separately observed authorized rollout lineage to DEPLOYED, and actual named human acceptance to HUMAN ACCEPTED. The detailed axes above qualify those claims; they do not introduce competing central work states. A package may have DONE objective work and VERIFIED evidence while its human lane remains NOT REVIEWED; R7-02 itself remains pending until its actual human gate is met.

Use VERIFIED only when all required automated and browser receipts for the stated scope pass. Acceptance is a decision naming its scope (design, local objective behavior, human usability, or separately authorized production observation), never a single green tick. R0/R1 can pass planning/design gates while implementation/runtime remain NOT STARTED/NOT RUN. R2–R6 require integrated behavior and evidence; R7 readiness remains distinct from release permission. A phase is not complete because its document exists, a build passes, or one representative page looks right. Pending subjective human evidence does not prevent finishing all reachable objective implementation, verification and readiness preparation; report that lane pending and never self-approve it.

Every unchecked item below is future work. A package may be ACTIVE without marking any criterion passed. Preserve failed receipts with their resolution; do not rewrite history or carry old evidence forward after an affecting change.

## 4. Phase-by-phase executable packages

### R0 — Inventory and baseline

**Goal:** replace assumptions with a bounded map of existing UI, supported reads/actions and current pain points. **Dependencies / entry:** master/pages available as specifications; source and current workflow guidance readable. No implementation or production access needed. **Source boundary:** the source map above plus discovered callers; no source edits. **Phase output:** route/DTO/action/scenario ledgers attached to the UX progress record. **Exit:** each UX00–UX16 page has a source-backed data/action mapping or a named prerequisite; the owner can distinguish observed defects from proposed design. **Evidence:** symbol/path references and dated local baseline receipts if actually exercised; missing credentials/environment are recorded, never bypassed.

#### R0-01 — Source, route and API inventory
- [ ] Read current registry, `route`, `Link`, nested read selection, filters, API transport and static fallback; enumerate every existing entry/deep link and actual generated link caller.
- [ ] Map UX00–UX16 to read endpoints, field paths, provenance, units, cursor/filter behavior, media/export paths and unknown/empty/error responses using `reads.py`/`api.py` and current regressions.
- [ ] Identify query/tab aliases and reload/back/forward/login-return limitations without silently declaring them fixed; record remediation owner under R2-01.
**Output:** source/route/data ledger with exact references and unresolved contracts. **Package exit/evidence:** every registry screen and secondary/detail family accounted for; no proposed field is presented as already supported. A fresh local route observation, if performed, states environment and does not imply deployment.

#### R0-02 — Action and safety compatibility ledger
- [ ] Inventory action catalog/schema use and every supported current action by domain, including currently unavailable capability reasons and operations requiring a distinct approver.
- [ ] Trace preview → exact review → session consent → enqueue → durable job → reconciliation, including idempotency recovery, resource/policy versions, expiration, errors and retained storage namespace.
- [ ] Record unchanged protections, forbidden financial/host scopes and plain-language replacements for raw action names. Identify any action with no discoverable current page entry; assign a new page entry without inventing authority.
**Output:** action-to-page/target/review/result ledger and explicit forbidden-scope list. **Package exit/evidence:** each supported action retains a discoverable future entry or a documented unavailable explanation; source references show no password re-entry or Tier 3 self-approval shortcut is required/designed.

#### R0-03 — Scenario and evidence baseline
- [ ] Capture current task friction: locating exposure, recognizing stale data, reviewing an exact action, finding an ambiguous job, returning from a detail. Do not run production actions to obtain examples.
- [ ] Define isolated account/database/browser setup, data provenance and supported evidence tool; identify absent frontend test tooling and who may approve any addition.
- [ ] Seed the browser-scenario and visual matrices in sections 7–8 with baseline/not-run statuses, redaction rules, actual dataset prerequisites and responsible owners.
**Output:** baseline task notes and an executable scenario roster. **Package exit/evidence:** every planned failure state has a controllable isolated setup or a named blocker; no unavailable live exposure is replaced by fabricated LIVE data.

### R1 — Visual and interaction design

**Goal:** make common tasks obvious before moving implementation. **Dependencies / entry:** R0 ledgers complete; unresolved data/action boundaries visible. **Source boundary:** UX master/pages/progress and design artifacts only; source inspection allowed, no app changes. **Phase output:** reviewed layouts, interaction contracts and page/state annotations. **Exit:** all 17 page IDs, mobile behavior, financial review and failure states can be implemented without inventing missing product choices. **Evidence:** annotated designs and a named integration/design review against the written criteria; human feedback, if available, is recorded separately and is never implied. No browser/production acceptance claim.

#### R1-01 — Information architecture and page flows
- [ ] Specify the five primary destinations, secondary/advanced navigation, breadcrumbs, context-preserving details and existing route aliases from R0-01.
- [ ] Walk three primary questions through actual information: “What needs attention?”, “What exposure/orders do I have?”, “What happened to this action/signal?” Give each page a clear title, primary content and only relevant next actions.
- [ ] Map all supported actions from R0-02 to dedicated forms/contextual entries; keep unavailable capability reasons accessible without normal-use raw action selection.
**Output:** page-flow/navigation map and route handoff contract. **Package exit/evidence:** all UX00–UX16 reachable, deep-link treatment explicit, no sixth top-level destination or duplicated safety workspace.

#### R1-02 — Visual, responsive and accessible interaction design
- [ ] Specify neutral light/dark tokens, hierarchy, dense readable data tables, tabular amounts, real units and explicit timezones. First visit defaults to neutral light; honor persisted choice; retain system theme as an option. Proposed starting tokens: 14px base, 24px page headings, 12px noncritical metadata; spacing 4/8/12/16/24/32.
- [ ] Apply the width/theme/keyboard matrix in section 8 to shell, lists, details, forms and reviews. Desktop header contains account, LIVE/environment truth, compact status and identity only; move preference controls into their appropriate secondary location.
- [ ] Specify focus order/return, labelled controls, errors, loading, status changes, contrast and content reflow. No gradients, marketing copy, badge/pill overload or tiny critical metadata.
**Output:** annotated responsive component/page exemplars and measurable accessibility targets. **Package exit/evidence:** WCAG 2.2 AA criteria and 200% zoom/reflow behavior are testable; dense financial tables have an intentional small-screen alternative or explicitly labelled data-region overflow, not accidental document overflow.

#### R1-03 — Financial review and failure-state design
- [ ] Define task-specific review content: exact account/environment, action, target/ownership, quantities/units, server-produced fees/limits only when available, preconditions, consequences and unavailable reasons.
- [ ] Define preview invalidation, exact consent/phrase requirements, expired/rejected/conflict states and ambiguous-outcome recovery. Distinguish submitting, accepted, executing, partially succeeded, reconciliation required and proved completed states.
- [ ] Specify stale/unknown/partial views and missing economic evidence. Never add a second login or a reassuring success state while evidence is ambiguous.
**Output:** review/result state diagrams and copy for failures and unavailable actions. **Package exit/evidence:** each command lifecycle/failure state maps to actual server evidence or a named prerequisite; human design review can identify what was requested versus what is proved.

### R2 — Frontend foundation

**Goal:** implement shared structure once so later pages are coherent rather than independently improvised. **Dependencies / entry:** R0 inventory and reviewed R1 contracts; one integration owner and file ownership declared. **Source boundary:** `main.tsx`, `api.ts`, `domain.tsx`, `forms.tsx`, `style.css` and approved small frontend modules; backend read-only. **Phase output:** integrated shell, observation primitives, domain adapter contract and single command lifecycle. **Exit:** UX00 and the shared examples work in actual browser scenarios; later page integration has stable interfaces; no duplicated routing/transport/event/recovery authority. **Evidence:** integrated build plus actual shell/login/navigation/data-state/review browser receipts; permanent tests only for plausible regressions using an explicitly selected harness, not invented commands.

#### R2-01 — App shell, navigation and password session
- [ ] Extract shell/session/navigation responsibilities; replace 15 primary sidebar entries with the agreed five destinations and secondary navigation without changing existing route addresses.
- [ ] Implement UX00, password-only sign-in, session check/expiry/logout and deep-link return; preserve CSRF and current API events. Login credentials remain memory-only and cleared on submission.
- [ ] Implement responsive shell, compact actionable status, identity/preferences, theme persistence, skip link, route headings/focus and current account/environment truth. Do not label unknown scope LIVE.
- [ ] Resolve R0-01 compatibility defects through one route/query contract; migrate all link callers; preserve supported filters/cursors/tab aliases or document a decided correction and its browser evidence.
**Output:** app shell/navigation/session composition and route compatibility ledger with results. **Package exit/evidence:** actual browser invalid/valid isolated password, expired session, logout, direct URL reload, back/forward, nested detail, query/cursor and mobile navigation scenarios; no protected data retained visibly after expiry.

#### R2-02 — View primitives and domain observation adapters
- [ ] Implement consistent typography/spacing, buttons/forms, table/detail structures, empty/loading/error/status/evidence disclosures and semantic amount/time display.
- [ ] Define adapters per domain with explicit unknown, stale, incomplete and retained-last-read state; keep operation state independent from observation freshness and SSE connection state.
- [ ] Preserve cancellation/refresh lifecycle and server filters/cursors; prevent late previous-route/account results overwriting the current view. Preserve source and scope in all displayed observations.
**Output:** reusable frontend primitives and documented adapter interface with an integrated real page exemplar. **Package exit/evidence:** browser shows zero versus missing, complete empty versus unavailable collection, failed refresh retained data with unchanged observation time, partial response, disconnected SSE and route-race behavior; source financial values remain intact.

#### R2-03 — Command review and ambiguous-outcome recovery
- [ ] Establish one shared review/consent/enqueue implementation and a dedicated contextual exemplar; migrate normal-use catalog entries as their owning pages land in R3–R6. Preserve the complete R0-02 action/eligibility register throughout; do not require all domain forms to be finished inside the foundation phase. Delete obsolete catalog/renderer paths after their last caller migrates, with zero legacy fallback remaining at R7.
- [ ] Preserve preview digest, resource/policy versions, exact phrases/acknowledgments, server gates, consent, durable idempotency keys and recovery before any resubmission. Maintain or explicitly migrate the current `console.pending.<principal>.<account>.<environment>.<section>` storage contract without losing unresolved operations across release/rollback.
- [ ] Integrate the shared UX09 job/review shell with job identity, digest/target invalidation, refresh/reload recovery, conflict/expiry/auth failures and accessible focus. Never automatically enqueue because consent succeeded. R4 must consume and complete trading job/readback outcomes before trading acceptance; R5-03 completes the full activity/approval/reconciliation page rather than supplying a missing R4 result path.
**Output:** shared command lifecycle consumed by a bounded isolated page exemplar and a pending-operation compatibility note. **Package exit/evidence:** credential-free isolated browser exact-review flow, double click, response loss/reload recovery with the same operation ID, stale preview/target rejection, denied consent, Tier 3 self-approval unavailability and accepted-not-executed messaging. No live provider call is required or permitted.

### R3 — Overview and account

**Goal:** make account state and next attention comprehensible without conflating balances, performance and admission evidence. **Dependencies / entry:** R2 integrated contracts and R0 fields; R3-01/R3-02 may proceed independently after shared ownership is settled. **Source boundary:** overview/account page modules, their adapters, composed `domain.tsx` panels and scoped styles; API/backend contracts read-only. **Phase output:** UX01 and UX16. **Exit:** overview answers current status and links to the right work; account explains only supported financial figures with source/scope/freshness. **Evidence:** scenario B03/B04 and full required layout states; no financial headline invented to fill a design gap.

Known source constraints: `reads.py` currently returns `admission_verdict=None`, `net_pnl=None` and `complete_account_history=False`. Keep admission unknown unless actual independent evidence supports a narrower stated fact, and show scoped gross/fee components without inventing a complete account-wide net figure. Current source/action schemas may be ahead of the historical UI/API document; resolve against current API callers and source rather than implementing obsolete schema assumptions.

#### R3-01 — Overview
- [ ] Build a concise attention summary, supported account/exposure snapshot, meaningful recent activity and links into Trading/Activity/Settings; prioritize what an operator can act on.
- [ ] Replace duplicate safety banners with one compact actionable summary and discoverable exact evidence; distinguish latch, effective gates, clock, stream and connection state.
- [ ] Represent missing provider/service evidence plainly; avoid “all clear” from empty/missing data or LIVE configuration alone.
**Output:** UX01 implementation with source-backed summary mapping. **Package exit/evidence:** actual browser fresh/unknown/stale/partial attention scenarios and working contextual links; human reviewer can state what is known and where to investigate without reading raw objects.

#### R3-02 — Account and performance
- [ ] Implement UX16 account, supported PnL, reservations and admission-snapshot views under Overview secondary navigation while preserving entry paths.
- [ ] Keep provider balances/uPnL, ledger realized PnL/fees, baseline exclusions, currency and time period separate. Explain unavailable net/account-wide performance; do not make a chart or computed metric without real support.
- [ ] Preserve filter/export entry points only where R0 establishes actual support and normal command authorization; label incomplete economics and fee currencies.
**Output:** account/performance views and metric/source dictionary. **Package exit/evidence:** browser demonstrates missing/zero/negative values, multiple fee currency evidence where available in isolated data, incomplete period and stale provider versus fresh ledger distinction; no ledger figure is labelled account-wide net.

### R4 — Trading

**Goal:** make precise exposure management and New trade fast to understand, never faster by weakening review. **Dependencies / entry:** R2 review/recovery and shared UX09 job shell, R1 trading designs and the stable account/resource link contract; completed R3 pages are not a prerequisite for independent R4 implementation. **Source boundary:** trading-domain page/adapters/styles, dedicated frontend forms and the trading outcome portion of the shared job view; `trading.py`/`commands.py` discovery only. **Phase output:** UX02–UX05 plus UX09 trading job/readback core. **Exit:** positions/orders/fills/protection are readable and linked; supported exact actions and New trade use shared review and can follow actual accepted/pending/partial/reconciliation/proved outcomes; unavailable scopes are explained. R4 cannot pass with a dead job link or by deferring trading outcome UI to R5. **Evidence:** B05–B08, B11/B17 trading portions and responsive trading table/detail/review matrix. No real trade or live protection change is an acceptance prerequisite.

#### R4-01 — Positions and position detail
- [ ] Implement UX02 dense exposure table and detail with symbol/direction, ownership, actual quantities/prices/units, supported PnL and protection summary.
- [ ] Make exact supported position actions contextual; external/baseline/unknown ownership must not acquire bot-owned action eligibility through presentation.
- [ ] Preserve list context, exact record links and source/protection/order/activity drill-down; prevent selection surviving a changed resource/scope without revalidation.
**Output:** positions list/detail and contextual action integration. **Package exit/evidence:** browser owned/external/baseline/missing/stale isolated scenarios; correct row-to-detail/action target and review revalidation; no account-wide shortcut.

#### R4-02 — Orders, fills and protection
- [ ] Implement UX03 order/fill lists/details with requested, filled and residual distinction, stable client/provider identity and actual fee/economic evidence.
- [ ] Implement UX04 protection list/detail distinguishing native proof, missing SL/TP, fallback and stream observation; surface only supported exact repair/change entries.
- [ ] Retain trace/job/reconciliation links, exact ownership eligibility and pending/partial/unknown outcomes. Never present intent or relay acknowledgment as provider execution.
**Output:** orders/fills/protection workspace under Trading and preserved deep links. **Package exit/evidence:** browser working/partial/terminal/unavailable orders, incomplete fills, missing/unknown protection, native versus fallback and blocked repair states; local exact-target review only, no provider mutation.

#### R4-03 — New trade
- [ ] Build UX05 dedicated `/trading` form using actual `trade.open` schema and server constraints; retain supported MARKET/LIMIT distinctions, units, optional narrowing and manual provenance.
- [ ] Show only real supported controls; validate form usability without taking authority away from server sizing, limits, liquidation guard, ownership or admission checks.
- [ ] Make draft → preview → exact review → consent/confirmation → accepted job → reconciled outcome explicit. Keep draft changes from reusing a prior preview and block new operation creation while an earlier outcome is ambiguous.
- [ ] Finish and exercise the UX09 trading job/readback core for New trade and other R4 action results, consuming the R2-03 shell. Preserve accepted, executing, partial, ambiguous and proved economic outcomes; do not wait for the full R5 activity hub to make this path usable.
**Output:** New trade form and review/result journey. **Package exit/evidence:** credential-free isolated browser valid/invalid fields, stale/blocked admission, changed arguments, submit timeout, reload recovery and accepted-not-filled states. No real entry/fill or latch release.

### R5 — Signals and activity

**Goal:** explain why something happened and what remains unresolved, without requiring knowledge of internal field names. **Dependencies / entry:** R2 foundation and stable job/resource link contracts; R4 cross-domain links must work before final integration, but independent R5 implementation need not wait for every R4 page. **Source boundary:** signals/activity/jobs/notifications/management views, adapters and styles; authenticated media/download flows reuse current API. **Phase output:** UX06–UX10. **Exit:** immutable source-to-dispatch-to-effect investigation and durable command history are usable; management/notification messages cannot overclaim execution. **Evidence:** B09–B12 and no-loss cross-navigation/filter/trace scenarios.

#### R5-01 — Signals inbox and trace
- [ ] Implement UX06 concise inbox with relevant server filters and UX07 source/revision/dispatch trace using real original text/media and immutable revision links.
- [ ] Distinguish received/analyzed/classified/admitted/dispatched/reconciled states and unknown missing steps. Preserve provenance; never fabricate a source message for manual entry.
- [ ] Handle long text, missing media, authenticated media failures and links to positions/orders/jobs without losing list context.
**Output:** signals inbox and evidence-linked trace. **Package exit/evidence:** browser real isolated signal/revision/media and missing-step scenarios; dispatch/manual provenance differences; valid trace links and explicit unavailable media rather than broken unexplained surfaces.

#### R5-02 — Activity, management and notifications
- [ ] Implement UX08 Activity/audit with management secondary view and UX10 notifications; show concise descriptions, times, outcomes and correlation links from actual data.
- [ ] Preserve supported filters/pagination and entries for current annotation/acknowledgment/retry/export actions only where the R0 ledger proves support and permission.
- [ ] Separate source management detection/queueing, claimed effect, readback, notification relay acceptance and actual delivery acknowledgment; explain unknown/partial outcomes.
**Output:** Activity workspace and management/notification details. **Package exit/evidence:** browser filter/deep-link/return flows, honest management terminal distinctions and notification sent/failed/pending/unknown states; no external message send used for verification.

#### R5-03 — Jobs, approvals and reconciliation
- [ ] Implement UX09 task-readable job detail, steps/checkpoints/readback, exact reviewed scope/digest, approval state and reconciliation-required explanation.
- [ ] Preserve server-eligible approve/reject/cancel/reconcile/export affordances and state/version constraints; cancellation is not reversal of a claimed effect.
- [ ] Explain why distinct approval is unavailable to the sole shared owner and route ambiguous effects to existing recovery/readback rather than a new command.
**Output:** durable job/approval/reconciliation journey shared by domain results. **Package exit/evidence:** isolated browser queued/claimed/partial/failed/reconciliation-required/proved-complete scenarios, stale revision and self-approval refusal, same-operation recovery and authorized isolated export download if supported; acceptance never inferred from queue receipt.

### R6 — Settings and advanced operations

**Goal:** make occasional administrative work understandable without exposing a normal-use raw command console or broadening authority. **Dependencies / entry:** R2 shared command/job contract and actual operation schemas/read evidence from R0; independent read/form work may proceed before R5 finishes. **Source boundary:** config/integrations/recovery/services/backups/access frontend modules/forms/styles only; backend/host/runtime/credential files read-only and not modified. **Phase output:** UX11–UX15. **Exit:** R5 durable result/approval views are integrated; desired/effective/observed values and advanced-action eligibility are explicit; existing operations remain available only through unchanged authority. **Evidence:** B13–B16, advanced-state matrix and deliberate refusal scenarios; no production host/provider/secret mutations.

#### R6-01 — Configuration and integrations
- [ ] Implement UX11 readable effective configuration, requested/draft/history differences and supported source/integration forms; retain pinned read-only risk/gate/LIVE values.
- [ ] Preserve staged-secret semantics: memory-only input, opaque stage reference, expiry and separately authorized application. No credential visibility, persistence in drafts/logs or rotation for redesign testing.
- [ ] Show unobserved consumer/effective readback and partial apply as unresolved rather than current configuration success.
**Output:** configuration/integration workspace and bounded task forms. **Package exit/evidence:** isolated browser requested-versus-effective, conflict, expired stage and unknown readback scenarios; any staging scenario uses clearly fake non-provider test material in an isolated environment, never real credentials.

#### R6-02 — Safety, recovery and access
- [ ] Implement UX12 advanced safety/recovery evidence and supported proof/reconciliation entries, distinguishing independent latches, admission pause, gates, protection and clock evidence.
- [ ] Implement UX15 shared owner/session/policy observation and supported session controls; remove obsolete enrollment language from redesigned views without inventing multiple owners.
- [ ] Make missing fresh proof and unavailable distinct approvals understandable. Never add blanket resume, account-wide close/cancel, automatic latch clearing or UI risk overrides.
**Output:** recovery/access views and plain-language eligibility explanations. **Package exit/evidence:** browser active/unknown/stale proof, sole-owner restrictions, expired/revoked local session and independently gated recovery states; no production latch or session mutation.

#### R6-03 — Services, releases, backups and maintenance
- [ ] Implement UX13 services/releases and UX14 backups/maintenance under Settings advanced, using actual observed health/logs/digests/integrity/restore evidence.
- [ ] Build dedicated entries for supported allowlisted operations; explain approval/catalog/flat-proof prerequisites and distinguish request acceptance, host execution and observed readback.
- [ ] Preserve deploy/rollback/catalog/schema/backup boundaries and staging-not-restoration distinction; no arbitrary shell, path, URL, service, branch or image input outside current schemas.
**Output:** advanced operations views and scoped review summaries. **Package exit/evidence:** isolated browser unknown service/log failure, approved versus unavailable artifact, stale/unknown/nonflat refusal, encrypted backup integrity versus unproved restore, partial operation and readback missing states. No real service stop/deploy/rollback/restore is required or authorized.

### R7 — Integrated verification and release readiness

**Goal:** prove the new UI works as an integrated product and prepare a reversible, separately authorized frontend cutover. **Dependencies / entry:** R2–R6 integrated, workers finished, ownership conflicts resolved, all UX IDs mapped, R0/R1 decisions current; one integration owner controls checks. **Source boundary:** integrated frontend, approved permanent frontend regressions and UX docs/evidence; existing backend commands are run only in isolated authorized verification environments. No deployment/runtime changes. **Phase output:** local verification and human acceptance records plus release/rollback readiness dossier. **Exit:** every required scenario/state has a passing current receipt or an explicit release blocker; readiness never authorizes rollout. **Evidence:** exact commands, browser assertions, human observations and immutable artifact/compatibility records. Deferred mandatory evidence keeps the gate open.

#### R7-01 — Integrated regression and browser verification
- [ ] Run agreed checks once after integration using section 6; record failures honestly, fix the bounded cause, and invalidate/re-exercise affected evidence rather than running workers' overlapping checks.
- [ ] Exercise all UX00–UX16 rendered routes, secondary/details/forms, old aliases, direct reload/back/forward, supported queries, refresh/SSE/session and section 7 failure scenarios using the actual built app and isolated real API/database where required.
- [ ] Complete width/theme/keyboard/zoom/accessibility matrix; add permanent tests only for plausible regressions such as route/query state, unknown/empty truth, preview invalidation, wrong-target selection, amount fidelity or same-key recovery.
**Output:** integrated local receipts, defects and permanent regression coverage where justified. **Package exit/evidence:** build plus all required browser assertions pass on the integrated revision; no hidden synthetic LIVE dataset, skipped mandatory scenario or frontend unit test substituted for browser proof.

#### R7-02 — Human usability and accessibility acceptance
- [ ] Have the designated human reviewer attempt the primary tasks from normal start state without being coached through the layout; record confusion and requests for change.
- [ ] Run the five-second exercise as a target, not a claim: ask what account/environment is shown, what needs attention and where the next relevant task lives. Record answers/time, page/state, participant and limitations; do not infer universal comprehension from one participant.
- [ ] Review reading order, meaningful copy, financial ambiguity, dense-table use, keyboard/screen-reader flow and both themes; resolve or explicitly reject release-blocking friction.
**Output:** human acceptance record separate from automated accessibility/browser receipts. **Package exit/evidence:** named reviewer accepts scoped local UX, mandatory accessibility defects are resolved, and unperformed human testing remains NOT REVIEWED rather than passed by automation.

#### R7-03 — Frontend release and rollback readiness
- [ ] Prepare the compatibility and rollback dossier in section 9 for candidate and previous bundle, actual API/schema, auth sessions, pending operations, in-flight jobs, static asset paths/cache and source/artifact identity.
- [ ] Identify exact release owner, authorization boundary, supported deployment mechanism, read-only post-cutover checks, abort conditions and known-good rollback artifact without executing rollout.
- [ ] Reconcile every package, UX page, action/route ledger and evidence link in progress; describe remaining production observations as unrequested/unauthorized, not blockers to truthful local reporting or fabricated completion.
**Output:** reviewed frontend-only release/rollback readiness checklist and explicit go/no-go recommendation. **Package exit/evidence:** candidate can be considered for separately authorized rollout, old/new compatibility risks are resolved or are release blockers, and no backend safety scope or live runtime change is implied. Production acceptance remains separate and unclaimed.

### 4.1 Page ownership coverage

| UX IDs | Delivery phase/packages | Integrated acceptance |
|---|---|---|
| UX00 password login | R2-01 | R7-01 session/route browser proof; R7-02 human review |
| UX01 overview | R3-01 | R7-01 / R7-02 |
| UX02 positions/detail | R4-01 | R7-01 / R7-02 |
| UX03 orders/fills/detail; UX04 protection/detail | R4-02 | R7-01 / R7-02 |
| UX05 manual/New trade | R4-03 | R7-01 / R7-02; isolated only |
| UX06 inbox; UX07 signal/dispatch trace | R5-01 | R7-01 / R7-02 |
| UX08 activity/audit/management; UX10 notifications | R5-02 | R7-01 / R7-02 |
| UX09 job/approval/reconciliation | R2-03 shared review/job shell; R4-01/02/03 trading outcome/readback core; R5-03 full page/approval/activity coverage | R4 local trading outcomes required before trading gate; R7-01 / R7-02 integrated |
| UX11 configuration/integrations | R6-01 | R7-01 / R7-02 |
| UX12 safety/recovery; UX15 access/sessions | R6-02 full pages; R2-01 basic UX15 sign-out/session boundary | R7-01 / R7-02 |
| UX13 services/releases; UX14 backups/maintenance | R6-03 | R7-01 / R7-02 |
| All UX00–UX16 shell/adapters/review | R2-01/02/03 shared contracts; R0/R1 specify all | R7-03 compatibility/readiness only, not deployment |

## 5. Safe scheduling, ownership and handoff

### 5.1 Parallelization and shared ownership

R0 discovery slices may run independently once page/route/action/scenario ledger interfaces are agreed. R1 visual and flow work may overlap after R0 contracts are available; financial flow decisions depend on R0-02. Do not begin a page implementation on an unresolved financial data contract.

R2 is the shared prerequisite, not three uncontrolled monolith edits. Assign one integration owner for `main.tsx`, `api.ts`, shared shell/navigation/session/SSE, global styles/tokens and final integration. View-primitives/adapters and command-review owners may work in separately owned new modules after interfaces are frozen. Only the integration owner edits shared wiring. If a proposed module requires a shared edit, send the exact interface requirement to that owner rather than editing concurrently.

After R2 lands, substantive independent page slices may run concurrently: overview/account; positions/orders-protection/New trade; signals/activity/jobs; configuration/recovery-access/services-backups. Keep one owner per actual file and split modules before parallel work if necessary. Domain owners own their adapter and scoped styles, not the shared transport or global tokens. Cross-domain resource links use the agreed route builder/contract. Jobs and command recovery are shared interfaces, not copied per page. R5-03 must stabilize before advanced operation result flows finish. R7 verification is serialized under the integration owner after all relevant edits land; workers do not run builds/tests/linters/formatters mid-flight.

No “merge” means automatically promoting status. A handoff can be integrated while its checks remain NOT RUN. Never overwrite unexpected user changes, delete another owner's files, run destructive Git operations or commit/deploy without separately assigned authority.

### 5.2 Work-package handoff template

Use this as a progress/evidence record, not a new unrequested documentation file:

```text
Package ID/title:
Owner / integration owner:
Base source identity and current affected-file ownership:
Scope: UX IDs, routes, actions, exact allowed files; explicit exclusions:
Entry dependencies and unresolved decisions:
Read evidence: symbols/paths, API fields/actions and existing regressions:
Shared interfaces consumed/provided:
Changed behavior and migrated callers:
Artifacts and source identity:
Checklist items actually completed:
Verification performed: exact command/scenario, environment, result, receipt IDs:
Verification NOT performed and why:
Browser evidence: routes, data provenance, dimensions/theme, assertions, artifacts:
Human acceptance: reviewer, tasks, result or NOT REVIEWED:
Compatibility: old URLs/query/storage, session/consent, pending operations/jobs:
Known defects/risks/blockers, owner and next required evidence:
Next eligible package and exact entry gate:
Production state: NOT REQUESTED/NOT AUTHORIZED unless separately evidenced:
```

### 5.3 Resumption protocol

1. Read master/pages/execution/progress and latest decisions; identify the next eligible NOT STARTED/BLOCKED package by dependencies, not document order alone.
2. Read its last handoff, failed receipts, current ownership and affected sources. Compare current file/source identity to the handoff without undoing other work. Treat user-reported failures as facts; do not rerun merely to confirm them.
3. Confirm the scope and API contracts still hold. If evidence predates an affecting change, mark it stale and name what must be re-exercised. Historical production observations are never refreshed by rereading them.
4. Claim only the bounded files and update ACTIVE status in the central register. A small missing fact obtainable from repository/tools is research, not a reason to ask the user or stop.
   Use the fixed design defaults and existing source conventions to resolve routine typography, layout, component naming and bounded implementation choices without repeated user confirmations. Record the choice when helpful. Ask only for materially different scope/security/destructive tradeoffs or information genuinely unavailable from repository/tools. Human comprehension and subjective approval are separate pending evidence, not permission to stop all reachable objective work.
5. Implement the exact checklist, migrating all affected callers; workers return changes/evidence prerequisites without mid-flight verification. Integration owner exercises checks after all relevant slices land.
6. Attach a complete handoff and evidence, update separate axes, and move to the next eligible package. Do not mark a phase complete when any mandatory state or failure scenario is blocked, partial or unexercised.

### 5.4 Change, risk, blocker and stop/merge rules

Record a change as: decision ID, requested behavior, source evidence, impacted packages/UX IDs/routes/actions, chosen bounded change, rejected alternatives, safety/compatibility effect, owner, approval need and affected receipts to invalidate. Do not silently solve a frontend gap by adding backend endpoints, relaxing gates, manufacturing data or deleting a supported action.

A blocker record names the missing prerequisite, evidence of attempts, exact impact, safe work still possible, owner and unblock condition. Distinguish product decision, missing DTO/read, isolated environment, inaccessible browser/human reviewer, source conflict and production authorization. Missing live trades are not a blocker: use truthful empty/unknown production views and isolated non-LIVE acceptance scenarios.

**Stop the affected slice and escalate** on suspected secret exposure, wrong account/scope, changed financial authority, uncertain claimed effect, any proposed new POST to recover ambiguity, unresolved Tier 3 identity, unsupported backend prerequisite, conflicting file ownership or incompatible pending-operation storage. Preserve evidence, redact artifacts and avoid further mutation. Continue unrelated safe work where contracts permit. Never clear latches, widen limits, disable a check or hide an error for green status.

**Merge/integrate only when** allowed files and callers are complete, interfaces match, no stubs/shims/fake fallback remain, relevant documentation and justified permanent regressions are updated, known failures are disclosed, and verification scope is assigned. Integration does not require a worker to run checks. **Promote objective evidence only when** the integration owner has its current receipts; **promote HUMAN ACCEPTED only when** the designated human has actually reviewed it. Missing mandatory objective evidence yields BLOCKED/PARTIAL. Missing human evidence leaves that lane pending while all reachable objective work continues; it never becomes self-approval or a reason to abandon the implementation.

## 6. Actual repository commands and their limits

These commands are grounded in the inspected manifests/guidance. They are **instructions for future authorized implementation verification**, not commands exercised by this planning task. Re-read manifests at execution time. Use each stated working directory; do not invent `npm test`, `npm run lint`, `npm run typecheck`, Vitest or Playwright commands.

| Working directory | Command | What it establishes / prerequisite |
|---|---|---|
| `frontend/` | `npm ci` | Lockfile-based dependency setup when needed; not a check or runtime proof. |
| `frontend/` | `npm run build` | Existing script is `tsc -b && vite build`; TypeScript plus production bundle only. It does not prove routes, rendering, keyboard behavior or financial truth. |
| `frontend/` | `npm run dev` | Existing Vite local dev server bound to `127.0.0.1`; `/api` proxy expects a separately prepared local API on port 18081. Starting Vite alone is not integrated acceptance. |
| `frontend/` | `npm run preview` | Existing preview script bound to `127.0.0.1`; serves built frontend. The inspected proxy is in `server`, not an established integrated preview API setup. Discover/prepare same-origin API serving before claiming authenticated preview coverage. |
| repository root | `uv sync --frozen` | Existing CI dependency preparation. No live credentials in verification environment. |
| repository root | `uv run pytest -q` | Existing operations full-suite command; PostgreSQL and platform prerequisites apply. A skipped required integration test is not acceptance. |
| repository root | `uv run pytest -q --junitxml=artifacts/pytest.xml` | Existing CI full-suite receipt form. CI uses isolated PostgreSQL 16 and `FATTY_TEST_POSTGRES_DSN`; use an isolated target only and record its identity, never production. |
| repository root | `uv run ruff check src tests scripts` | Existing CI Python lint; not frontend lint. |
| repository root | `uv run ruff format --check src tests scripts` | Existing CI Python format check; not a CSS/TS formatter. |
| repository root | `uv run mypy src` | Existing strict Python typecheck; not TypeScript/browser verification. |

Focused console tests may be selected from the discovered `tests/unit/test_console_*.py` files and `tests/e2e/test_console_jobs_postgres.py` after reading their fixture requirements. Record the exact selection actually run; do not imply a focused pass equals the full suite. For a pure frontend change, the integration owner records whether the existing backend full suite is required by project release policy, run unchanged as compatibility assurance, or intentionally not exercised. Never change backend tests merely to make frontend-only scope appear covered.

There is currently no frontend unit/browser test script or declared browser automation dependency in `frontend/package.json`, and `pyproject.toml` does not declare a browser runner. R0-03/R7-01 must name the available browser tool or obtain a scoped tooling decision before adding a permanent harness. Browser smoke of the **actual rendered surface is mandatory**, regardless of whether permanent automated frontend tests are added. A bespoke throwaway fixture page, component-only rendering, static screenshot or JSON API response is not that smoke.

Permanent tests should protect plausible behavior regressions, not freeze arbitrary layout snapshots or test trivial constants. Prefer integrated route/query restoration, status/unknown fidelity, decimal display, target identity, preview invalidation, focus recovery and same-key ambiguity recovery. Data fixtures are credential-free, isolated, labelled “SIMULATED — NON-LIVE” in visible UI/evidence and never mixed into a LIVE account view. Test-only fixtures never become a production fallback or a hidden switch for sample account data. Remove temporary harnesses/scaffolds after accepted evidence while retaining justified permanent tests and redacted receipts.

Browser acceptance should serve the production bundle through the existing local application static path with an isolated real API/database where scenarios need server state, or another explicitly documented same-origin equivalent. Record console enablement/static-directory and credential-free account setup, without logging secrets. The backend launch/provision commands and browser runner are environment prerequisites to discover, not commands invented by this plan.

## 7. Browser scenario contract

For each scenario, record source/artifact identity, exact URL, isolated account/environment, dataset provenance, steps, expected/actual assertions, dimensions/theme, browser version, network/API/job evidence when relevant, screenshot/interaction artifacts and limitations. Use actual rendered pages and controls; assert outcomes, not merely no console errors. Each page must be visited as an entry URL and through navigation where supported. Every supported action must have a discoverability/eligibility/review assertion; representative isolated lifecycle tests may cover shared internals only if all page-specific target/argument mappings are separately checked.

| Scenario | Pages / required real interaction and assertions | Safety / required evidence |
|---|---|---|
| B01 Password/session | UX00: invalid password refusal, valid isolated sign-in, reload, expired/revoked session, logout and protected-read denial; return to preserved deep link. | Real isolated auth/CSRF, no bypass; password redacted and not stored. Session expiry must remove protected content without losing durable job truth. |
| B02 Navigation/compatibility | All UX IDs: five-nav ownership, every secondary/detail, old routes/tab aliases, direct reload, back/forward, query/filter/cursor restoration, malformed/unknown path handling, keyboard main focus. | Compare R0 route ledger against built app; record unresolvable aliases as defects, not silent redirects that lose meaning. |
| B03 Overview/attention | UX01: fresh complete, active latch, stale clock/provider, unknown/partial snapshot and disconnected SSE. Follow attention link. | Independent states visible; LIVE badge is environment, never “ready to trade”; no production clock claim. |
| B04 Account/performance | UX16: zero/missing/negative amounts, fees/currency, provider versus ledger timing, reservations/baseline/admission views and incomplete economics. | UI agrees with actual isolated API values and provenance; no invented account-wide net/chart. |
| B05 Positions | UX02: owned/external/baseline rows, exact detail, stale/changed selection, contextual action review. | Correct resource/ownership/units, unknown no longer actionable by accident; no live effect. |
| B06 Orders/fills | UX03: working/partial/terminal, empty complete versus unavailable, fill/fee details and trace/reconciliation link. | Requested/filled/residual and provider/ledger evidence never conflated. |
| B07 Protection | UX04: native proved, missing, unknown, fallback and stream-stale states; supported exact repair review/unavailable reason. | Native/fallback/stream independent; no test protection mutation. |
| B08 New trade | UX05: supported fields and MARKET/LIMIT path, invalid data, stale admission, preview invalidation, review/consent/queue and timeout recovery. | Isolated credential-free server scenarios only; no live entry or latch mutation. Accepted is not filled. |
| B09 Signal/trace | UX06/07: filter inbox, open immutable revision, original text/media, dispatch/manual provenance, missing step and linked order/job. | No fabricated source or successful missing timeline step; authenticated media handles refusal. |
| B10 Activity/management | UX08: filter and pagination, correlation links, queued versus reconciled/cancellation-only/partial/ambiguous management result. | No queue/relay message labelled execution confirmation. |
| B11 Job/approval | UX09: queued, awaiting approval, claimed, partial, reconciliation required, failed and proved complete; stale revision, self-approval refusal and allowed state controls. | Isolated durable API/DB state; no cancel represented as reversal; server authority retained. |
| B12 Notifications | UX10: pending/failed/accepted/acknowledged/unknown delivery, related source/job and supported retry eligibility. | No real external send; provider/relay/delivery concepts distinct. |
| B13 Configuration/integrations | UX11: draft/history/effective mismatch, restricted values, stale apply readback, secret stage expiry/clear behavior if fixture available. | Fake isolated test material only; no actual credential, pinned policy edit or activation. |
| B14 Safety/access | UX12/15: active/unknown proof, independent gates, unavailable Tier 3, session policy/current session and isolated expiry. | No production latch/session change; no usernames/passkeys/invitations/OTP. |
| B15 Services/releases | UX13: observed healthy versus unknown/stale, log failure, artifact approval unavailable, maintenance blocked by unknown/nonflat proof. | No actual host operation/deploy/rollback; exact allowlist/catalog constraints remain visible. |
| B16 Backups/maintenance | UX14: integrity/encryption known versus unknown, restore-drill not proved, partial/readback-missing operation and supported plan review. | No restore/replay/service disruption; backup presence is not restore success. |
| B17 Command ambiguity | Shared R2 lifecycle plus at least one trading and one advanced-operation page: double submit, lost enqueue response, reload, original-key receipt recovery, expired preview and changed target/digest. | Instrument isolated network/DB receipts to prove original operation identity and no replacement POST; all pages map exact targets correctly. |
| B18 Observation races/failures | Shared R2 plus representative list/detail/summary: delayed older response, failed refresh, 401/403/409/412/422/428 where actual contract permits, partial read, SSE gap/reconnect and account/scope mismatch. | Last-known observation time unchanged on failure; no stale response overwrites a new target; access errors do not expose prior protected data. |

### Page-scenario crosswalk

Each page's `S1` and `S2` are defined in the [page specification](BOT-CONSOLE-UX-PAGES.md). The B-scenarios above add shared negative/interaction coverage; they do not replace page-specific target and argument checks. Record both IDs in the same receipt when one observed flow genuinely proves both.

| Page critical scenarios | Package owner | Shared browser scenarios |
|---|---|---|
| UX00-S1, UX00-S2 | R2-01 | B01, B02, B18 |
| UX01-S1, UX01-S2 | R3-01 | B03, B18 |
| UX02-S1, UX02-S2 | R4-01 | B05, B11, B17 |
| UX03-S1, UX03-S2 | R4-02 | B06, B17 |
| UX04-S1, UX04-S2 | R4-02 | B07, B17 |
| UX05-S1, UX05-S2 | R4-03 | B08, B11, B17 |
| UX06-S1, UX06-S2 | R5-01 | B09, B02 |
| UX07-S1, UX07-S2 | R5-01 | B09, B18 |
| UX08-S1, UX08-S2 | R5-02 | B10, B11 |
| UX09-S1, UX09-S2 | R2-03 core; R4 trading; R5-03 complete | B11, B17, B18 |
| UX10-S1, UX10-S2 | R5-02 | B12, B17 |
| UX11-S1, UX11-S2 | R6-01 | B13, B17 |
| UX12-S1, UX12-S2 | R6-02 | B14, B17 |
| UX13-S1, UX13-S2 | R6-03 | B15, B17 |
| UX14-S1, UX14-S2 | R6-03 | B16, B17 |
| UX15-S1, UX15-S2 | R6-02; R2-01 session core | B01, B14 |
| UX16-S1, UX16-S2 | R3-02 | B04, B18 |

### 7.1 Required adversarial checks

- **Stale/unknown:** absent/null/invalid observation time, omitted threshold, explicit stale source, unobserved permission/gate, unknown environment/account and connection live with stale data. Display facts separately; never infer fresh/ready from clock tick, fetch completion or SSE connectivity.
- **Empty/partial:** complete zero-item collection, `items: null`, incomplete page, failed provider but available ledger, unknown total, pagination boundary and missing linked evidence. Only a proved complete scoped read may claim empty; partial data is visibly qualified.
- **Financial ambiguity:** no fill/economic record, fee currency mismatch, baseline/external quantity, partial fill/close, reconciliation pending, claimed effect with timeout, old/new resource version and accepted job lacking readback. Unknown PnL is not `0`; unknown effect is not success or safe retry.
- **Identity/scope:** expired/revoked session, denied capability, wrong account/scope, principal change, preview expiry and digest/target change after consent. Do not carry authorization or pending payload into a different scope.
- **Interaction:** double activation, Escape/reopen review, focus return after cancel/error, touch activation at dense rows, navigation while loading and long text/IDs/amounts. User must know what is selected and whether an operation is still pending.

Capture an assertion of the truthful failure state and action eligibility, not just an error toast. A backend refusing a forbidden action is necessary but does not accept a frontend that says it succeeded.

## 8. Visual, responsive and accessibility acceptance matrix

Run this matrix on the integrated candidate in **both light and dark themes** at **1440, 1024, 768 and 390 CSS-pixel widths**. Record viewport height, browser zoom and pixel ratio; “mobile screenshot” alone is insufficient. All UX00–UX16 need a documented pass per width/theme for their normal, loading/empty and relevant error/unknown states. Shared behavior can reference one shared receipt, but each page must prove content fit, source/action meaning and its unique interactions. Complex forms/reviews and dense trading tables also require their specific failure states. Do not silently count one screenshot of Overview as all pages.

| Width | Layout expectation | Interactive/observable acceptance |
|---|---|---|
| 1440 | Clear five-destination desktop hierarchy; compact header; restrained content width and useful dense trading tables/details; no giant empty card grid. | Primary task/state visible without raw-object reading; table comparison works; review target/consequence visible; all controls reachable and focus visible. |
| 1024 | Shell/content adapt without crowded header or squeezed action area; details and forms may stack deliberately. | No clipped account/environment or primary action; table columns remain meaningful or an intentional labelled horizontal data region is provided; no document overflow. |
| 768 | Compact navigation pattern and stacked details as specified; preserve task order, source/freshness and context. | Navigation open/close/escape/focus return; no overlay covering critical status/actions; forms/errors readable; list-to-detail/back preserves context. |
| 390 | Single-column task flow with purposeful compact rows/cards or labelled data-region scrolling; readable amounts/units; full-screen detail/review where needed. | No document-level horizontal scroll, hidden submit or truncated critical consequence; touch/focus works; no accidental row/action activation; source/freshness and selected target remain visible. |

Additional mandatory checks:

- WCAG 2.2 AA: normal text contrast at least 4.5:1, large text 3:1, meaningful UI/focus/non-text boundaries 3:1 where applicable; semantic color plus text/icon meaning, never color alone. Measure actual theme token pairs and inspect disabled/error/selected states, not only body text.
- Primary touch actions target 44×44 CSS pixels; dense-row controls at least 24×24 with adequate separation and accessible names. No essential action requires hover. Critical data never relies on 12px metadata styling.
- Keyboard-only: skip link, all five destinations and secondary navigation, tab behavior where real tabs are used, filter/apply/clear, table row links, details, forms, review, errors and sign out. Logical order, visible unobscured focus, no trap; modal focus/escape/return must be demonstrated.
- Screen-reader/AX semantics: one clear main/page heading, meaningful names, associated labels/help/errors, table headers and row identity, dialog name, controlled status announcements and no repeated announcement storm from live timers/SSE. Automated DOM checks help; human assistive-technology review remains separately recorded.
- At 200% zoom: text/actions still readable and usable; critical content not clipped. At 320 CSS-pixel reflow: no two-dimensional page scrolling except explicitly labelled, keyboard-operable financial data regions that genuinely require it; surrounding controls/details/forms still reflow. Also inspect long account IDs, symbols, timestamps, translations of plain-language labels if present and large/negative decimal amounts.
- Respect reduced motion, avoid distracting status animation and preserve browser text resizing. Theme preference persists and system option follows only when selected. Freshness/source stays interpretable in both themes.
- Screenshots prove composition at captured states. Browser assertions prove observed behavior. Automated contrast/accessibility checks cannot prove task comprehension. The five-second exercise and “excellent/simple” judgment require the R7-02 human record, not fabricated automation claims.

## 9. Frontend release/rollback readiness — not rollout permission

The current build is compiled into the application image according to existing implementation guidance. “Frontend-only” describes the change surface, not proof that an actual deployment can swap assets without restarting a web process. Discover the actual approved packaging/static-serving mechanism and preserve unrelated services; do not invent an independent CDN, deploy command or hot-swap guarantee.

### Candidate readiness dossier

- [ ] Exact candidate source identity, lockfile/build inputs, bundle asset hashes and corresponding application artifact identity; no secrets/sample LIVE data included. Previous known-good bundle/application artifact is identified and retained.
- [ ] API compatibility ledger: current endpoint/method/envelope/action schema unchanged; no backend/schema/worker migration; new and previous bundles work against the unchanged API for required reads, auth, review, jobs, media and exports.
- [ ] Route/static compatibility: index fallback, asset base paths, old deep links/query aliases and direct refresh verified in the intended local serving topology. Retain old hashed assets for a safe overlap if the actual mechanism requires them; cache/header behavior is evidenced, not assumed from source alone. A tab with old JavaScript must not become unusable on the first lazy/resource request.
- [ ] Session continuity: existing authenticated sessions, expiry/revocation, CSRF refresh and digest-bound consent behave correctly across a frontend reload; no forced reauthentication or broader consent merely for redesign. If real deployment necessarily invalidates a session, document that actual behavior and user-safe recovery rather than promising continuity.
- [ ] Pending/in-flight continuity: old tab/new tab, accepted job, unresolved enqueue receipt and pending public payload retain the same durable operation identity; jobs continue server-side independent of navigation. Old/new bundle and rollback can locate existing jobs and recover receipts without a new financial request.
- [ ] Frontend storage changes, if any, have explicit forward/backward handling for theme/timezone and unresolved pending operations; do not clear session storage globally or strand an old payload. Prefer unchanged pending-operation format for this redesign.
- [ ] Safety invariants unchanged: LIVE/LIVE/1 policy, independent latches/gates/clock incident, risk limits, exact ownership, baseline exclusions, server permissions, distinct approvals, credentials and host allowlists. No mutation-bearing migration or verification step hidden in asset release.
- [ ] Release/rollback owner, authorized target/artifact/scope, expected user-visible change, observation window, abort thresholds and read-only evidence sources specified. This record contains readiness only; obtain separate authorization before release.

### Abort and rollback planning

A wrong account/environment, misleading financial success, lost idempotency recovery, inaccessible core route/auth, broken review/consent, fatal rendering, protected-data exposure, asset mismatch or critical accessibility failure is a stop/no-go condition. Keep unknown backend/provider state distinct from a frontend defect; do not mask an incident by clearing its latch or changing policy.

Rollback means restoring the previously approved frontend-containing artifact through the established authorized mechanism, not down-migrating storage, erasing jobs, replaying requests, resetting auth/security state or switching production to DEMO/execution=0. Prove locally that the old bundle can read jobs created while the new bundle was open, recover unresolved operation receipts and use the unchanged API/session contract. If compatibility cannot be demonstrated, release readiness is BLOCKED until the actual safe rollback path is established.

A separately authorized post-release observation should use fresh read-only evidence for bundle/artifact identity, authenticated navigation, existing routes, source/freshness rendering, existing jobs, auth boundary and unchanged safety scopes. It must not create a trade/job with provider or host effect, send notifications, rotate credentials, restore backup, release a latch or revoke real sessions as a smoke test. Production receipt fields stay blank/NOT REQUESTED in this planning deliverable. Local browser success and historical deployed P-phase evidence do not satisfy this future observation.

## 10. Evidence archive record

Use the existing approved private artifact/evidence location discovered by the integration owner; do not create a public dump of account data. The record may live in the progress register with links to redacted artifacts. Store failed and passing attempts with distinct IDs, not overwritten filenames.

```text
Evidence ID / package ID / UX IDs / scenario IDs:
Evidence kind: source | design | automated | browser | human | production-read-only
Scope claimed and explicitly NOT claimed:
UTC capture time / observer or reviewer:
Source revision and affected uncommitted content identity:
Build/artifact/API/schema identity where applicable:
Environment: isolated local / other explicitly authorized target:
Account alias/scope/environment; dataset provenance; SIMULATED — NON-LIVE label:
Prerequisites: isolated DB identity, no provider credentials, browser/server setup:
Command or exact browser URL and step sequence:
Viewport CSS width×height, theme, zoom, DPR, browser/assistive tool version:
Expected assertions / actual observations:
Result: PASSED | FAILED | PARTIAL | BLOCKED | NOT RUN
Request/job/operation correlation IDs (redacted where necessary):
Network/DB evidence proving scope/idempotency/outcome when required:
Artifacts: redacted screenshots, interaction trace, logs, command output, hashes:
Secrets/personal data redaction and access restrictions:
Known limitations; missing scenarios; whether human review occurred:
Affecting changes that invalidate this receipt:
Reviewer / promotion decision / exact acceptance scope:
```

No password, CSRF token, cookie, verifier, private key, provider credential or plaintext secret belongs in screenshots, traces, URLs, logs or source. Redact genuine account/source details as needed while preserving enough correlation to prove the claim. A screenshot without source/environment/time is not reusable evidence; a source reference without an exercised behavior is not a runtime receipt. Finish by updating the [progress register](BOT-CONSOLE-UX-PROGRESS.md) with precise states and next eligible work, never a claim that planning has delivered the UI.
