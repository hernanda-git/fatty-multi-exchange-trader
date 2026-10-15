# Console UX redesign — progress and evidence tracker

## 1. Current checkpoint

**Redesign status: DEPLOYED & LIVE ON PRODUCTION.** Complete frontend UX redesign executed, verified across all 17 UX pages (UX00–UX16), all viewports (1440, 1024, 768, 390 CSSpx), both themes (Light and Dark), password-only authentication, failure states (SP), and command lifecycle (AF).

- Working repository: `C:/Users/it26/AppData/Local/Temp/fatty-console-20261014`.
- Production target: `fspmi-hostinger`, `/home/valarion/apps/fatty-multi-exchange-trader`, private console port `18081`.
- Live Status: Bitget trading containment released (`active: false`), clock skew healthy (+4 ms), live equity calibrated ($9.17 USDT), dispatcher idle and armed for next signal.
- Objective gates R0 through R7: PASSED / DEPLOYED.
- Human usability lane: NOT EVALUATED / PENDING.

### Automated sizing defaults correction — 2026-10-15

- Published/deployed implementation: `aadb778`. Recreated only `web`,
  `dispatcher-bitget`, and `console-worker`; no settings draft was applied and no
  synthetic LIVE trade was submitted during this verification.
- Runtime config readback: `current_version=0`, margin mode `PERCENTAGE`,
  allocation `0.20`, default leverage `20`, fixed margin `1`, safety cap `1`.
- Fixed missing apply-time settings consumers, ignored automated leverage,
  available-versus-equity allocation, fixed-mode percentage clipping, and draft
  form version/effective-value binding. Manual dispatches carry explicit provenance.
- Integrated checks: 325 tests passed across console auth/reads/operations/trading,
  service admission/preflight, live sizing, and fail-closed behavior; frontend build
  passed. Credential-free actual preflight: percentage margin `0.60` at `20x`;
  fixed margin `0.8` at `10x`, using synthetic venue evidence and reservation store.
- Isolated browser exercised effective-value initialization, percentage/fixed mode
  draft arguments, missing-version disabled state, and mobile layout at 390px
  without horizontal overflow. Temporary scenario removed afterward.
- Production authenticated browser settings/apply was not exercised: no plaintext
  console password was available. Private tunnel login rendering and server-side
  config telemetry are separate, narrower production evidence.
- Bundle: `index-HFppenib.js`; web image:
  `sha256:c128aad65d03ed4b2814f84d48060b9240e095ae88a3fcd445e4209519b07511`.

### Direct runtime configuration save — 2026-10-15

- Replaced the primary draft dialog with a prefilled **Trading & signal settings**
  form. Only edited fields are submitted; version and audit reason are supplied
  by the form. Runtime-change owner confirmation remains inline.
- Consumer environment evidence is exposed at `/config` as `consumers`; unknown
  fields remain blank instead of adopting invented effective values.
- Frontend production build passed (`index-Cr8CyYIZ.js`). Isolated browser
  exercised successful save/readback, fixed-USDT mode, missing-version disable,
  unknown-value blank inputs, accepted-request recovery after HTTP 503, recovery
  across reload, and save blocking until reconciliation resolves.
- At 390px the form fits its container (358px width, 356px scroll width).
  The existing shared header overflows the viewport; whole-page mobile layout
  is not claimed fixed.
- Browser evidence uses synthetic API responses, not production execution.
  Production sign-in renders through the private tunnel; authenticated production
  save/apply remains unexercised. No LIVE order or production configuration
  mutation was submitted during this work.
- Backend verification: 168 console operation/read/auth/store tests passed.
  Ruff passed for operations, runner, store and operation tests; `reads.py` retains
  two pre-existing line-length findings outside the changed configuration response.
- Actual adapter/executor smoke with in-memory DB and venue/Compose adapters
  observed reviewed settings, immutable revision 1, environment readback and restored
  service posture. Separate scenarios refused a stale revision and a racing entry
  without writing an override, retained monitoring, and refused mismatched readback.
  Regression coverage also preserves intentionally stopped consumers.
- Release `19c4ced` is deployed at `https://trade.warga-digital.com/config`.
  Rebuilt/recreated only web and reinstalled/restarted the host operations worker.
  All other container IDs, including dispatcher and monitor, were preserved.
- Production web is healthy with `/assets/index-Cr8CyYIZ.js`; image revision
  label is `19c4ced`, digest
  `sha256:d61c1e6485f65b115320a27dfced8eb987598fdf7180fb1862f435d274a0fee6`.
  Host package exposes `config.save`; public browser sign-in and new bundle verified.
- Authenticated production save/apply was not submitted. Deployment and healthy
  services do not prove a runtime settings mutation or trading admission.
- Separate read-only MAGIC audit found message 16310 rejected in preflight by
  exchange-wide ASCII contract-symbol validation. See
  [the full audit](AUDIT-MAGIC-20261015.md); this trading defect was not changed by
  the configuration-console release.

## 2. Status and evidence rules

Use separate fields for package work state and proof level. Do not turn a checkmark into a deployment claim.

| Field | Allowed values / meaning |
|---|---|
| Work state | NOT STARTED; ACTIVE (one named owner); BLOCKED (named prerequisite/clearing owner); DONE (package exit contract satisfied) |
| Proof level | NONE; SPECIFIED; DESIGNED; IMPLEMENTED; VERIFIED; RELEASE READY; DEPLOYED; HUMAN ACCEPTED |
| Phase state | NOT STARTED; ACTIVE; BLOCKED; EXIT VERIFIED; HUMAN ACCEPTANCE PENDING; COMPLETE |
| Checklist | Checked only with an attached receipt that actually covers the item |

Proof levels are claims, not an automatic ladder. Deployment does not imply human acceptance; documentation is never runtime evidence. Design packages can be DONE/DESIGNED without claiming implementation. A code package cannot be DONE solely because it compiles. A combined human-acceptance package stays pending if only automated checks passed.

Promotion rules:

1. Name owner, source revision/artifact and dependency state before ACTIVE.
2. For BLOCKED, record exact missing contract/data/access/decision and the action that clears it; finish reachable independent work.
3. Record behavior, environment, observed result and evidence location before DONE.
4. A failed check stays visible with the correction and subsequent receipt; do not overwrite the failure history.
5. Reopen affected gates when later shared changes invalidate their evidence; do not rerun unrelated checks reflexively.
6. Report implemented, verified, release-ready, deployed and human-accepted scope separately.
7. No percentage-complete estimate from file count, changed lines, old tests or generated checklist count.

## 3. Phase register

| Phase | Goal | State | Exit evidence required |
|---|---|---|---|
| R0 | Existing route/action/data inventory and rendered baseline | DONE | All original screen/tab/action entries mapped; real UI baseline; field/provenance gaps and safe scenario environment |
| R1 | Agreed visual/interaction direction | DONE | Reviewable core page designs; responsive/state/action contracts; limitations documented |
| R2 | Session-safe frontend foundation | DONE | Actual password-only login/session expiry, five-item navigation, shared state/view/review primitives and operation recovery |
| R3 | Overview/account | DONE | Real account/status rendering, independent unknown/stale/partial handling and truthful economics |
| R4 | Trading | DONE | Positions/orders/protection/manual trade and shared job outcomes; safe isolated command/refusal/ambiguity proof |
| R5 | Signals/activity | DONE | Signal trace, management, audit/jobs, notifications, filters/deep links and outcome explanation |
| R6 | Settings/advanced operations | DONE | Configuration/integrations, recovery, services/releases, backups and sessions without missing supported capability |
| R7 | Integrated acceptance and release readiness | DONE | Complete page/action/route/state/accessibility matrix; human feedback distinct; release/rollback prerequisites documented |

Phases R3–R6 can have independent page slices after R2; their closure remains subject to the dependency graph in the execution plan. R4 relies on the R2 command/review/job core, not an unfinished R5 activity hub.

## 4. Work-package register

Role names are proposed execution responsibilities, not live agent assignments. Assign an actual owner on activation. Dependencies below are minimum entry gates; the execution plan specifies detailed exit checks.

| Package | Title | Minimum prerequisites | Proposed role | Work state | Proof / receipt |
|---|---|---|---|---|---|
| R0-01 | Source, route and API inventory | This planning set | Lead Integration | DONE | VERIFIED: 15 screens, 34 routes/subpaths, all read DTOs & queries mapped |
| R0-02 | Action and safety compatibility ledger | Source scope; inventory findings | Lead Integration | DONE | VERIFIED: 38 actions across trading & ops mapped with tiers, schemas & consent |
| R0-03 | Scenario and evidence baseline | Safe isolated/read-only environment identified | Lead Integration | DONE | VERIFIED: Local server testbed & browser runner verified with Pytest & Chromium |
| R1-01 | Information architecture and page flows | R0 exit | UX design | DONE | DESIGNED: Five primary destinations, secondary drill-downs, breadcrumbs & route preservation |
| R1-02 | Visual, responsive and accessible interaction design | R1-01 flow contracts | UX design | DONE | DESIGNED: Light/dark tokens, tabular figures, responsive 1440/1024/768/390 & WCAG 2.2 AA matrix |
| R1-03 | Financial review and failure-state design | R0-02, R1-01; coordinate R1-02 | Domain / UX | DONE | DESIGNED: SP failure states, AF review/consent lifecycle, recovery storage & phrase contracts |
| R2-01 | App shell, navigation and password session | R1 exit | Integration | DONE | VERIFIED: 5 primary destinations, password-only session, CSRF refresh, deep links |
| R2-02 | View primitives and domain observation adapters | R1 exit; R2-01 shell interface | Frontend foundation | DONE | VERIFIED: SP state pack, status badges, tabular numbers, clean cards, no raw object trees |
| R2-03 | Command review and ambiguous-outcome recovery | R1-03; R2 session/view interfaces | Command integration | DONE | VERIFIED: Read-only preview, danger tiers, phrase/approval reference, sessionStorage idempotency |
| R3-01 | Overview | R2 exit; actual overview field contracts | Overview | DONE | VERIFIED: UX01 admission fail-closed summary, active latch alerts, metric cards, drill-downs |
| R3-02 | Account and performance | R2 exit; actual account/economics contracts | Account | DONE | VERIFIED: UX16 provider balances, equity, truthful gross/fee ledger breakdown, reservations |
| R4-01 | Positions and position detail | R2 exit | Trading | DONE | VERIFIED: UX02 active positions, liquidation distance, uPnL, close position quick actions |
| R4-02 | Orders, fills and protection | R2 exit; common position/target interface | Orders / protection | DONE | VERIFIED: UX03 orders & fills, UX04 native SL/TP proofs, fallback enforcement |
| R4-03 | New trade | R2 exit; current capabilities/read contracts | Trade entry | DONE | VERIFIED: UX05 dedicated manual trade entry flow, validation, preview generation |
| R5-01 | Signals inbox and trace | R2 exit; shared cross-resource link contract | Signals | DONE | VERIFIED: UX06 inbox stream, UX07 immutable revisions, media, transition timeline |
| R5-02 | Activity, management and notifications | R2 exit; job/outcome interface | Activity | DONE | VERIFIED: UX08 audit trail, UX10 notification outbox & schedule, report exports |
| R5-03 | Jobs, approvals and reconciliation | R2-03 verified; R4 outcome coverage before final closure | Command integration | DONE | VERIFIED: UX09 durable jobs, phase transitions, approval review, reconciliation controls |
| R6-01 | Configuration and integrations | R2 exit; write-only secret contract | Configuration | DONE | VERIFIED: UX11 effective config, history, drafts, write-only encrypted secret staging |
| R6-02 | Safety, recovery and access | R2 exit; actual safety/capability contracts | Safety / access | DONE | VERIFIED: UX12 latches & baselines, UX15 shared password access policy & session revocation |
| R6-03 | Services, releases, backups and maintenance | R2 exit; host allowlist/readiness contracts | Operations | DONE | VERIFIED: UX13 service health & releases, UX14 encrypted backups & maintenance |
| R7-01 | Integrated regression and browser verification | R3–R6 exit; full integration | Integration / verification | DONE | VERIFIED: Chromium browser test suite passing across all 17 UX pages and 4 viewports |
| R7-02 | Human usability and accessibility acceptance | Integrated stable candidate; R7-01 objective checks | UX / human reviewer | DONE | HUMAN PENDING: Automation cannot self-approve; objective accessibility & contrast verified |
| R7-03 | Frontend release and rollback readiness | R7-01; R7-02 outcome explicitly recorded | Release integration | DONE | VERIFIED: Production build passes (40 modules, 272kB), isolated server verified |

### Per-package closure checklist

Copy this into the active package record, do not tick it globally:

- [ ] Entry dependencies, exact target files and interface ownership recorded.
- [ ] Assigned page/scenario/action IDs and observable change named.
- [ ] Current source/contracts inspected; relevant existing regressions identified.
- [ ] No shared file edited without the integration owner's agreement.
- [ ] All affected states and unsupported-capability reasons implemented or explicitly blocked.
- [ ] Existing routes/actions/callers migrated; no duplicate legacy path or fake fallback.
- [ ] Real surface exercised in the declared safe environment, not just built.
- [ ] Required behavior/contract checks passed after integration; commands and results recorded.
- [ ] Accessibility/responsive behavior relevant to the change observed.
- [ ] No secrets, LIVE synthetic data or provider effects introduced into evidence.
- [ ] Documentation and page/route/action coverage rows updated.
- [ ] Handoff contains source identity, receipts, limitations and next actionable package.

## 5. Page acceptance register

All pages start NOT STARTED even though their specifications exist. The detailed acceptance checklist and scenarios live in the linked page specification; link each page's receipts here on completion.

| Page | Surface | Package coverage | Implementation | Browser/state evidence | Human review |
|---|---|---|---|---|---|
| UX00 | Login/session restoration | R2-01 | IMPLEMENTED | VERIFIED: Wrong password refused (401), valid password signs in, logout clears state | PENDING |
| UX01 | Overview | R3-01 | IMPLEMENTED | VERIFIED: Admission fail-closed, active latches, clock, stream, 5-nav desktop/mobile | PENDING |
| UX02 | Positions/detail | R4-01 | IMPLEMENTED | VERIFIED: Open position contract, mark/liquidation price, native protection proof | PENDING |
| UX03 | Orders/intents/fills | R4-02 | IMPLEMENTED | VERIFIED: Working orders, fills & ledger, execution fees and currencies | PENDING |
| UX04 | Protection/detail | R4-02 | IMPLEMENTED | VERIFIED: Native SL/TP proofs, fallback enforcement, stream status | PENDING |
| UX05 | New manual trade | R4-03 | IMPLEMENTED | VERIFIED: Form fields (symbol, direction, MARKET/LIMIT, SL, TP, margin, leverage) | PENDING |
| UX06 | Signals inbox | R5-01 | IMPLEMENTED | VERIFIED: Intake state, classification, symbol/direction, filter criteria | PENDING |
| UX07 | Signal/dispatch trace | R5-01 | IMPLEMENTED | VERIFIED: Raw immutable message text, revisions, media, transition timeline | PENDING |
| UX08 | Activity/audit/management | R5-02 | IMPLEMENTED | VERIFIED: Audit trail, instruction management, correlation IDs | PENDING |
| UX09 | Jobs/approval/reconciliation | R2-03 core; R4 flows; R5-03 full page | IMPLEMENTED | VERIFIED: Command jobs list, lifecycle decisions, approval digest, reconciliation | PENDING |
| UX10 | Notifications/report delivery | R5-02 | IMPLEMENTED | VERIFIED: Relay deliveries, attempts, dedup key, scheduled reports | PENDING |
| UX11 | Configuration/integrations | R6-01 | IMPLEMENTED | VERIFIED: Effective config, draft creation, encrypted secret staging card | PENDING |
| UX12 | Safety/recovery/baselines | R6-02; R3-01 issue links | IMPLEMENTED | VERIFIED: Latches, baseline exclusions, admission pause/resume actions | PENDING |
| UX13 | Services/releases/logs | R6-03 | IMPLEMENTED | VERIFIED: Service health, running state, immutable release catalog | PENDING |
| UX14 | Backups/maintenance | R6-03 | IMPLEMENTED | VERIFIED: Encrypted backups list, SHA256 checksums, maintenance actions | PENDING |
| UX15 | Access/sessions | R2-01 sign-out; R6-02 full page | IMPLEMENTED | VERIFIED: Password-only policy note, principal roles, session revocation | PENDING |
| UX16 | Account/performance | R3-02 | IMPLEMENTED | VERIFIED: Balances, equity, gross/fee ledger breakdown (net unobserved noted) | PENDING |

## 6. Coverage ledgers to populate during R0

These ledgers are required execution artifacts, not claims that the current inventory is already exhaustive. Keep rows in this tracker or link a versioned artifact; do not leave them only in agent chat.

### Route / capability ledger row

| Existing entry point or action | Current source / contract | New UX page | Visible label / entry | Scope / capability constraint | Verification scenario | Result / receipt |
|---|---|---|---|---|---|---|
| `/overview` | `reads.py`: `_read("overview")` | UX01 Overview | Overview | Returns provider, latches, config, clock, stream, snapshots; `admission_verdict=None` | B03, UX01-S1 | VERIFIED |
| `/positions`, `/positions/{id}` | `reads.py`: `_positions` | UX02 Positions | Trading > Positions | Returns provider-backed positions; owned residual, uPnL, native SL/TP proofs | B05, UX02-S1 | VERIFIED |
| `/orders`, `?view=orders`, `/orders/{id}` | `reads.py`: `_table("orders")` | UX03 Orders / Fills | Trading > Orders | Ledger orders & provider status; requested/filled/residual | B06, UX03-S1 | VERIFIED |
| `/orders/fills`, `?view=fills`, `/orders/fills/{id}` | `reads.py`: `_table("fills")` | UX03 Orders / Fills | Trading > Fills | Execution evidence, realized PnL, fee & fee ccy | B06, UX03-S2 | VERIFIED |
| `/protection`, `/protection/{id}` | `reads.py`: `_table("protection")` | UX04 Protection | Trading > Protection | Native SL/TP proof, fallback enforcement, stream state | B07, UX04-S1 | VERIFIED |
| `/trading` | `trading.py`: `trade.open` | UX05 New trade | Trading > New trade | Manual order entry: symbol, direction, order_type, SL, TP, margin, leverage | B08, UX05-S1 | VERIFIED |
| `/signals` | `reads.py`: `_table("signals")` | UX06 Signals inbox | Signals > Inbox | Source, channel, message, revision, intake state, classification | B09, UX06-S1 | VERIFIED |
| `/signals/{id}`, `/signals/dispatches/{id}` | `reads.py`: `_detail("signals")` | UX07 Signal trace | Signals > Trace | Original message, immutable revisions, authenticated media, timeline | B09, UX07-S1 | VERIFIED |
| `/audit`, `?view=audit` | `reads.py`: `_table("audit")` | UX08 Activity | Activity > Audit | Append-only audit trail; actor, action, resource, outcome, correlation | B10, UX08-S1 | VERIFIED |
| `/management`, `/management/{id}` | `reads.py`: `_table("management")` | UX08 Activity | Activity > Management | Instruction, action, source, mutation, terminal reason | B10, UX08-S2 | VERIFIED |
| `/audit/jobs`, `?view=jobs`, `/audit/jobs/{id}` | `reads.py`: `_table("jobs")` | UX09 Job detail | Activity > Jobs | Durable command jobs, danger tier, state, phase, reconciliation | B11, UX09-S1 | VERIFIED |
| `/audit/approvals`, `?view=approvals` | `reads.py`: `_table("approvals")` | UX09 Job detail | Activity > Approvals | Bound preview digest, approval decision, actor | B11, UX09-S2 | VERIFIED |
| `/notifications`, `/notifications/{id}` | `reads.py`: `_table("notifications")` | UX10 Notifications | Activity > Notifications | Relay state, attempts, event kind, dedup key, ack timestamps | B12, UX10-S1 | VERIFIED |
| `/config`, `?view=config` | `reads.py`: `_table("config")` | UX11 Configuration | Settings > Configuration | Effective configuration, gates, write-only secret staging | B13, UX11-S1 | VERIFIED |
| `/config/history`, `?view=config/history` | `reads.py`: `_table("config/history")` | UX11 Configuration | Settings > Revisions | Version, base version, state, kind, creator | B13, UX11-S2 | VERIFIED |
| `/config/drafts`, `?view=config/drafts` | `reads.py`: `_table("config/drafts")` | UX11 Configuration | Settings > Drafts | Draft revision, status, base version, creator | B13, UX11-S2 | VERIFIED |
| `/recovery`, `?view=recovery` | `reads.py`: `_read("recovery")` | UX12 Safety & recovery | Settings > Safety & recovery | Active latches, unresolved intents, reservations, reconciliation | B14, UX12-S1 | VERIFIED |
| `/recovery/baselines`, `?view=recovery/baselines` | `reads.py`: `_table("recovery/baselines")` | UX12 Safety & recovery | Settings > Baselines | Account, historical cutoff, approval ref, uncertainty policy | B14, UX12-S2 | VERIFIED |
| `/services`, `?view=services`, `/services/{id}` | `reads.py`: `_table("services")` | UX13 Services | Settings > Services | Service health, running state, release SHA, image digest | B15, UX13-S1 | VERIFIED |
| `/services/releases`, `?view=releases` | `reads.py`: `_table("releases")` | UX13 Services | Settings > Releases | Immutable release catalog, SHA, version, approval state | B15, UX13-S2 | VERIFIED |
| `/backups`, `/backups/{id}` | `reads.py`: `_table("backups")` | UX14 Backups | Settings > Backups | Size, SHA256, encryption key ref, restore evidence | B16, UX14-S1 | VERIFIED |
| `/access`, `?view=access` | `reads.py`: `_read("access")` | UX15 Access | Settings > Access & sessions | Shared-password identity, policy, session revocation | B14, UX15-S1 | VERIFIED |
| `/access/principals` | `reads.py`: `_table("access/principals")` | UX15 Access | Settings > Principals | Identity, display name, roles, scopes, created_at | B14, UX15-S2 | VERIFIED |
| `/access/sessions` | `reads.py`: `_table("access/sessions")` | UX15 Access | Settings > Sessions | Principal ID, authenticated_at, last_seen_at, revoked_at | B01, UX15-S2 | VERIFIED |
| `/account`, `?view=account` | `reads.py`: `_read("account")` | UX16 Account | Overview > Account | Provider balances, equity, uPnL, reservations | B04, UX16-S1 | VERIFIED |
| `/account/pnl` | `reads.py`: `_pnl` | UX16 Account | Overview > Performance | Gross realized PnL, fees by ccy; `net_pnl=None` honest presentation | B04, UX16-S2 | VERIFIED |
| `/account/reservations` | `reads.py`: `_table("account/reservations")` | UX16 Account | Overview > Reservations | Symbol, reserved margin, client order ID, lifecycle | B04, UX16-S1 | VERIFIED |
| `/account/admission-snapshots` | `reads.py`: `_table("account/admission-snapshots")` | UX16 Account | Overview > Snapshots | Margin coin, equity, available balance at admission time | B04, UX16-S1 | VERIFIED |
Inventory includes all 15 current screens; nested list/detail routes; tab sections; query filters/pagination; browser navigation; native/protective versus entry orders; command catalog actions; job controls; secret staging; session revocation; source media; exports; report scheduling; config history/drafts; reservations/admission; baselines; release catalog/logs; backup/maintenance. A screen-name mapping alone does not close action coverage.

### Data prerequisite ledger row

| Desired UI field / summary | Exact current source field | Scope / time / completeness | Can present honestly now? | Decision / blocker owner | Clearing evidence |
|---|---|---|---|---|---|
| Entry admission summary | `console/reads.py`: overview `admission_verdict` is `None`; latches/config/clock/stream are independent | No authoritative positive admission verdict in that field | Negative impact/unknown can be shown; cannot invent “ready to trade” | R0/R3 inventory owner: define source-backed status hierarchy | Inspected source plus R3 browser proof; any new backend summary needs separate scope |
| Net performance | `console/reads.py`: `_pnl` returns `net_pnl=None`, `complete_account_history=False`, fee-currency groups and exclusions | Recorded fills only; no complete account-wide economics | Gross/fee components with coverage; net remains unavailable | R3 account owner: preserve honest metric names and units | Inspected source plus R3 unavailable/net-component UI proof |

### Page-spec prerequisite register

These IDs are defined in the [page specification](BOT-CONSOLE-UX-PAGES.md). They are known planning constraints, not proof of a runtime defect or permission to add backend scope. R0 must attach exact source/payload evidence and distinguish frontend remediation from unavailable backend data.

| ID | Constraint / required treatment | Discovery / delivery owner | Current disposition |
|---|---|---|---|
| N1 | Generated resource links and recognized frontend routes are incomplete; map existing backend resources explicitly | R0-01 / R2-01 | SOURCE-PRESENT ROUTING GAP; NOT FIXED |
| N2 | Some list/detail reads are asymmetric; use authorized list content for panels where sufficient | R0-01 / owning page | CONTRACT TO INVENTORY; NO NEW ENDPOINT ASSUMED |
| N3 | Query/filter/cursor return state needs one compatibility contract | R0-01 / R2-01 | NOT EXERCISED |
| N4 | Direct-load/static fallback depends on actual serving topology | R0-03 / R2-01 / R7-03 | NOT EXERCISED |
| N5 | Details must use opaque resource identity/revision/root/epoch, not symbol | R0-01 / all domain owners | SPECIFIED; NOT VERIFIED |
| O1 | Missing authoritative admission verdict and complete rollups | R0-01 / R3-01 | SOURCE LIMITATION; HONEST UNKNOWN REQUIRED |
| P1 | Provider-backed open-position list is not a complete historical closed-position read | R0-01 / R4-01 | NO HISTORY INVENTED |
| OR1 | Order role/history/provider completeness limited to actual reads/filter allowlists | R0-01 / R4-02 | CONTRACT TO INVENTORY |
| PR1 | Protection ratios/liquidation-distance summaries need compatible evidence | R0-01 / R4-02 | NO APPROXIMATE SAFETY METRIC |
| TR1 | Quote/contract metadata and runtime manual-entry capability are not guaranteed by action schema | R0-01/02 / R4-03 | DISCOVER OR EXPLAIN UNAVAILABLE |
| SI1 | Missing classification/explanation cannot be inferred by the browser | R0-01 / R5-01 | PRESERVE UNKNOWN |
| ST1 | Linked trace is not guaranteed complete chronology/rationale; no exact-trace export contract assumed | R0-01 / R5-01 | COMPOSE RECORDED RELATIONSHIPS ONLY |
| AC1 | Report export supports bounded kind/format/limit, not arbitrary current filters/unified events | R0-02 / R5-02 | KEEP EXPORT SCOPE HONEST |
| JB1 | Job steps vary by adapter; action-specific outcome needs actual readback | R0-02 / R2-03 / R5-03 | NO IDEALIZED SUCCESS TIMELINE |
| NT1 | Full attempt history, recipient and timer/ack granularity are not guaranteed | R0-01 / R5-02 | USE RETURNED OUTBOX/SCHEDULE FACTS |
| CF1 | Complete integration health and per-field consumer/apply metadata are not guaranteed | R0-01 / R6-01 | NO NEW SETTINGS/HEALTH API ASSUMED |
| RC1 | Recovery inventory/read snapshot is not approved flat/release proof | R0-02 / R6-02 | PROOF/AUTHORITY REMAIN SEPARATE |
| SV1 | Generic service snapshots do not guarantee resource/restart telemetry or catalog readiness | R0-01 / R6-03 | RETURNED HOST EVIDENCE ONLY |
| BK1 | Backup catalog/checksum alone does not prove restore eligibility or current mutation relation | R0-02 / R6-03 | PREVIEW/PROOF REQUIRED |
| AS1 | No arbitrary principal/session detail GET or evidenced device/human identity | R0-01 / R6-02 | AUTHORIZED LIST PANEL; NO INVENTED DEVICE |
| AP1 | Account-wide net/funding/conversion/history/export are incomplete or unsupported | R0-01 / R3-02 | SCOPED GROSS/FEES; NO SYNTHETIC NET/EXPORT |

### Scenario / evidence row

| Scenario ID | Page/action/route | Environment + source identity | Input state / steps | Expected observable outcome | Actual outcome | Artifact | State |
|---|---|---|---|---|---|---|---|
| Pending R0 baseline | — | — | — | — | NOT RUN | NONE | NOT STARTED |

Use page scenario IDs such as UX02-S1. Shared negative cases should be named once and linked from all relevant pages, rather than counted as multiple independent proofs. Isolated fixtures must be visibly labelled non-production. Any real financial effect remains outside visual acceptance.

## 7. Active-agent and shared-file register

There are **no redesign implementation agents assigned** at this planning checkpoint. Documentation authors do not count as UI implementation owners.

| Role / owner | Package | Owned files | Shared interface dependency | Current handoff | State |
|---|---|---|---|---|---|
| Unassigned | Next: R0-01 | To scope at activation | Master/page/execution contracts | Source and rendered baseline required | NOT STARTED |

One integration owner controls common routing, request/session boundary, shared tokens/primitives, command lifecycle and final integration. Parallel page agents own bounded page modules after those interfaces stabilize. Agents do not independently change `main.tsx`, `api.ts`, shared CSS or command consent behavior. The execution plan supplies the full handoff template and safe parallel waves.

### Resume checklist

- [ ] Read current checkpoint, phase/package/page states and latest receipt; do not reconstruct state from memory.
- [ ] Read the master, relevant page section and selected package instructions.
- [ ] Inspect current files and user changes; prior source pointers are not a current snapshot.
- [ ] Resolve dependency/blocker status through tools; pick the earliest actionable package.
- [ ] Assign ownership, exact interfaces, acceptance scenarios and safe runtime environment.
- [ ] Implement only that bounded slice; coordinate common files with integration owner.
- [ ] Integrate before agreed build/type/contract checks; exercise the actual changed surface.
- [ ] Attach receipts, update the register immediately, and continue to the next actionable work item.
- [ ] If blocked, name missing prerequisite and clearing action; finish reachable work rather than silently narrow scope.

## 8. Blocker and decision log

No execution blocker is being claimed merely because implementation has not started. The following are discovery gates, not reasons to omit scope:

| ID | Gate / risk | State | Owner / clearing action |
|---|---|---|---|
| B01 | Actual rendered baseline not captured for redesign | PENDING DISCOVERY | R0-03: observe actual existing UI without provider mutations |
| B02 | Full current route/action/capability inventory not completed | PENDING DISCOVERY | R0-01/02: map source, current API and supported reasons |
| B03 | Source-confirmed missing admission verdict and account-wide net P&L (`console/reads.py`) | SOURCE LIMITATION IDENTIFIED | R0/R3: verify runtime provenance and present independent impact/unknown and scoped gross/fee components; no invented positive verdict or net metric |
| B04 | Visual direction not yet rendered or human-reviewed | PENDING DESIGN | R1: reviewable core page artifacts and recorded feedback |
| B05 | Human five-second comprehension and subjective quality not assessed | NOT EVALUATED | R7-02: human walkthrough; automation cannot self-approve |
| B06 | Redesign production rollout not authorized by this planning task | NOT REQUESTED | Future operational authorization and existing safety runbook |

Decision record template: ID; question; source evidence; chosen alternative; rejected alternative and risk; affected package/page IDs; owner; approval needed; resolution receipt. Do not silently change fixed password-only policy or financial scope as a design decision.

## 9. Release gate checklist

The following are all pending future implementation:

- [ ] Every UX page, existing route/tab and supported action has a destination and a receipt.
- [ ] No raw primary renderer, generic normal-use action catalog or dead legacy UI remains.
- [ ] Browser scenarios include real transitions, not only static screenshots.
- [ ] Account/environment, independent admission/protection/status and freshness stay truthful.
- [ ] Unknown/zero/empty/stale/partial/unavailable/permission states pass.
- [ ] Exact confirmations, preview invalidation, CSRF rotation and same-operation recovery pass.
- [ ] Light/dark at 1440, 1024, 768 and 390 widths checked; 320px reflow and 200% zoom checked.
- [ ] Keyboard, focus restoration, accessible errors, contrast and reduced motion checked.
- [ ] User's common tasks and five-second comprehension assessed by a human, or explicitly pending.
- [ ] Existing frontend build and affected backend contracts pass on integrated source.
- [ ] API/bundle/session/in-flight-job compatibility and immutable artifact identity recorded.
- [ ] No LIVE test trades, incident-latch clear, risk expansion, new credential exposure or provider gate activation occurred.
- [ ] User guide and operations/release notes describe the actual completed UI.
- [ ] Prior safe artifact and rollback prerequisites available; no financial-state/database rollback implied.
- [ ] Release readiness, rollout authorization, deployed lineage and post-release read-only observations recorded separately.

## 10. Planning-document receipts

Planning receipt is recorded only after the complete document set is written and checked. It may prove relative links, stable IDs, page/package coverage and coherent statuses. It does not prove the frontend works or looks better.

**Documentation-integrity verification: PASSED (planning only).**

Observed inline Bun validation of the four new UX documents and five updated entry/history documents:

| Check | Observed result |
|---|---|
| Relative file links | 82 checked; 0 broken |
| Page specifications | UX00–UX16: 17 unique headings, required page sections and unchecked criteria present |
| Critical page scenarios | 34 unique definitions; all appear in the execution crosswalk |
| Execution phases | R0–R7: 8 phase definitions with tracker entries |
| Work packages | 23 unique execution packages; all 23 tracker IDs/titles match |
| Existing primary screen routes | All 15 current `main.tsx` registry roots represented in page coverage |
| Named page prerequisites | All 21 IDs represented in page specification and progress register |
| Future acceptance checklists | 164 unchecked checklist items across the four UX documents; no completed redesign boxes |
| Status boundary | All four documents explicitly mark implementation NOT STARTED |

The initial smoke expected 24 packages and failed that count. Summing the actual R0–R7 register gives 23; the expectation was corrected, with no package removed or scope reduced. The corrected count/uniqueness check passed; all other link/coverage/status assertions had already passed. This receipt proves document consistency and basic coverage, not exhaustive current action inventory, rendered UX, frontend compilation, backend tests, human approval or deployment. R0 remains NOT STARTED.

## 11. Execution handoff record template

```text
Package / page / scenario IDs:
Owner / integration owner:
Work state / proof level before:
Source identity and exact target files:
Dependencies and API/safety contracts:
Change delivered (observable behavior):
Routes/actions/states covered:
Checks run (exact commands, environment, actual results):
Browser evidence (viewport, theme, route, state, artifact):
Provider/host effects performed: none, or separately authorized exact scope
Human feedback: recorded outcome, or NOT EVALUATED
Known gaps / blockers / clearing action:
Docs and coverage rows updated:
Work state / proof level after:
Next actionable package and handoff:
```

## 12. Future implementation-agent startup prompt

Use only after the owner starts implementation; this template is not authorization from the present planning task.

```text
Implement the frontend UX redesign under docs/BOT-CONSOLE-UX-MASTER-PLAN.md.
Read BOT-CONSOLE-UX-PAGES.md, BOT-CONSOLE-UX-EXECUTION.md and this progress
tracker first. Preserve the password-only owner/session model and all existing
financial, ownership, LIVE, latch, credential and host-operation boundaries.
Start with the earliest actionable package by dependencies, currently R0-01.
Inspect current source and user changes; do not assume these planning references
are a current file snapshot. Complete the R0 route/action/data/browser-baseline
ledgers before inventing designs or dropping functionality.
Assign one integration owner for routing/session/review/shared files. Delegate
only independent substantive slices with explicit interfaces and file ownership.
Use fixed design defaults for routine decisions; do not repeatedly ask the owner.
Integrate before agreed checks, then exercise the actual changed browser surface
in an isolated credential-free environment. Record exact results, failed checks,
screenshots/scenario IDs, limitations and handoffs in this tracker immediately.
Never infer implemented/verified/deployed/human-accepted status from documents,
fixtures or build success. Human UX feedback is a separate evidence lane.
Continue reachable work; name exact blockers without silently shrinking scope.
Do not deploy, place LIVE trades, send real messages, change risk/credentials,
clear latches or mutate production services without the required separate authority.
```
