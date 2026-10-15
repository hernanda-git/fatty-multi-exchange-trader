# Console UX redesign — master plan

## 1. Mandate, status and authority

**Status: SPECIFIED; redesign implementation NOT STARTED.** This document answers the owner's request for a detailed, page-by-page, phased frontend execution plan with tracked progress and checklists. It does not report an implemented redesign, authorize a rollout or accept the existing UX.

The owner reports that the deployed interface is complicated, hard to read and acknowledge, visually unprofessional and poor at serving information. Treat that observation as the UX baseline, not something a passing build disproves. The prior console's backend/release acceptance is historical functional evidence, not acceptance of its information design.

**Outcome:** a calm, readable trading-operations application that explains the account, bot and next useful action without requiring knowledge of internal records or command names. Simplify presentation, not financial safeguards.

### Planning document set

| Document | Authority |
|---|---|
| [This master plan](BOT-CONSOLE-UX-MASTER-PLAN.md) | Scope, decisions, experience principles, invariants and completion contract |
| [Page specifications](BOT-CONSOLE-UX-PAGES.md) | UX00–UX16 layouts, fields, interactions, states and page acceptance checklists |
| [Execution plan](BOT-CONSOLE-UX-EXECUTION.md) | R0–R7 sequencing, work packages, agent ownership, verification and release gates |
| [UX progress tracker](BOT-CONSOLE-UX-PROGRESS.md) | Actual package states, next work, blockers, evidence and handoffs |
| [Existing UI/API contract](BOT-CONSOLE-UI-API-SPEC.md) | Backend resource/action contracts; historical authentication text yields to password-only policy |
| [Capability inventory](BOT-CONSOLE-CAPABILITIES.md) | Existing operation boundaries; verify current source before assuming support |
| [Original progress tracker](BOT-CONSOLE-PROGRESS.md) | Historical implementation/deployment receipts, not progress on this redesign |
| [Trading rules](TRADING-RULES.md), [LIVE policy](PRODUCTION-LIVE-POLICY.md), [agent workflow](../AGENTS.md) | Financial safety and execution constraints; never overridden by visual design |

If documents conflict: explicit owner password-only instruction and financial safety rules remain binding; this plan controls the new frontend's composition/navigation; current backend source controls actual support; the UX tracker controls completion claims. Record contradictions before implementation rather than choosing whichever is easier.

No password, verifier, token, provider credential, private key or raw environment belongs in these documents, screenshots, design fixtures or evidence.

## 2. Source-backed diagnosis

Inspected planning baseline:

- `frontend/src/main.tsx`: 15 top-level destinations in four groups; common `Value`, `Evidence`, `SchemaField`, `ActionPanel`, `ReviewDialog`, route and app-shell logic; evidence panels before primary page content; generic action catalog and repeated policy copy.
- `frontend/src/domain.tsx`: columns concatenate multiple concepts, including internal IDs and ownership/proof data; domain presentation still delegates fields to the recursive renderer.
- `frontend/src/forms.tsx`: filters and dedicated encrypted secret staging already exist; preserve useful behavior rather than replace everything indiscriminately.
- `frontend/src/api.ts`: same-origin requests, session/CSRF refresh, password login, existing-session consent and session-expired events. These are integration contracts, not cosmetic details.
- `frontend/src/style.css`: existing light/dark and responsive work is present. The redesign must fix hierarchy and workflows, not merely add another stylesheet.
- [Operator guide](BOT-CONSOLE-USER-GUIDE.md): source tracing, controlled trade management, diagnostics, maintenance, write-only secrets and ambiguity recovery all remain in scope.

This is source inspection, not a fresh screenshot audit or current provider observation. R0 must capture the actual rendered baseline, route behavior and data availability before design acceptance. No old release receipt is a fresh health check.

Known current data constraints from `src/fatty_trader/console/reads.py`: the overview returns `admission_verdict=None`; performance returns `net_pnl=None` and `complete_account_history=False`. Existing independent observations and scoped gross/fee components can support a clearer UI, but not a fabricated positive trading verdict or complete account-wide net P&L. These are explicit design inputs, not requests to expand the backend.

## 3. Product priorities and success criteria

Order of priorities: **truthful status and safe actions → comprehension → task efficiency → consistency → visual polish.** Every phase must address the first two, not postpone usability to R7.

### First-view questions

1. Is the bot admitting new entries, paused, blocked, or not currently observable?
2. What is the account exposure and what are the open positions?
3. What needs attention, why does it matter, and where do I act?

### Measurable acceptance targets

| Target | Required evidence |
|---|---|
| Five primary navigation destinations, with all original screens/actions reachable | Route/capability inventory plus browser traversal |
| Account/environment and meaningful operating state visible on the initial Overview viewport | Real browser screenshot at the declared viewport and data state |
| Position close, order cancellation and signal-blocker lookup require no raw command names | Recorded task walkthroughs with target scope and outcome |
| One incident is presented once at summary level, with linked details rather than repeated full warning panels | Screenshot and incident-navigation walkthrough |
| No raw object tree or digest wall on ordinary primary views | Page review across every UX page, including empty/error states |
| Existing deep links and browser back/forward remain useful | Explicit route/detail/query-state matrix, exercised in browser |
| Unknown, zero, empty, partial, stale and unavailable remain distinguishable | Isolated scenarios and observed UI text/state |
| Five-second status comprehension by a human reader | Human exercise answering the three first-view questions; browser automation is not proof of comprehension |
| WCAG 2.2 AA interaction/contrast intent fulfilled on changed surfaces | Contrast measurements, keyboard/focus checks, zoom/reflow and assistive-label review |

Do not claim the five-second target passed without a human exercise. If human evaluation is unavailable, record that acceptance criterion as NOT VERIFIED while reporting the other gates accurately.

## 4. Scope and navigation contract

### Primary navigation

| Destination | Entry route | Secondary destinations and purpose |
|---|---|---|
| Overview | `/overview` | Account/performance (`/account`), status and issue drill-down |
| Trading | `/positions` | Positions, orders/fills (`/orders`), protection (`/protection`), New trade (`/trading`) |
| Signals | `/signals` | Inbox, exact signal/dispatch trace and analysis/annotation |
| Activity | `/audit` | Jobs/approvals/audit, management (`/management`), notifications (`/notifications`) |
| Settings | `/config` | Configuration/integrations, safety/recovery (`/recovery`), services/releases (`/services`), backups (`/backups`), sessions/access (`/access`) |

These labels reorganize navigation; they do not require gratuitous route or backend endpoint renaming. Preserve existing valid entry routes, record links, relevant query state and resource identity. R0 records unsupported or broken current link cases; never claim a route works solely because it appears in a table. No parallel legacy UI, obsolete generic action path or compatibility shim survives cutover. Keeping a canonical existing URL is not keeping an obsolete implementation.

Safety/recovery is reachable directly from relevant issues, even though it lives under Settings. A paused operator must not hunt through settings for the current blocker. Account/performance is available from its Overview summary. Advanced tools are less prominent, not hidden from search or removed.

### Full page register

| ID | Page / task surface | Delivery phase |
|---|---|---|
| UX00 | Password login, session restoration and expiry | R2 |
| UX01 | Overview and attention summary | R3 |
| UX02 | Positions and position detail | R4 |
| UX03 | Orders, intents, fills and detail | R4 |
| UX04 | Protection and protection detail | R4 |
| UX05 | New manual trade | R4 |
| UX06 | Signals inbox | R5 |
| UX07 | Signal/dispatch trace | R5 |
| UX08 | Activity, audit and management | R5 |
| UX09 | Job detail, approval/review and reconciliation | R4 core, R5 complete coverage |
| UX10 | Notifications and reporting delivery | R5 |
| UX11 | Configuration and integrations | R6 |
| UX12 | Safety, recovery and baseline evidence | R6; summary/link treatment in R3 |
| UX13 | Services, logs, releases and maintenance | R6 |
| UX14 | Backups and restore prerequisites | R6 |
| UX15 | Shared-owner policy and sessions | R2 basic sign-out, R6 full page |
| UX16 | Account, performance, reservations and admission history | R3 |

Every current screen/tab and supported action must appear in the inventory and a destination checklist. Search, date filters, exports, histories and advanced workflows are part of coverage, not optional cleanup. Current limitations remain visible and explained. This redesign does not promise unsupported venue adapters, account-wide financial actions or independent approval from one identity.

## 5. Information hierarchy and visual direction

### App shell

- Desktop: five-item sidebar; compact account/environment header; one page title; page-relevant content and actions.
- Header: account label, unmistakable LIVE scope, compact connection indicator and owner menu. A connected event stream is not permission to trade or proof of provider health.
- Move theme/timezone and verbose identity metadata into preferences/owner menu; preserve persisted choices and accessible labels.
- Mobile/tablet: deliberate navigation drawer with current destination, close control, focus containment and restoration. No compressed desktop header with wrapping metadata everywhere.
- Persistent critical issues use one concise banner with impact and a deep link. Page-specific errors appear next to the failed section or action.
- Page heading states the task. Remove repeated slogans, claims of authority and legalistic footers from the main reading path.

### Proposed design tokens, to validate in R1

| Element | Default direction |
|---|---|
| Theme | Neutral light on first use; honor saved preference; dark and system modes supported |
| Type | Existing system-font stack; 14px base, 24px page heading, 12px noncritical metadata; readable line height |
| Rhythm | 4 / 8 / 12 / 16 / 24 / 32px spacing scale |
| Numbers | Tabular alignment, explicit currency/quantity units, sensible display precision with exact value available |
| Surfaces | Minimal borders, low visual weight, restrained elevation for overlays only |
| Color | One accent; semantic red/amber/green with text/icon meaning, never color alone |
| Tables | Purposeful columns, right-aligned numeric values, stable row identity, visible sorting/filter state where supported |
| Buttons | One dominant action per task region; destructive action styling only where warranted |
| Icons | Consistent small set with labels; no decorative emoji or unexplained icon-only commands |

No gradients, oversized greeting cards, arbitrary gauges, invented performance charts, animated market decorations, excessive status pills or separate cards for every scalar. Do not add font/icon/chart/UI packages by reflex. Reuse existing dependencies and native HTML unless a demonstrated requirement justifies a new one.

### Progressive disclosure

**Level 1 — understand:** symbol, direction, quantity, price/P&L, status, important impact and next action.

**Level 2 — decide:** position/order details, ownership, precise scope, protection, execution explanation, review consequences and blockers.

**Level 3 — diagnose:** resource IDs, immutable digest, raw sanitized evidence, source/provider comparisons, job claims/readback and correlation IDs.

The primary view is curated domain content, not a recursive object renderer. Technical details remain searchable/copyable/exportable where already supported. Progressive disclosure must not conceal unknown protection, incomplete inventory, non-owned exposure or irreversible consequences at decision time.

## 6. State and financial presentation contract

| State | Required treatment | Forbidden shortcut |
|---|---|---|
| Loading, no observation yet | Stable labelled skeleton/placeholder per region | Zero balances, green status or fabricated example data |
| Fresh known zero | Show zero with unit and applicable observation time | Replacing zero with unavailable |
| Complete empty inventory | Domain-specific empty state and useful next navigation | Inferring flatness from a filtered/incomplete list |
| Stale retained observation | Keep last known value visibly stale; explain affected action restrictions | Freshening its timestamp after a failed refresh |
| Partial success | Render successful sections independently; label unavailable parts | One failed endpoint erasing all account information |
| Unavailable/unknown | Plain reason, applicable retry/read navigation, details on demand | Claiming safe/flat/healthy by default |
| Permission/capability blocked | Relevant action unavailable with specific reason | Hiding important limitations or using a frontend override |
| Submitted/queued | Pending state and job navigation | Success toast implying provider completion |
| Ambiguous/needs reconciliation | Preserve operation identity and read-only outcome recovery | A new-key retry or duplicate effect |
| Confirmed result | Describe the actual action-specific verified outcome | Treating job creation or an HTTP 200 as financial success |

Status summaries must preserve independent facts: entry admission, incident latch, monitoring/protection readiness, provider observations and connection freshness. Show the most important impact first, with separate details. An entry block does not prove that monitoring is stopped; a fresh clock sample does not mean a durable latch is cleared; acknowledgement does not mean resolution.

Money and quantity rules:

- Backend Decimal/string contracts remain authoritative. UI display formatting must not round-trip into changed command values through JavaScript floating-point conversion.
- Separate equity, available balance, reserved margin and open exposure. State currency and account scope.
- Distinguish realized/unrealized, gross/net, fees/funding and reporting interval. Unsupported components remain unknown; do not manufacture a net metric.
- No chart unless the existing data supports its period, completeness, units and economics. Tables are the fallback, not synthetic curves.
- Show source disagreements and bot/manual/baseline ownership where they affect decisions. A symbol is not an ownership identifier.
- Relative times get exact timestamp/timezone on detail; timezone is presentation only, never a changed operation cutoff.

## 7. Interaction and authentication contract

### Contextual actions

Replace the general action catalog with dedicated domain actions and forms. Maintain a central coverage register mapping each current action to its destination, required capability, target type, review and result path. Relevant unavailable actions show a useful reason. Do not present disabled unrelated commands on every page.

Each mutating flow follows:

1. Select the exact resource and supported operation.
2. Enter task-specific fields; show clear units, validation and consequences.
3. Obtain current server preview and capabilities.
4. Review effects, exclusions, ownership, warnings and scope in plain language.
5. Perform exactly the confirmations/phrase/reference required by the backend; get consent from the existing session.
6. Submit one durable operation and track the same operation through uncertain responses.
7. Display verified readback, partial result or unresolved state truthfully.

Changes to inputs/resources/scope invalidate the preview. Session rotation must refresh CSRF everywhere. Unsaved input warnings must distinguish safe local drafts from operations already submitted; closing a drawer does not cancel an accepted effect. Persistent operation recovery must retain existing identity scoping and never store secrets.

### Password-only access is fixed

UX00 contains one password input and Sign in. No username, enrollment, passkey, invitation, OTP or external identity. No repeated password challenge for financial review. Preserve same-origin cookie sessions, CSRF, exact Origin, expiry/rate limits and sign-out revocation. Do not add credential values to fixtures, URLs, browser storage, screenshots or logs.

The shared owner cannot fulfill an independent two-person approval. A beautiful approval dialog must not create the impression that it can. Supported low-risk review must not inherit unusable Tier 3 prompts.

### Accessibility and responsive interaction

Target WCAG 2.2 AA: visible keyboard focus, accessible names, logical heading/landmark order, labelled validation and non-color state communication. Primary touch targets aim for 44px; dense controls meet at least 24px with suitable spacing. Verify 200% zoom and 320 CSS-pixel reflow, except explicitly labelled table regions where two-dimensional layout is essential.

Side panels restore focus to their invoking row/control. Modal reviews have an accessible title, intentional focus, Escape/close behavior and no hidden second active modal. Mobile details become full-height sheets/pages without trapping critical confirmation controls offscreen. Respect reduced motion. Background refresh must not steal focus, reset filters or reorder the active row underneath the user.

## 8. Architecture and clean-cutover boundaries

Use React, TypeScript, Vite, the existing same-origin API and durable jobs. This is not a backend rewrite, new trading engine, design-framework migration or state-management-platform project.

Proposed module responsibilities, finalized against existing patterns in R2:

- App/session shell and five-destination navigation.
- Small shared presentation primitives: status, amount/time, data state, table, disclosure, detail panel, form field and review shell.
- Domain-specific page components and explicit read-model adapters, preserving provenance/freshness instead of flattening it away.
- Shared command lifecycle for preview/consent/submit/recover/readback, with dedicated domain forms above it.
- Existing request/session integration in `api.ts`, kept as the single transport boundary.

Do not create a second request client or consent implementation. Do not leave the old recursive primary renderer or generic action catalog as a hidden fallback after migration. Avoid a universal schema-driven UI and an enormous new component library. Extract what the pages actually share.

A missing backend field/operation is a named dependency. Check current API/source and capability inventory, then choose an honest alternative presentation or record a scope decision. Do not add retries, endpoints, telemetry, business rules or provider operations just to make a mockup look complete. Backend contract changes require a separately reviewed work item, all callers and verification; they are not silently absorbed into frontend scope.

## 9. Sequencing, evidence and release boundaries

Execution details and package IDs live in the [execution plan](BOT-CONSOLE-UX-EXECUTION.md); current status lives in the [tracker](BOT-CONSOLE-UX-PROGRESS.md).

| Phase | Outcome | Gate before advancing |
|---|---|---|
| R0 | Rendered baseline and complete route/action/data inventory | Existing features mapped, gaps named, evidence captured |
| R1 | High-fidelity core designs, state rules and interaction contracts | Overview/positions/review direction reviewed against real data limits |
| R2 | Session-safe shell, tokens, shared states and action lifecycle | Actual login/navigation/expiry/review behavior exercised in isolation |
| R3 | Overview/account explain status and money | Real read data and critical degraded states verified |
| R4 | Position/order/protection/manual-entry tasks are understandable | Isolated supported command flows and ambiguity recovery verified |
| R5 | Signals/activity/jobs explain origin and outcome | Cross-resource navigation, history, filters and outcomes verified |
| R6 | Advanced tools retain full supported capability | Settings/recovery/host/backup/session coverage proven without LIVE effects |
| R7 | Integrated visual, usability, accessibility and release readiness | All page/route/action/state evidence complete; limitations explicit |

R0 and R1 are real prerequisites, not paperwork to skip. R3 and R4 establish the visible standard; later pages reuse that standard. R4 establishes job review/outcome UI before R5 completes the activity hub. R2 can provide basic session management without declaring the complete UX15 page finished.

Do not replace the entire frontend in one unreviewable patch. Each integrated package must leave its affected path usable; no placeholders, fake buttons, incomplete legacy fallback or broken links counted as completion. Parallelize independent page slices only after shared contracts stabilize. One integration owner owns common routing/session/review wiring and final verification.

### Autonomous decision boundaries

Agents use these design defaults and repository conventions without asking the owner about routine spacing, component names, file extraction or ordinary layout decisions. Record a short rationale for meaningful alternatives, choose the conservative option and continue. Ask only when a materially different scope, security boundary, financial consequence or destructive action requires the owner's decision.

Design review may be performed against the written criteria by the integration owner; it is not a claim of human approval. Finish all reachable implementation and objective verification even if human feedback is not yet available. Keep subjective visual/comprehension acceptance explicitly pending instead of inventing approval or abandoning the remaining work. A backend gap becomes a named dependency with alternatives, not an excuse to fabricate a metric or silently drop a page.

### Evidence levels

SPECIFIED is documentation. DESIGNED requires reviewable visual/interaction artifacts. IMPLEMENTED requires integrated source. VERIFIED requires observed scenarios on the actual surface. RELEASE READY requires complete integration gates. DEPLOYED requires separately authorized rollout and running lineage. HUMAN ACCEPTED requires recorded human feedback. No level automatically implies another.

Planning completion in this session means these linked documents and their checklists are internally consistent and validated. It does **not** promote R0–R7 or any UX page to implemented/verified/accepted.

### Release and rollback

No frontend rollout is performed by this planning task. Future release must preserve API/auth compatibility, in-flight operation recovery, immutable build identity, private ingress, current risk/latch/gate settings and host privilege separation. A UI rollback must not restore old financial state, down-migrate the database, replay commands or lose a pending operation's identity. The existing financial/exposure-aware deployment runbook remains binding, even for apparently frontend-only artifacts.

## 10. Risk and decision register

| ID | Risk / decision | Treatment / owner |
|---|---|---|
| D01 | Five destinations versus loss of specialist functionality | Fixed five-item navigation; full secondary page/action inventory; integration owner |
| D02 | Professional look versus decorative dashboard clutter | Content-first restrained tokens; core-page visual checkpoint; design owner |
| D03 | Simple status versus false safety claim | Impact summary plus independent facts/freshness; domain/page owner |
| D04 | Fewer clicks versus meaningful financial consent | Dedicated forms/reviews, unchanged server confirmation contract; command owner |
| D05 | Existing read DTO may not supply desired metrics | R0 field/provenance matrix; honest unknown or named prerequisite; data-contract owner |
| D06 | Large main component encourages conflicting rewrites | R2 boundaries and single shared integration owner before page fan-out |
| D07 | Existing deep links/query handling may be fragile | Inventory and browser route matrix before cutover; shell owner |
| D08 | Static screenshots can hide broken state transitions | Browser task evidence across success/failure/expiry/ambiguity; verification owner |
| D09 | Isolated fixtures mistaken for LIVE | Separate environment and visible fixture label; never substitute production data |
| D10 | Human approval unavailable | Record objective checks separately; keep human acceptance pending |
| D11 | Old docs imply frontend already accepted | Cross-link separate UX tracker; preserve historical backend/deployment receipts |
| D12 | Cleaner diagnostics remove evidence operators need | Keep deliberate technical details/export with sanitized copy behavior |

## 11. Definition of complete redesign

- [ ] UX00–UX16 are implemented, integrated and exercised; no current capability silently lost.
- [ ] Five-destination navigation and existing canonical deep links work on desktop/mobile.
- [ ] Overview answers the operating questions with real, independently scoped data.
- [ ] Common financial tasks use dedicated forms and readable reviews, not raw schemas.
- [ ] All backend-required financial confirmations, safety/ownership checks and ambiguity recovery remain intact.
- [ ] Password-only login/session/CSRF/sign-out behavior remains intact.
- [ ] Loading/empty/zero/stale/partial/unavailable/blocked/pending/ambiguous states pass their scenario checks.
- [ ] Light/dark, responsive, keyboard, focus, contrast and zoom gates are evidenced.
- [ ] Technical details remain accessible without dominating ordinary pages.
- [ ] Build/contract checks and actual browser workflows pass; no screenshot-only completion claim.
- [ ] User guide, affected contracts and operational release notes describe the new actual interface.
- [ ] No temporary scaffold, parallel old UI, dead renderer/action path or fake result remains.
- [ ] Package/page/phase tracker evidence is current; unexercised claims are explicitly pending.
- [ ] Human comprehension/visual feedback is recorded separately from automated verification.
- [ ] Rollout occurs only under its operational authorization, with separate post-release evidence and safe rollback prerequisites.
