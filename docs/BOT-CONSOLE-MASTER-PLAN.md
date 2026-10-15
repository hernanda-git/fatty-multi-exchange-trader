# Bot Management Console — approved master plan

Date: 2026-10-14. Owner approved the planning proposal and requested documentation, phase tracking, commit/push, and work to start. This approval covers repository development and publication, not provider mutations, test trades, production console rollout, latch release, changed risk limits, or credential substitution.

The subsequent instruction to complete everything autonomously extends authority to
the full implementation, publication and exposure-safe private console rollout.
It does not authorize test trades, incident-latch release, risk expansion or
enrollment of an authenticator on the owner's behalf. Current receipts and phase
states are maintained in the progress tracker, not inferred from this plan.

## Document map and status authority

- [Progress tracker](BOT-CONSOLE-PROGRESS.md): current work, dependencies, evidence and release checkpoints.
- [Capability and safety inventory](BOT-CONSOLE-CAPABILITIES.md): actual existing boundaries, missing support and clean-cutover requirements.
- [UI/API specification](BOT-CONSOLE-UI-API-SPEC.md): proposed interactions, endpoints, durable jobs, authentication and data contracts.
- Existing authority: [trading rules](TRADING-RULES.md), [production LIVE policy](PRODUCTION-LIVE-POLICY.md), [agent workflow](../AGENTS.md).

Specifications describe proposed behavior unless explicitly identified as inspected existing behavior. Approved design, source implementation, local verification, deployment, current provider evidence and a real trade are separate evidence tracks. The progress tracker must never label one as another.

## 1. Outcome and complete scope

Deliver one responsive operator console for tracing signals, managing positions/orders/protection, controlled recovery, configuration, notifications, account performance, and deployment operations. A read-only dashboard is a delivery phase, not the final accepted scope. Every action must have server-enforced permission, capability, ownership, evidence and outcome semantics; unsupported actions are visibly unavailable with a reason, never fake buttons or silent fallbacks.

Persistent account/environment and safety header; desktop sidebar and mobile navigation; search/filter/detail links and accessible keyboard operation. Screens:

| Screen | Required content and actions |
|---|---|
| Overview | Independent liveness, progress, provider availability, admission, protection, reconciliation and freshness; active incidents/jobs; pause and scoped emergency navigation |
| Signals/inbox | Original text/media, channel, timestamps, revision/edit evidence, realtime/catchup origin, freshness, classification; read-only analysis preview and annotations |
| Signal trace | Source to canonical signal, dispatch transitions, sizing, reservation, split intents, fills, protection and close; exact blocker, correlation IDs and evidence export |
| Trading desk | Supported contracts, fresh quote/account, direction, market/LIMIT, margin/leverage/quantity, liquidation/risk, SL/TP; fresh preview and approved new manual entry |
| Positions | Provider versus ledger, bot/manual/baseline ownership, quantity/price/margin/PnL, protection coverage; selected partial/full close, SL/TP/SL-to-entry and owned remainder cancellation |
| Orders/entry legs | Entry versus protective/reduce-only orders, split children, partial fills, client IDs, durable cancellation claims; exact-owned cancellation and GET-only reconciliation |
| Protection | Native identity/status/trigger/covered quantity versus actual fills, fallback binding/gates, stream evidence; supported repair/protection changes with readback |
| Trade management | Source and manual management requests, cancellation/close sequencing, truthful terminal/pending outcomes; existing-request reconciliation |
| Safety/recovery | Current faults versus durable latches, clock sample validity/latency, stream/readiness evidence; pause, proof, reviewed digest-bound recovery |
| Account/performance | Futures scope, current account, reservations, historic admission evidence, fees, funding when supported, net/gross realized/open PnL; bounded refresh/export |
| Services/deployment | Immutable revision, actual source/image identity, worker progress, logs, policy/migration checks; approved restart/release/rollback |
| Configuration | Desired/effective values, policy versions and pending changes; impact previews and approved changes; secrets remain write-only |
| Notifications | Outbox/delivery status and report schedule; health send, test delivery and eligible retry with duplicate-delivery disclosure |
| Audit/jobs | Actor, reason, normalized preview, approval, resource version, intent and readback; incident acknowledgement and sanitized export |
| Backups/maintenance | Integrity, retention and restore/reconciliation prerequisites; backup/verify and separately approved restore |
| Access/integrations | Roles/sessions, Telegram intake, provider binding and optional model fallback; session revocation and dedicated credential rotation |

No historical signal receives renewed freshness. Re-analysis is read-only. Trading an old setup requires a new explicitly manual command and fresh evidence. No exchange expansion is implied: Bitget production first; other venues only through verified capability/adapters, not a fictitious selector.

## 2. Architecture decisions

React + TypeScript frontend with Vite, existing FastAPI API/service, existing PostgreSQL. Same authenticated HTTPS origin serves compiled static assets and versioned API; no direct browser provider access. SSE provides sanitized updates with bounded polling fallback. No new trading engine or general-purpose workflow platform.

Reuse account/ledger loading, execution intents, risk/admission, source management, provider adapters and supported operations scripts. Extract a shared typed command layer for web and Telegram rather than invoke Telegram text parsers as a web API or maintain duplicate rules. Resolve all callers at cutover.

Reads and mutations are separate. Page loading/probes cannot run mutating preflight or change margin mode. Financial DTO values use decimal strings; backend Decimal is authoritative. Bounded read models prevent one browser tab from polling every contract/account repeatedly. Each observation records source, scope, time, known/unknown/stale outcome; an unavailable section does not erase independently successful sections.

Durable action jobs, session-bound expiring approvals and application audit live in PostgreSQL; existing financial intents remain authoritative, not copied into conflicting ledgers. A restricted operations executor handles a fixed allowlist of reviewed service/backup/recovery jobs. Web receives no Docker socket, unrestricted shell, arbitrary SQL, request composer, arbitrary path or arbitrary Git revision command.

## 3. Safety and action semantics

Preserve LIVE/LIVE/1 and independent default-closed operator/fallback/stream mutation gates. Keep existing risk caps unless separately approved. Exact split sizing, source guards, bot/baseline distinction, native FULL-position first-target TP, source TP1 half-close semantics, intent-before-POST and GET-only ambiguity reconciliation stay intact.

Each action follows select scope -> fresh evidence -> preview -> authorization/feasibility -> explicit confirmation -> durable claim -> final resource/gate recheck -> execute once -> provider/host readback -> verified outcome or reconciliation pending. Approval binds actor/session, account/environment, normalized action/parameters, policy and resource versions, evidence expiry and reason. Server rechecks everything; disabled buttons are not authorization.

A browser timeout or lost acknowledgement never authorizes another POST. Idempotency binds one request to one durable action; changed payload under the same key refuses. Multi-resource jobs report individual outcomes; mixed failure is not all-success. Cancellation of a UI job can only prevent unclaimed work, not undo an accepted provider mutation or abandon reconciliation.

Bulk scope defaults to bot-owned resources. Native protective orders, reduce-only closes, unrelated/manual orders and baseline exposure are not entry-cancellation targets. Account-wide management remains disabled until separately approved ownership and readback contracts exist.

Distinct emergency actions: pause new entries without stopping protection; cancel bot-owned waiting entries; close selected bot exposure reduce-only; flatten bot exposure using ordered cancellation/fresh read/close/readback; exposure-aware service stop. No overloaded panic button or unconditional resume switch. Recovery preserves exact eligible reason/binding/digest/freshness checks and refuses nonflat accounts when procedure is flat-only.

## 4. Security model

Viewer, Trader and Administrator roles; recovery/maintenance privileges separate from ordinary trading. One owner may hold multiple privileges but dangerous actions still require recent step-up authentication. MFA/passkey-capable identity, short server-side sessions, secure HttpOnly/SameSite cookies, CSRF protection, origin checks, bounded rate limits and backend resource authorization. Default private access; public exposure needs a separately reviewed HTTPS/auth/proxy decision.

Credentials never returned to browser, stored in action bodies, logs or exports. Rotation uses dedicated secret storage/binding verification; presence/status replaces plaintext. Sanitize exceptions and source/media rendering; exports must resist formula injection and secret leakage. Append-only application audit with controlled retention is not claimed tamper-proof against privileged database administrators.

## 5. Phase-by-phase delivery

| Phase | Work packages | Exit evidence |
|---|---|---|
| P0 Capability/safety specification | Enumerate actual adapter/caller support, gates, ownership and missing contracts; classify unsafe bulk/manual paths | Source-backed action matrix, explicit prerequisites, no parser-only support claims |
| P1 Interaction/API contracts | Screen flows, permissions, DTOs/freshness, jobs/approvals/events, scoped migrations and operational privilege design | Every action maps to UI, API, lifecycle, failure/recovery and observable acceptance |
| P2 Authenticated read console | Real auth/session/CSRF and compiled UI, account/trace/position/order/protection/latch/job/log views | Real browser proof; read interactions issue no provider mutation; stale/unknown truthful; auth negative cases |
| P3 Shared durable command layer | Migrate Telegram/web to typed shared commands, approval/job claims, resource versioning, locks and restart recovery | Duplicate click, crash and lost-response proofs with real isolated PostgreSQL; no duplicate provider mutations |
| P4 Trading/protection controls | Manual entries with same admission, exact cancel/close/SL/TP, partial close and source semantics | Ownership/fill race/protection/replay behavior proof; adapter-incomplete actions blocked honestly |
| P5 Recovery/operations | Guided approved recovery, entry pause/resume, services, backup/restore, policy/integration/deployment jobs | Nonflat/unknown/stale refusal; allowlist isolation; exposure-safe job/recovery outcomes |
| P6 Hardening/release acceptance | Accessibility/mobile, security review, docs/runbooks, complete contract tests, deployment/rollback acceptance | Complete requested scope; actual browser/API/DB proof; approved rollout source/policy/worker evidence |

Dependencies: P0/P1 contracts precede production APIs; auth precedes sensitive read exposure; durable jobs/shared commands precede financial controls; privileged isolation precedes host jobs. Independent read screens and UI components may proceed together once DTOs settle. One integration owner controls migrations/shared command interfaces; no tests against half-integrated changes.

## 6. Verification and acceptance scenarios

Permanent tests target behavior/invariants: cross-user/role access, CSRF and approval replay, changed input/resource/version after preview, stale account/clock/stream evidence, duplicate submit, fill/cancel race, latch at final entry fence, split remainder protection coverage, acknowledgement timeout, worker death after intent, reconciliation after restart, simultaneous Telegram/web actions, partial bulk outcome, source edit/expiry, nonflat recovery refusal, secret-safe logs/media/exports, service operations preserving protection, backup restore preventing ledger/provider divergence.

Observe actual browser desktop/mobile navigation, preview/confirmation/errors/live refresh/reconnect/expired session. Tests alone do not prove UI. Use isolated provider simulation only for local mutation behavior, clearly separated from live account evidence. Source compilation/image build do not prove running identity. No test trade/canary without explicit separate approval; final release records that natural live lifecycle may remain unobserved.

## 7. Required release deliverables

All screens/actions above implemented or explicitly prohibited under approved policy with explanations, not unimplemented placeholders. Shared command cutover and necessary migrations complete. Auth/operations threat model reviewed. Operator/trader/recovery/admin user guide, incidents and rollback runbooks, config/reference, acceptance receipts and tracker updated. Build allowlist excludes secrets and artifacts. Policy checker before separately approved rollout, backups/retained images, source identity and migrations/worker/provider evidence after rollout. Never automatically restore a database over later provider mutations.

## 8. Current boundaries

The last conversation observed recurring clock-unavailable admission after a short repaired-monitor PASS. This is historical context, not a fresh production read or resolved incident. The console must make faults understandable and recoverable but does not hide or automatically resolve them. Developing this console does not authorize recovery or broaden existing trading policy.

Work starts with completed specification artifacts and capability investigation. Tracker records actual findings and remaining code work; documentation-only commits must not claim a built console, authenticated API, verified adapter behavior or production deployment.
