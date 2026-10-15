# Bot Management Console: UI and API contracts

Date: 2026-10-14. Status: **phase 1 design deliverable; not an implemented console**. This specification covers the entire approved console, including capabilities that need new durable adapters. It does not authorize deployment, provider mutations, consumer shutdown, Telegram sends, latch release, risk changes, or access changes. Production remains LIVE/LIVE/1 under [PRODUCTION-LIVE-POLICY.md](PRODUCTION-LIVE-POLICY.md); all independent admission and mutation gates remain effective.

## 1. Evidence, architecture, and implementation boundary

Source baseline inspected: `ea0114e` on `origin/main`. Production was not probed for this specification. Source evidence and proposed contracts are deliberately separated:

| Existing source | Observed contract | Console consequence |
| --- | --- | --- |
| `src/fatty_trader/web/app.py` | FastAPI has only GET `/health` and GET `/health/telemetry`; both return the same sanitized health report | Every `/api/v1` endpoint below is proposed, not available today |
| `web/health.py`, `web/runtime_probe.py`, `worker_health.py` | Configuration, handler liveness, and worker progress/dependency readiness differ; web `orders_enabled` is null; required web readiness probes cover PostgreSQL, intake, analyzer, notification | Never use a green HTTP/configuration badge to imply dispatcher admission, monitor enforcement, or all-service health |
| `operator/health.py`, `operator/health_report_format.py` | Shared account/ledger meanings and report rendering; provider sections and ledger sections can independently be unknown; reporting never refreshes admission snapshots | Share normalized readers with Telegram; construct typed JSON rather than scrape report HTML |
| `operator/live_commands.py`, `operator/authorization.py` | Telegram sender/private/non-forwarded checks, independent mutation gate, in-memory five-minute confirmations | Existing Telegram authorization is not web login, durable approval, RBAC, or cross-channel idempotency |
| `operator/bitget_gateway.py` | `open_position` returns `operator open is not enabled`; close persists an intent before reduce-only submission; source owned-entry cancellation delegates to lifecycle; broad operator cancellation and direct protection placement do not provide the proposed job fence | Do not expose the parser/gateway wholesale; manual open is unsupported until a real integrated adapter exists; cancellation/protection require exact durable web-safe adapters |
| `storage/schema.py`, `storage/migrations.py` through 25 | Durable dispatches/transitions, entry/protection/close intents, fills, reservations, snapshots, source management, notification outbox, latches | Preserve these records and native states; console jobs are a separate control-plane layer |
| `storage/fallback_schema.py`, `verified_close_schema.py`, `bitget_baseline_schema.py` | Position epoch/environment ownership, exact close-to-reservation binding, immutable historical uncertainty baseline | Never infer current ownership from symbol alone or call historical retirement a verified close |
| `storage/bitget_live_latch_recovery.py`, `docs/OPERATIONS.md` | Scoped flat-baseline proof/apply, all mutation consumers stopped, exact eligible reasons, account/baseline/stream/digest binding | Recovery requires its real protocol, not a boolean toggle or automatic webhook call |
| `recovery_webhook.py` | Separate bearer-authenticated read-only inventory; broad recovery and latch release explicitly refused | Not a console mutation API; no POST proxy to it |

Architecture: React + TypeScript client served from the existing FastAPI origin; PostgreSQL durable read models, authentication, approvals, jobs, audit, and event outbox. Read adapters use existing normalized provider/ledger contracts. Typed command adapters shared by web and Telegram call existing execution/management/protection/recovery owners. **The web process must not become a second trading engine**: no browser sizing authority, duplicate signal routing, admission bypass, independent scheduler for trading, or direct provider POST. Existing workers remain authoritative for execution and reconciliation. Migrating Telegram onto shared durable commands is a clean cutover prerequisite for enabling corresponding web mutations; do not leave a less-safe alternate route.

The browser holds no provider, DB, Telegram, deployment, or recovery credentials. No arbitrary POST proxy, raw SQL interface, filesystem path input, Docker socket, host PID namespace, unrestricted shell, user-supplied executable, or arbitrary URL fetch exists in this contract. All operations use named schemas and resource identifiers.

## 2. Global interaction and truth model

### 2.1 Navigation and responsive behavior

Canonical routes: `/overview`, `/signals`, `/trading`, `/positions`, `/orders`, `/protection`, `/management`, `/recovery`, `/account`, `/services`, `/config`, `/notifications`, `/audit`, `/backups`, `/access`. Stable detail routes append a resource ID, not only a symbol; links preserve account/environment and return filters. Desktop uses a persistent grouped side rail (Observe, Trade, Operate, Govern), top account/venue/environment selector, freshness/connection indicator, search, and identity menu. At widths below 1024px the rail collapses; below 768px use a drawer and cards, while retaining every route. Do not hide safety facts or destructive actions behind horizontal table scrolling.

Tables use server cursor pagination and filters encoded in URL query parameters. Mobile cards expose identity, state, observation age, and essential financial values first; drill-down reveals remaining fields. Drawers for quick inspection link to independently addressable full details. Dangerous confirmation uses a full-height dialog on narrow screens, with preview evidence above the submit button. Back/refresh must not resubmit a job. Browser history never carries secrets, MFA assertions, or confirmation tokens.

Keyboard-accessible navigation, focus trapping/return, labeled inputs, visible pending state, and textual status accompany icons/colors. Financial decimal strings are formatted for display only; never calculate order quantities or financial rollups using JavaScript floating point. Display timezone is selectable; API timestamps and default audit display are UTC. Search/filter inputs are debounced; discarded read responses cannot overwrite a newer account/filter selection.

### 2.2 Independent statuses and freshness

Every panel labels `source`, `read_status`, `observed_at`, and age. Common read statuses are `ok`, `partial`, `unavailable`, `unknown`; freshness is `fresh`, `stale`, `unknown`. Null means unavailable/not observed, never zero. A successful zero count or empty provider list is shown only after a successful, scoped, complete read. A cached list after failed refresh remains visible as **last known**, with its original observation time and error badge. Missing, malformed, stale, or failed reads must never prove flatness, cancelled orders, protection, delivery, readiness, or successful mutation.

Separate status lanes: configured mode/execution flag; HTTP liveness; worker progress and dependency readiness; current entry-admission verdict and each gate; provider open state; ledger ownership; native protection proof; fallback registration versus runtime enforcement; durable latch state; current recovery proof. `orders_enabled: null` from today's web remains **unknown**, not disabled or enabled. Fresh GET account data is not a new admission balance snapshot. Existing web readiness uses at most 120 seconds of valid progress even when worker health configuration permits a larger age; keep that distinction in diagnostics.

Proposed policy defaults: account/position/order/mark observations become stale at 15 seconds, runtime/service observations at 120 seconds; ledger reads carry database snapshot observation time plus record update time, not invented provider recency. Action-specific execution freshness is stricter and is evaluated by the authoritative adapter. These defaults are versioned policy, not a claim about current runtime configuration. A preview expires after 60 seconds; an expired/stale preview cannot authorize new work. Source recency, clock bounds, stream heartbeat bounds, and provider admission rules remain those of the existing engine, not these display defaults.

No optimistic financial or operational success updates. Accepted work adds a job row; confirmed outcomes update read models only after evidence. Alerts link to precise position/order/intent/job/evidence; do not replace detail with a generic red banner. All buttons use the returned action capability: unavailable controls explain the missing adapter, privilege, evidence, gate, or policy instead of pretending to execute.

## 3. Screen-by-screen specification

All mutations below use the preview/approval/job flow in sections 6–8; mentioning a button is not claiming backend support.

### 3.1 Overview — `/overview`

Show provider equity/available/unrealized PnL alongside separately labeled ledger realized PnL/fees; actual provider positions and waiting entries; recent signals and dispatch outcomes; protection exceptions; unresolved intents/jobs; source/worker freshness; active latch scope/reason/time; effective mode and independent mutation flags. Never aggregate provider and ledger positions into one double-counted total. Cards drill into filtered tables. A current account read and last admission snapshot age are separate cards. Global containment and recovery links expose scoped previews; no one-click release, trade, close-all, or deployment. Filters: account, venue, environment, time range. Refresh is GET-only and preserves last-known evidence on partial failure.

### 3.2 Signals and Trace — `/signals`, `/signals/{message_id}`

Filters: channel/source, received-time range, intake state, analyzed/canonical classification, symbol/direction, revision, dispatch state, terminal reason, media presence, freshness. Rows show channel/message IDs, immutable revision hash, source receipt time, classification, canonical signal ID, and each venue dispatch independently. Detail timeline: received message/revision → sanitized text/media → analysis outcome → canonical entry/SL/targets → dispatch transitions → route/sizing/admission evidence → stable child intents → provider acknowledgments/fills → protection/management → notifications. Missing persisted analysis explanation is marked unavailable; do not fabricate a full analyzer trace from a canonical signal.

Media is authenticated, MIME/size allowlisted, served by opaque resource ID, never a raw host path; text is escaped, not injected as HTML. Revision switching is explicit. Source message links open safely without credentials. Read-only download exports the selected sanitized trace. Proposed reanalysis action creates a new recorded analysis run only; it must not reset source receipt time, reactivate expired dispatches, overwrite a revision, or automatically authorize trading. Enabling it requires analyzer integration and an explicit no-dispatch versus admitted-dispatch policy; until then capability is unsupported.

### 3.3 Trading Desk — `/trading`

Controlled form: account/environment/venue, supported contract, LONG/SHORT, input margin, policy-pinned leverage/margin mode, market or permitted LIMIT entry, SL, TP plan, operator reason. Contract step/minimum/notional/max data and observation age accompany inputs. Show computed normalized quantity, planned margin/notional, projected liquidation/SL safety, route, every admission gate, and exact leg identities from the server. There is no browser-created provider payload. For source trace previews preserve the approved recent passed-entry 25% market / 75% working LIMIT rule; lot-infeasible split fails closed, not full-market fallback. Source routing uses original receipt time and a single sizing/route observation. Automatic first-target TP covers the **full actual position**; targets are not an invented allocation scheme.

Current operator open adapter is unsupported; the UI displays that reason and cannot submit. A future enabled manual command must use the canonical entry admission/reservation/intent/protection lifecycle, pinned LIVE isolated 20x policy and effective conservative cap, not call `open_position` and treat its error dictionary as success. Distinguish manual market/LIMIT policy from source recency routing; do not silently apply a source-only rule to manual trades. Editing any field invalidates preview/approval. There is no force, ignore-pending-orders, gate override, or duplicate dispatch button.

### 3.4 Positions — `/positions`, `/positions/{position_key}`

Filters: provider open/ledger active/history, venue/account/environment, symbol/side, ownership (`bot`, `account_baseline`, `external`, `unresolved`), protection state, drift, opened/closed time. Show provider size, entry/mark/liquidation, leverage/margin mode, unrealized PnL, native SL/TP level and IDs, bot-owned residual, and separate ledger quantity. Key includes provider position epoch and ownership root where available; symbol is only a label. Detail links to source/dispatch, admission reservation/snapshot, entry legs/fills, exact close bindings, protection evidence, management, jobs, and audit.

Actions: preview reduce-only close, owned SL change, permitted TP change, readback reconciliation. Bot ownership is default scope. Baseline/external/unresolved exposure cannot be mutated through bot-management actions; a separately approved account-wide emergency operation must explicitly enumerate such exposure and have a validated adapter. Full close and TP1-specific source half-close remain different commands. Position disappearance alone after an ambiguous close is not proof of that close's financial attribution. Detail exposes pending remainder and cancel/fill race risk before closing.

### 3.5 Orders and Intents — `/orders`, `/orders/{intent_id}`

Tabs: working provider orders, durable intents/entry legs, plan protection, fills, history. Filters: role, provider versus intent state, symbol/side, dispatch/root, entry leg/order type, ownership, cancellation claimed/terminal flag, ambiguity, update time. Show exchange client order ID, provider ID, requested/acknowledged/filled/residual quantities and prices, stable root/child identity, LIMIT price, reservation binding, timestamps, cancellation claimant, and `provider_terminal` independently. Unknown ownership stays visible, not auto-imported as bot-owned.

Exact-order cancel preview lists only the matched order and effect on protection/remaining entry. Owned-remainder cancel lists exact ENTRY client IDs, never native SL/TP, reduce-only exits, manual orders, or symbol-wide cancel-all. Protective cancellation is unavailable unless a dedicated approved replacement/containment workflow proves continued protection. Bulk operations are explicitly enumerated child resources, each versioned; no hidden `cancel_all` call. Plan orders are not assumed to be covered by the regular pending-order endpoint. Intent detail links to fill evidence, dispatch, protection, management cancellation claim, reconciliation audit, and parent job. A claimed cancel offers **reconcile**, not **retry cancel**.

### 3.6 Protection — `/protection`, `/protection/{position_key}`

Matrix shows actual provider size versus observed native SL/full-position first TP coverage; native proof status and exact identities; persisted capability (`UNKNOWN`, `VERIFIED`, `UNSUPPORTED`, `FAILED`); fallback registration/position epoch/environment; fresh mark and worker/stream evidence; independent mutation flags; liquidation safety and mismatch reasons. Never label a registration or price alone as enforced/protected. Filters: symbol, evidence status, missing SL/TP, coverage mismatch, fallback state, stream state, stale evidence.

Actions: read-only diagnosis/readback, preview supported exact-owned SL modification, supported full-position TP repair/native recovery, fallback registration or containment only through their canonical durable adapters. Existing direct operator protection placement is not sufficient for web enablement. SL replacement must preserve retained TP ID and validate owned root/residual under strict pre/post readback. TP1 booked requests remaining entry SL only after confirmed reduction. Native error `43011` is failed/degraded, not proof of unsupported capability; failed/missing plan reads remain unknown unless independently sufficient strict evidence exists. Unsupported native/fallback actions explain why; no unverified fallback activation or protection removal shortcut.

### 3.7 Management — `/management`, `/management/{update_id}`

Filters: source/revision, symbol, action, state, claimant, age, pending/failed. Display original unambiguous source instruction, dedup tuple, mutation gate/freshness, exact cancellation claims and terminal readback, fresh post-cancel position observation, close/stop intents, partial outcomes, notification truth. Action labels preserve `TP1_BOOKED`, `TP_BOOKED`, `SL_TO_ENTRY`, `CLOSE`:

- CLOSE: prove all owned remaining entry cancellation, refresh position, close full fresh size reduce-only, require flat readback.
- TP1_BOOKED: same cancellation first; lot-safe half of fresh size, confirm reduction, request SL at remaining entry. This is not automatic TP behavior.
- TP_BOOKED (generic/later targets): cancellation only, never another close allocation or SL authorization.
- SL_TO_ENTRY: existing stop behavior; no implied waiting-entry cancellation.

Show `cancelled-flat`, `entries-cancelled`, `reconciled`, `reconciliation-pending`, and `failed` without renaming them all executed. Relay detection/queued is not execution confirmation. Proposed reconciliation resumes only existing claims using GET evidence; it cannot synthesize a source instruction, change message revision, replay a claimed POST, or convert a flat account plus an earlier close intent into cancellation-only success. Queue ordering/leases belong to the source-management worker. Any new manual management action has its own typed operator command, not a fabricated source message.

### 3.8 Recovery and Safety — `/recovery`

Show global/venue/stream latches independently with exact reason/latched/updated times; unresolved ownership/intents/reservations; audited baselines and unresolved-history exclusions; current provider/ledger drift; last observed runtime conditions separately from approved proof. Filters: scope, reason, ambiguity type, ownership, proof status, baseline/account.

Read-only inventory and reconcile-evidence requests are separate from apply. Historical baseline retirement preview lists exact candidate record IDs/digest/cutoff/account/environment/approval and preserves prior snapshots; it does not edit fills or prove closure. Flat-latch recovery wizard binds existing audited baseline, account UID, exact eligible latch rows/reasons, all-consumers-stopped attestation from the operations runner, full authenticated flat inventory, ready ledger, fresh bounded clock evidence where required, observe-only authenticated V2 public/private stream and pongs. Dry-run digest and fresh proof are displayed and bound to apply. Apply re-runs canonical proof and locks, never trusts the old preview. Unknown/global reasons, nonflat account, stale proof, changed rows, unbaselined commitments, missing baseline/consumer-stop evidence refuse release.

No automatic broad recovery, boolean latch switch, fake successful webhook proxy, or action to mark an unknown intent filled/cancelled by hand. Post-apply show exact released rows, audit links, resume authorization, and full runtime verification evidence; release is not a claim of future order readiness. Keep protection for existing exposure; flat-only recovery cannot justify stopping protection on a nonflat account.

### 3.9 Account and PnL — `/account`

Tabs: current authenticated provider account, positions/uPnL, historical ledger fills/PnL/fees, immutable admission snapshots/reservations, external/baseline reconciliation. Filters: account/environment/product/margin coin, time range, symbol, source/ownership, fill/intent. Provider equity/available/locked/isolated/cross margin and uPnL show source and successful GET completion time; historical ledger realized PnL and fees show coverage/completeness. Show gross realized PnL and fees separately; compute net only when fee currency/conversion/semantics are known, with formula and provenance. No double subtraction or unlabeled mixing with current equity. Unknown fee valuation makes net unknown, not zero fee.

A reconciliation difference is diagnostic, not a fabricated missing fill. Excluded baseline history remains visible and labeled `unresolved-history-not-verified-closure`. No account transfer, withdrawal, or wallet action is in scope. CSV export uses a scoped server export job and authenticated expiring artifact, includes observation/coverage metadata, and escapes spreadsheet formulas in text fields.

### 3.10 Services and Deployment — `/services`, `/services/{service_id}`

Show allowlisted service topology/dependencies (PostgreSQL → migrate → init → application), requested/observed release SHA plus actual image/source digest, container/process running and healthcheck verdict, worker-owned progress/attestation, restart history, bounded redacted logs, resource observations, effective mode/gates, host-run checks and their timestamps. Current web probes cover only the named required workers; all-service telemetry needs dedicated evidence adapters. Host Git SHA is not proof of container source. Binance-disabled topology does not imply live trading support.

Read-only logs use service ID/time/cursor, not paths or shell text. Proposed restart/stop/start/deploy/rollback commands select only reviewed service/release IDs. Preview shows impact/dependencies, active exposure, monitor/close recovery continuity, maintenance approval, LIVE policy validation, expected artifact digest, and rollback constraints. Deploy/rollback requires immutable approved artifacts and runner verification of rendered configuration and actual runtime; no arbitrary branch, Compose fragment, environment override, command, or SSH input. Application release rollback is distinct from destructive DB restore; no automatic old DB rollback after provider mutation. Stop-all is not a generic incident solution. Operations results can be successful at a runner step while readiness remains degraded; job output must preserve both.

### 3.11 Configuration — `/config`

Tabs: effective per-service nonsecret values, pinned policy, editable draft, change history, secret references. Each field reports source (pinned/environment/default/secret reference), requested and effective values, revision, validation, restart/apply requirement, and drift. Never return raw environment or secret dumps. LIVE/LIVE/1, positive cap/approval/freshness/clock/ownership/protection constraints and isolated 20x policy cannot be relaxed through generic settings. Independent source/operator/fallback/stream gates remain explicit.

Draft editor validates an allowlisted typed config schema, produces a field-by-field diff/impact preview and policy version, and requires approved apply via appropriate owner/runner. Saving draft is not applying runtime. Sensitive risk/gate changes require owner approval and step-up; unsupported policy changes have no execute path. No arbitrary key/value patch. Secret replacement is write-only using the flow in section 5; blank means no change unless an explicit typed revoke command is selected. Never prefill hidden secrets with masked dummy strings. Validation errors identify the field but never echo a secret value.

### 3.12 Notifications — `/notifications`, `/notifications/{outbox_id}`

Separate trading-event outbox from direct periodic six-hour health report/timer evidence. Filters: event kind, dedup key, pending/leased/retry/sent/terminal-failed, attempt count, job/source/intent, date. Detail shows sanitized rendered content, provider acceptance/DB acknowledgment where known, token-fenced attempt timeline, next attempt, lease, and linked actual trade outcome. Sent relay is not a trade fill. Existing outbox is at-least-once across acceptance/DB-ack crashes; duplicate risk is visible.

Preview test-send identifies exact destination (redacted where appropriate), content class, and explicit permission. Resend/requeue is a durable administrative delivery command against exact outbox/version, not deletion of dedup evidence or replay of a trading action; eligibility excludes active lease and unresolved acceptance unless duplicate delivery is explicitly accepted and independently approved. Existing terminal-failed rows remain audit history. A new delivery attempt retains original notification identity and prior attempt facts. Quiet hours/routing settings use versioned typed config. Secrets/token reads are impossible.

### 3.13 Audit and Jobs — `/audit`, `/audit/jobs/{job_id}`

Tabs: command jobs, approvals, security/access events, domain mutation/evidence history, export artifacts. Filters: actor/channel, account/environment, action/resource, job state, approval reference, request/correlation IDs, time. Timeline distinguishes preview, approvals, enqueue, worker claims/checkpoints, provider intent claim/POST boundary, acknowledgment, readback, outcome, notification, and operations verification. Show requested versus normalized arguments, policy/resource versions, safe before/after evidence, cancellation reason and partial outcomes. Redacted audit has no raw secret or MFA data. Job detail exposes ambiguity and safe next command; no generic retry button on mutation.

Approvers can approve/reject exact digest-bound pending jobs after examining the preview and step-up. A requester cannot self-approve a two-person operation; changing scope invalidates approval. Cancellation is a command with the race semantics below, never a hidden browser abort. Exports are scope-checked and audited; failed/denied commands are searchable without revealing resource existence to an unauthorized caller.

### 3.14 Backups — `/backups`, `/backups/{backup_id}`

Inventory lists opaque backup ID, creation time, size, checksum, encryption/key reference, schema/release versions, retention, verified readability/restore-drill evidence, and known relation to live provider mutation time. No backup download unless separately granted because it can contain messages and operational data; secrets must be excluded or encrypted under policy. Backup creation/verification use allowlisted runner jobs; a successful dump is not proof of a restorable release.

Restore wizard shows exact artifact digest, destructive target/account/environment, existing/new schema compatibility, worker-stop/protection plan, post-restore provider reconciliation/no-replay plan, and two-person owner approval. Restore has no automatic fallback and cannot assume an old DB corresponds to the provider after live changes. Apply uses fixed runner operation and backup ID, not filename/shell text. Retention deletion is also explicit and audited; legal/audit hold prevents deletion. No source implementation is claimed for console backup/restore jobs.

### 3.15 Access — `/access`

Owner-selected password-only cutover supersedes the original identity-provider/enrollment proposal. Access lists the one shared scoped owner, owned sessions and revocation/policy evidence; no password/verifier, invitations, MFA setup, role grants or external identity links. Session revocation is authenticated and CSRF-protected. The configured Telegram operator can use only an already authenticated password-session bridge and cannot create login or digest consent.

## 4. Proposed read API and DTOs

### 4.1 Wire conventions

Same-origin prefix `/api/v1`; all endpoints authenticated except a deliberately minimal internal liveness endpoint. Existing `/health` endpoints keep their current contract for internal probes, with network/proxy policy preventing unauthenticated external exposure; they are not the browser authorization mechanism. Versioned OpenAPI schemas are the TypeScript generation source. Reject unknown mutation fields, unsupported enum values, cross-account references, and nonfinite/invalid decimal inputs. IDs are UUID/opaque strings; Telegram big integers are decimal strings; quantities/prices/money are canonical decimal strings with currency/unit metadata; instants RFC3339 UTC. Never serialize Decimal through binary floats.

Read envelope: `{api_version:"v1", request_id, server_time, scope:{account_id,venue,environment}, snapshot_id, event_cursor, data, evidence[], warnings[]}`. `EvidenceDTO` has `{id, source:provider|ledger|worker|runner|configuration, read_status, observed_at|null, record_updated_at|null, age_seconds|null, stale_after_seconds|null, freshness, complete, error_code|null}`. Age is calculated against server time; monotonic worker progress is converted to age at collection, not treated as a Unix timestamp. The snapshot cursor and data are coherent for persisted read-model transactions; provider observation collection is not an atomic provider/DB snapshot and carries its individual times.

Lists wrap `data:{items, next_cursor|null, has_more, total|null}`. Default limit 50, maximum 200; allowlisted sorts use stable `(sort_key,id)` tie-break. Cursor is opaque, scope/filter/snapshot-bound; changing filters starts a new page. Missing totals are null, not zero. Detail includes `resource_version` and safe `links` with canonical resource IDs. GET uses ETag/If-None-Match, private/no-store for sensitive account/security data, and no-cache revalidation where safe. Collection `from`/`to` UTC ranges have a policy maximum; exports handle larger authorized ranges. No client-selected database columns, joins, predicates, or SQL order expressions.

`CapabilityDTO`: `{action, support:available|gated|unsupported|read_only, reasons:[{code,message}], required_role, required_scopes, danger_tier, gates:[{name,verdict:pass|fail|unknown,evidence_id}], policy_version, adapter_version|null, preview_allowed, enqueue_allowed}`. Available means an integrated, reviewed adapter exists and current requirements pass; it is not a promise execution will pass fresh checks. Gated means the implementation exists but permissions/gates/evidence prevent execution; unsupported means no safe integrated implementation. Read-only identifies diagnostic operations with no apply path. Never promote an existing unsafe gateway method simply because a method name matches a planned action.

### 4.2 Read endpoint catalog (all proposed)

| GET route | DTO / filters and links |
| --- | --- |
| `/session` | `SessionDTO`: principal ID/display name, roles/scopes, MFA/step-up timestamps, idle/absolute expiry, current policy version, CSRF token; no bearer credential |
| `/actions` | CapabilityDTO list, filtered by resource/scope; authoritative render hint, never sole execute authorization |
| `/overview` | Independently evidenced account, worker, admission, protection, latch, queue summaries and detail links |
| `/signals`, `/signals/{message_id}` | `SignalTraceDTO`: channel/message/revision/received state, canonical setup, media refs, dispatch IDs, persisted trace with gaps |
| `/signals/{message_id}/media/{media_id}` | Authenticated safe bytes; no raw filesystem locations |
| `/dispatches`, `/dispatches/{id}` | `DispatchDTO`: source/revision/exchange/native state/terminal reason, claim/lease/attempts, transitions, intent/admission refs |
| `/positions`, `/positions/{position_key}` | `PositionDTO`: provider identity/epoch/side/size/economics, ledger refs, ownership classification and proof, residual, protection/evidence refs |
| `/orders`, `/orders/{intent_id}` | `OrderIntentDTO`: role/native state/leg/type, stable IDs, requested/ack/fill fields, terminal/cancel flags, dispatch/root/reservation, provider order observation |
| `/fills`, `/fills/{id}` | `FillDTO`: provider fill ID, exchange/client order binding, quantity/price/fee/currency/realized PnL/time/source and attribution confidence |
| `/protection`, `/protection/{position_key}` | `ProtectionDTO`: native coverage/proof, capability states/environment, fallback epoch/registration/enforcement, observed stream/monitor facts |
| `/management`, `/management/{id}` | `ManagementDTO`: dedup tuple/action/native state/claim, exact cancel intents, fresh position observation, close/SL refs, truthful terminal meaning |
| `/recovery` | `RecoveryDTO`: latch rows/version, unresolved intents/reservations, baseline receipt refs, current observations, proof status and refusal reasons |
| `/recovery/baselines`, `/recovery/baselines/{id}` | Immutable historical cutoff/digest/account/approval/provider evidence and exact retired-record refs; not verified closure |
| `/account`, `/account/admission-snapshots`, `/account/reservations`, `/account/pnl` | Current provider values separate from immutable admission/reservation and time/ownership-scoped fill aggregates; explicit calculation coverage |
| `/services`, `/services/{id}`, `/services/{id}/logs` | `ServiceDTO`: topology/runtime/progress/health/source digest/config revision and bounded sanitized log cursor |
| `/releases` | Allowlisted immutable signed/approved artifact IDs, digest/schema compatibility; not arbitrary repository refs |
| `/config`, `/config/history`, `/config/drafts/{id}` | `ConfigDTO`: typed field schema, redacted effective/requested values, revisions, policy pins, diff, apply status |
| `/notifications`, `/notifications/{id}` | `NotificationDTO`: event/dedup/state/attempt/lease/ack facts, sanitized preview, source/job refs |
| `/jobs`, `/jobs/{id}`, `/jobs/{id}/steps` | `JobDTO` and ordered durable steps/evidence refs; scopes checked at every read |
| `/approvals`, `/audit` | Digest-bound approval records and append-only redacted audit entries with filter/cursor |
| `/backups`, `/backups/{id}` | `BackupDTO`: checksum/size/schema/release/encryption/retention/verification and restore eligibility |
| `/access/principals`, `/access/sessions`, `/access/policy` | Scoped identity/session/role/MFA metadata and versioned policy; admin-only except self-session subset |
| `/artifacts/{id}` | Authorized artifact metadata/download; opaque IDs, expiring download permission, no traversal/guessable host paths |
| `/events` | Authorized SSE stream, described in section 9 |

Native states are retained, not translated into one misleading generic status. Important mapping: SQL live intents include `staged` after migration 25 although `ORDER_INTENT_STATES` in `storage/schema.py` does not include it; the future DTO must admit real stored `staged` and implementation must resolve that source validator/schema gap rather than hide rows. Dispatch transitions use existing domain state-machine values. Regular orders and live intents have different state conventions; DTO exposes `state_domain` plus `native_state`.

## 5. Authentication, authorization, and secret handling (proposed)

Current owner directive: password-only sign-in, with no username, passkey, invitation, MFA/OTP or external identity provider. POST `/api/v1/auth/login` accepts `{password}` and verifies a salted scrypt hash from a private read-only-mounted file. Missing/malformed configuration refuses login; there is no fallback password. Plaintext is never committed, logged, exported or returned. Deploy on the existing private localhost tunnel or a separately reviewed HTTPS origin.

Opaque server session in `__Host-console_session` (`Secure`, `HttpOnly`, `SameSite=Strict`, `Path=/`, no Domain), or `console_session_local` only for explicitly permitted localhost HTTP. Rotate at login and reviewed digest consent. Idle expiry 30 minutes and absolute expiry 12 hours remain. Mutations require current session, exact Origin, JSON and synchronizer CSRF; no credentialed wildcard CORS. Logout/revocation invalidates protected reads, SSE/downloads and pending authority. POST `/api/v1/auth/consent` accepts exactly one `{preview_digest}` or `{job_id}` and binds an expiring, consumed-once grant to the current password principal/session/action/scope/digest without another auth prompt. Old WebAuthn/enrollment/OIDC routes are removed. Login and consent are rate-limited; passwords/tokens/verifiers never enter logs, URLs or browser storage.

Roles are additive only by explicit assignment and resource scope: `viewer` read; `operator` approved bot-owned trading/management operations; `risk_owner` risk/gate/baseline/latch policy approval; `ops_admin` allowlisted services/releases/backups; `access_admin` identity/session administration; `owner` scoped final approval. Operations privilege never implicitly grants provider-trading privilege, and access administration does not grant self-approval. Deny by default by account/venue/environment/action/resource. All approvals and execution re-evaluate active principal, role/scope, policy revision, session/step-up validity, and adapter gate; UI checks are not security. Reject cross-account IDs even if their UUID is known. Revoke old approvals when the approver loses relevant authority.

Secret values are write-only and never appear in ConfigDTO, previews, errors, audit, job args, events, exports, or command output. Replacement form POST `/secret-stages` accepts a typed allowlisted secret kind and encrypted transport value into a dedicated secret service, returns short-lived opaque stage ID/version and redacted metadata only. Draft/preview stores the stage reference, bound to actor/account/secret kind, not plaintext. The secret service atomically consumes the stage on an approved config apply; expiry/revocation invalidates preview, and unused stages are securely destroyed. At-rest encryption and key/runner access are outside the web DB role; no key material in the application repository. A generic jobs dispatcher cannot read secret values. Rotation/revoke never echoes a last-known value or secret suffix as a convenience. Unsupported secret-manager integration keeps secret changes disabled; a masked field is not an implementation.

`policy_version` identifies immutable authorization, confirmation, risk pins, freshness limits, action schema, and rollout gates; each preview/approval/job records it. Effective service config uses distinct `config_version`. A changed relevant policy/config/adapter/resource invalidates pending authority and requires new preview. Policy change itself uses owner-approved versioned apply; it cannot weaken fixed production rules through freeform input.

## 6. Typed actions, preview, danger tiers, and approvals

### 6.1 Action schema and endpoints

POST `/previews`: `{action, scope, target_refs:[{kind,id,resource_version}], arguments:<action-specific object>, policy_version, reason, approval_reference|null}`. It validates read-only, authorizes preview, resolves exact targets/ownership, performs only GET/read queries, and returns `PreviewDTO`. **Do not call execution `preflight()` for a read preview**: existing Bitget preflight can prepare margin mode and is not GET-only. Implement a pure calculation/read adapter over existing rules, then re-run canonical mutating preflight at approved execution.

`PreviewDTO`: ID, schema/adapter/policy/config versions, normalized arguments and their digest, target refs/versions, account/environment, exact planned effects and exclusions, ordered irreversible boundaries, fees/slippage uncertainty, gates with evidence ages, warnings/refusals, danger tier, confirmation phrase, approval requirements, created/expiry timestamps, preview digest. Read-only previews do not reserve margin, create provider intent claims, change source freshness, stop consumers, send notifications, or release latches.

Named action catalog and minimum schemas:

| Action | Required typed arguments / target | Enablement and completion boundary |
| --- | --- | --- |
| `trade.open` | contract ID, direction, margin decimal, permitted entry kind/limit price, stop decimal, target decimals; exact account | Unsupported today; integrated canonical sizing/admission/reservation/intent/protection adapter required; acceptance/fill/protection remain distinct |
| `position.close` | exact owned position key/epoch/root; mode `full` | Stable reduce-only CLOSE intent and authoritative readback; no new quantity from stale client snapshot |
| `entry.cancel_owned` | exact ENTRY intent IDs + management/command cancellation ownership | Persist every claim before cancel, prove terminal by readback; protective/external IDs excluded |
| `order.cancel_exact` | exact regular order ID/client ID, ownership/role | Durable claim and provider readback adapter required; no symbol/all overload |
| `protection.set_sl` | exact position/protection roots and retained TP ID; requested stop decimal | Canonical strict owned modification/normalization, durable intent and readback; no direct placement bypass |
| `protection.set_tp`, `protection.repair_native`, `protection.register_fallback` | exact position epoch/root, requested level or recorded plan/capability refs | Adapter-specific full-position coverage/ownership/gate proof required; unsupported until integrated |
| `intent.reconcile`, `management.reconcile`, `protection.observe` | exact existing intent/update/position refs | GET-only provider reconciliation; ledger/audit updates permitted only from real readback; no new provider POST |
| `signal.reanalyze` | immutable message/revision, explicit analysis-only policy | Unsupported until recorded-run/no-replay analyzer adapter exists |
| `safety.contain` | exact allowlisted latch scope + incident reason | Durable audited containment; never changes LIVE/LIVE/1 or automatically releases another gate |
| `baseline.preview_retire`, `baseline.apply_retire` | exact candidates/cutoff/digest/account/approval | Canonical immutable unresolved-history retirement; no synthetic fills/verified closure |
| `latch.prove`, `latch.release_approved` | baseline ID, exact latch rows/reasons, dry-run digest, maintenance approval | Canonical scoped flat-baseline protocol and fresh proof; ops quiescence required, no broad release |
| `notification.test`, `notification.requeue` | approved destination/content kind or exact outbox ref + duplicate-risk acknowledgment | Delivery-only adapter; preserve dedup/attempt facts, never replay trade |
| `config.save_draft`, `config.apply` | typed diff or immutable draft ID + secret-stage refs | Schema/policy version validation, approved owner/runner apply and effective readback |
| `service.start`, `service.stop`, `service.restart` | exact allowlisted service set + maintenance plan ID | Runner operation with dependency/exposure/close-protection checks |
| `release.deploy`, `release.rollback` | immutable release ID/digest, target service set, maintenance plan ID | Runner policy validation, migrations/compatibility and observed source/readiness evidence |
| `backup.create`, `backup.verify`, `backup.restore`, `backup.delete` | target or exact backup ID/digest + maintenance/retention policy | Runner-only scoped operations; restore requires reconciliation/no-replay plan |
| `access.invite`, `access.change_roles`, `access.disable`, `session.revoke` | principal/session ref + exact scope/role diff | Identity adapter + last-owner protection, effective policy readback |
| `report.export` | allowlisted report kind, filters, format | Read-only collection with durable artifact result, access/redaction/retention policy |

All catalog entries are proposed bindings, not proof of supported runtime. Capability catalog explicitly records missing adapters. Bulk close/cancel/deploy is an enumerated parent with exact typed children and per-child outcomes, not an `all=true` provider command. Emergency account-wide operation, if implemented, is a distinct owner-approved schema listing non-bot resources; normal bot commands never inherit account-wide authority.

POST `/jobs` accepts `{preview_id, preview_digest, confirmation_phrase, reason, approval_reference, policy_version}` plus `Idempotency-Key` and `If-Match` for the preview version. It returns 202 + `JobDTO` + Location after durable transaction commit only. Submitted arguments come exclusively from immutable preview; no overrides. POST `/jobs/{id}/approve` and `/jobs/{id}/reject` accept exact digest/version/reason and step-up binding. POST `/jobs/{id}/cancel` has the semantics in section 8. There is no generic `/execute`, command text field, SQL, provider URL, shell, or force argument.

### 6.2 Danger confirmation protocol

Tier D0: read/analysis-only operations and exports use the current password session. Tier D1: low-risk durable administration requires current session and explicit review. Tier D2: provider mutations, containment, notifications and routine maintenance require fresh digest-bound session consent, explicit reason/approval reference, exact financial confirmation phrase and effect review; no second password/factor prompt. Tier D3 retains the distinct-approver financial policy and is unavailable to a sole shared identity; no synthetic approver or silent tier downgrade. Adapter and ownership gates, maintenance/proof bindings and GET-only ambiguity recovery remain unchanged.

Preview shows: LIVE/account/venue/environment; actor; exact action; normalized financial quantities/levels and affected resource IDs; bot/baseline/external ownership; current versus proposed state; what is explicitly not touched; exact gates/evidence ages; potential cancellation/fill races; irreversible steps; ambiguity/readback/no-retry policy; operations continuity and rollback limitations. Backend generates exact case-sensitive phrase, e.g. `CLOSE BTCUSDT LIVE <short-preview-id>` or `RESTORE <backup-id> LIVE <short-preview-id>`. Phrase is digest-bound and cannot be guessed from symbol alone. Require scrolling/access to all warnings, explicit acknowledgment of listed risks, and typing the phrase; neither a browser checkbox nor typing substitutes for backend MFA/approval.

Step-up assertion is server-bound to principal/session/preview digest/action/scope, expires after five minutes, and is consumed for that authorization only. Approval is durable, single-use for the exact job digest, and expires after at most five minutes before worker start; a queued job that misses its window returns to `awaiting_approval` and requires a fresh preview if its 60-second source preview/evidence is obsolete. No worker refreshes consent silently. Background execution after the validated start can finish readback of its claimed mutation even if UI/session expires; every new irreversible child step rechecks the action's policy and approval boundary. Newly revoked authority stops unclaimed effects, not necessary readback/containment by the system's own preauthorized safety owner.

## 7. Durable jobs, existing states, and completion semantics

`JobDTO`: `{id, action, scope, initiator, channel, target_refs, normalized_arguments_redacted, argument_digest, preview_id/digest, policy/config/adapter versions, danger_tier, state, phase, progress:{completed,total|null}, approval_refs, created_at, updated_at, resource_version, steps[], effects[], result|null, error|null, reconciliation_required, cancellation:{requested_at|null,accepted,reason|null}, links}`. Progress counts known steps, not invented percentages/fills. Each effect links existing dispatch/intent/management/reservation/notification IDs and describes requested/submitted/observed outcomes separately.

Job states (proposed, durable and distinct from trading states):

- `awaiting_approval`: immutable request persisted, no execution claim; approval/preview expired or incomplete.
- `queued`: authorization complete and eligible for a worker claim; still no provider mutation asserted.
- `running`: fenced worker owns an active step; phase distinguishes prechecks, intent-claim, submission, readback, operations apply.
- `reconciliation_pending`: a claimed or ambiguous irreversible effect needs evidence; nonterminal, GET-only recovery of that effect.
- `succeeded`: requested action's documented completion predicate proved, with evidence and truthful per-resource result.
- `failed`: proved rejection/pre-mutation failure or definitively unsuccessful action; no unaccounted mutation ambiguity.
- `partially_succeeded`: bulk children have mixed proved terminal outcomes; no unresolved child ambiguity. If any child is ambiguous, parent remains reconciliation_pending and exposes proved partial results.
- `rejected`: approver or fresh authorization/policy/resource precondition denies the unclaimed operation; includes reason.
- `expired`: no irreversible claim and consent/preview window exhausted without renewed authorization.
- `cancelled`: no new effects remain authorized and every already-claimed effect is accounted for; does not imply reversal or no partial effect.

Allowed spine: awaiting_approval → queued/rejected/expired/cancelled; queued → running/awaiting_approval/rejected/expired/cancelled; running → succeeded/failed/partially_succeeded/reconciliation_pending/cancelled; reconciliation_pending → running for readback only → terminal or reconciliation_pending. A known canceled-before-POST request cannot later become queued. Terminal result is immutable; subsequent observation is a separate reconciliation record/job, not rewrite of audit history. Waiting is not failure; HTTP request disconnect/worker lease loss is not cancellation. Successful job creation/approval is never successful trade execution.

Mapping to existing domain records:

| Existing state/evidence | Job interpretation |
| --- | --- |
| Dispatch QUEUED/PREFLIGHT/SIZED/VALIDATED or staged ENTRY | Admission/entry lifecycle progress, not fill or completed trade |
| requested intent persisted / cancel_post_claimed true | Irreversible boundary conservatively claimed; crash may have submitted, so reconcile only |
| acknowledged/submitted intent | Provider acknowledgment/submission recorded; job still needs action-specific readback |
| partially_filled / working LIMIT | Actual partial exposure/owned remainder; never call entry complete/position fully protected without coverage proof |
| filled | Fill evidence exists, but entry completion additionally requires configured protection/admission outcome |
| unknown or existing unresolved POST claim | reconciliation_pending; no new POST on retry, restart, new idempotency key, or lease steal |
| cancelled/rejected + authoritative provider terminal evidence | Proved terminal order outcome; cancellation job can succeed only for its exact requested IDs |
| reconciled | Use the adapter's evidence/result predicate, not state text alone; entry, SL, CLOSE, and source-management outcomes differ |
| management cancelled-flat | Succeeded cancellation-only result, no close/TP fill asserted |
| management entries-cancelled | Succeeded generic/later-TP cancellation result while position remains; no close claimed |
| management reconciliation-pending | Pending job/child until exact cancellation/close/SL recovery completes |
| notification sent_at / terminal failure | Delivery ledger outcome only; no implication that trade outcome succeeded |
| immutable audited baseline | Historical uncertainty excluded under approval, not fill/flat proof |

Entry job result distinguishes submitted/working versus filled-and-protected. If the action's agreed completion predicate is durable owned working-order acceptance, `succeeded` may mean **working entry accepted**, never **position opened**; later lifecycle updates remain linked domain events. A close success requires the canonical close readback contract and proper ownership attribution; financial release/accounting additionally requires verified fills and close binding. A source cancellation success requires every exact owned remaining entry terminal and fresh post-cancel positions. Protection success requires exact identity/quantity/level coverage readback; config success requires effective version readback; deployment success requires observed artifact/policy/readiness checks; restore success requires completed post-restore reconciliation gate, not merely dump import.

## 8. Idempotency, versions, concurrency, cancellation, and retries

Idempotency key is caller-generated high-entropy UUID persisted before enqueue in tab/session state; it survives timeout/reload until the result is retrieved. Uniqueness scope is `(principal_id, action, account_id, environment, idempotency_key)`, storing canonical argument/preview digest and job ID. Same key + identical payload returns the existing job/status with `Idempotency-Replayed: true`; same key + different payload returns 409 `idempotency_conflict`. A same-key replay never reconsumes an approval. Keep mutation receipts as durable audit for the retained financial history; do not expire the only duplicate-prevention record while provider IDs can still be reconciled. New keys do not bypass resource/domain claims; stable client order IDs derive from the canonical command/dispatch child, not each HTTP attempt. Web and Telegram receipts converge on the same command identity.

Resource versions are monotonic DB revisions or digest-bound canonical snapshots covering the actual mutation fields and identity/epoch, not only `updated_at` precision. Required If-Match is checked at preview enqueue, approval, worker claim and immediately before each effect. Provider resources carry observation digest plus epoch, quantities and order IDs; provider can change externally without an ETag, so fresh authoritative prechecks remain mandatory. A stale resource returns 412 `resource_changed`; refresh/repreview, never automatically upsize/reduce targets beyond approved rules. Position fill races may change a source close quantity under its canonical post-cancel algorithm; the preview must explicitly authorize that policy and bounded scope rather than pretend a fixed stale size is guaranteed.

Database transaction atomically inserts job, approval/idempotency consumption, audit and event outbox before returning 202. Workers use `FOR UPDATE SKIP LOCKED`, lease owner/token, monotonic claim generation, and checkpoints; a stale worker cannot acknowledge/write for a successor. Exact account/environment/symbol/position/order resource locks serialize conflicting close/protection/entry-cancel operations; account-wide operations acquire sorted affected-resource locks and an account maintenance lock. No deadlocking arbitrary lock order. Existing workers and Telegram must honor the same canonical ownership/intent fences; a console-only mutex is insufficient. Avoid holding DB transactions across ordinary provider network requests; persist the claim first, commit, then perform the owned step. Specialized recovery keeps its existing locked proof/apply protocol. External/manual provider changes are detected by readback, not assumed impossible because of local locks.

Persist provider intent/close binding/exact cancel claim before POST. Persist expected endpoint/normalized payload digest and stable ID without secrets. A crash between durable claim and POST is conservatively ambiguous; recovering worker reads order/fill/cancel/position evidence and must not issue that POST again just because no acknowledgment exists. Lease takeover cannot create a replacement child over an in-flight DB write or provider step. Use canonical storage drain/fencing behavior; cancellation does not start another cycle over unfinished writes.

Cancellation POST is idempotent and versioned. Before claim: atomically cancel queued/unapproved work and release only console reservations that are proved unused through the canonical owner. During running: set `cancellation.requested_at`, stop at the next safe unclaimed step, expose which steps are already irreversible. After claim/POST: cancellation cannot retract or claim the provider order canceled; keep readback running and settle ambiguity. Canceling an entry job is not canceling an already-working ENTRY order; that needs its own exact-order command. Canceling a close cannot reopen the position. Bulk cancel prevents remaining unclaimed children but settles claimed children; final canceled result includes all proved partial effects. Terminal successful work returns 409 `not_cancellable` or the existing terminal canceled receipt, not a false canceled status.

Retry policy: read-only GETs may use bounded backoff/jitter with rate-limit handling and explicit deadline, retaining real errors. Single-attempt bounded clock sampling and other existing specialized proof rules are unchanged. Browser retries GET or repeats enqueue with the **same** idempotency key, not mutation submission. No automatic retry of provider POST after timeout, malformed ack, disconnect, crash, lost lease, unknown status, or existing claim. Provider missing order detail alone is not proof of absence or fill. Even a definite rejection permits a new command only after the canonical adapter proves no surviving effect, revalidates admission, obtains fresh preview/approval, and uses its approved stable-ID rule; there is no generic job retry that clears domain claims. Config/deployment/restore ambiguity similarly requires runner state/checkpoint readback, not an unrestricted repeat. Ambiguity is surfaced, never suppressed as success/failure.

## 9. SSE events and resynchronization

GET `/api/v1/events` uses the same cookie session and scope authorization; no bearer token in query strings. SSE is notification of durable changes, not the sole source of truth and not a mutation channel. Event fields: monotonic durable `id`, `event` type, JSON `{schema_version, sequence, scope, resource_type, resource_id, resource_version, event_kind, observed_at, job_id|null, request_id|null, redacted_change|null}`. Types include job/approval/audit change, read-model invalidation, capability/config/policy change, and `resync_required`. Provider WebSocket stream remains owned by the existing protection/runtime service; browser SSE does not establish provider stream health.

Job/audit/domain-projection events are inserted into a durable event outbox in the same transaction as state changes. Delivery is at-least-once; client deduplicates event ID and ignores older resource versions. Events chiefly invalidate GET caches; sensitive resource contents are fetched by authorized endpoint. Filters/scopes are server allowlisted and checked before every delivery. Events reveal no other account resource IDs, raw secrets, SQL, credentials, approval phrases, or MFA assertions. Privilege/session change closes or reauthorizes connections; blocked/out-of-scope event IDs are not exposed as data.

Initial/resync GET returns data + event_cursor from one persisted snapshot, then client opens SSE after that cursor (`after` parameter on first connect; Last-Event-ID on reconnect). Provider observations are tagged separately as described above. Default heartbeat is 15 seconds; client declares connection stale after 45 seconds without bytes and independently ages all data. Reconnect uses bounded exponential jitter; do not recreate a job. Disconnected UI shows last-known data, ages, and a reconnect banner; disable new dangerous action when required evidence is stale, not necessarily all read navigation.

The replay retention default is 24 hours under policy; financial/audit/job history has independent longer retention. If cursor is missing/expired, ordering/schema continuity is lost, or bounded per-client queue overflows, send `resync_required` then close. Client discards incompatible cached snapshots, performs authorized GET resnapshot of visible panels/job, and reconnects after the new cursor. If history cannot be replayed, never silently jump the cursor and imply no events were missed. Heartbeats and worker polling cannot refresh business `observed_at`. Polling fallback is authenticated GET with If-None-Match and explicit disconnected status, not synthetic live events.

## 10. Operations privilege boundary

Web/read API runs least privilege: read projections plus dedicated control-plane insert/approval permissions, no direct trading-ledger UPDATE/DELETE, provider mutation credentials, Docker socket, host shell, backup decryption keys, or unrestricted SSH. Trading command executor obtains only its required provider credential through the existing service/secret boundary and invokes authoritative adapters. Read provider credentials should be separate read-only grants where the venue supports them. Web's worker-health mounts remain metadata-only with dedicated worker attestation sockets, not host /proc access.

A separate authenticated operations runner implements a finite allowlist of service/release/backup/config/quiescence operations. Runner accepts job ID, approved operation schema, artifact/resource IDs, policy/digest and target versions; it independently verifies signed/DB-backed authority, expiry, actor/approver scopes, expected release, LIVE policy, and maintenance constraints. It does not accept shell/Compose YAML/arbitrary environment fields or execute strings received from the browser. Fixed executable paths/argument templates run under constrained OS privileges with bounded timeout/output and redaction. Its privileged transport is not accessible from public browser routes. Runner receipts are token-fenced and read-backable after crash; inability to establish apply outcome makes the job reconciliation_pending.

Start/stop/restart does not alter execution mode/gates. Approved maintenance checks active exposure and preserves monitor/protection/close recovery; consumer-stop proof covers source/operator/fallback/stream as well as dispatcher, not just one latch. Recovery `all consumers stopped` is attested/observed by the runner, never a browser checkbox. Deployment runs read-only rendered-policy validation before build/recreate and runtime source digest/LIVE/readiness verification afterward. Operations privileges never release safety gates implicitly. Logs/output are sanitized at the runner before persistence; exit code alone is not provider/trading success.

## 11. Durable schema requirements and migration scope

These are **required future additive migrations**, not tables created by this document. Use existing `storage/migrations.py` sequencing, frozen historical schema discipline, and audited migration permissions; do not change already-deployed bootstrap text to masquerade as a migration. New tables use account/environment scope keys, foreign keys, check constraints, stable IDs, UTC times, and restricted roles. Exact migration numbers are assigned during implementation after checking current head.

| Proposed storage | Minimum invariants |
| --- | --- |
| `console_principals`, `console_role_bindings`, `console_sessions`, `console_identity_links` | Unique issuer/subject; explicit action/resource scopes, policy revision, revocation; session/token hashes and expiry, not raw IDP credentials; proven Telegram links |
| `console_policy_versions`, `console_config_versions`, `console_config_drafts` | Immutable policy/config revisions, typed redacted diff, approval/apply/effective refs; no secret plaintext |
| `console_previews` | Immutable action/schema/adapter/scope/arguments digest, exact resource versions/evidence refs, danger/phrase digest, expiry; consumed authority bound to job |
| `console_jobs`, `console_job_steps`, `console_job_effects` | Durable state/version; unique logical command; fenced leases; immutable step claim/payload digest before effect; native domain refs/results and explicit ambiguity |
| `console_idempotency_receipts` | Unique scoped key, payload digest and job FK; same-key conflict protection and durable retention |
| `console_approvals` | Exact job/preview/policy/digest/role scope, approver/step-up verification timestamps, expiry/consumption/revocation; two-person constraint enforced transactionally |
| `console_resource_locks` / canonical command ownership | Scoped conflict key, owner/generation/lease; integrate existing domain fences rather than override durable trading ownership |
| `console_audit_events` | Append-only actor/channel/action/resource/request/job/approval/policy/version/outcome, redacted before/after/evidence digest; denied attempts and reasons; no raw secret/approval token |
| `console_event_outbox` | Transactional monotonically ordered events with scope, resource/version/schema and delivery retention; no ephemeral-only job updates |
| `console_artifacts`, `console_backup_catalog`, `console_runner_receipts` | Opaque immutable artifact/digest, scope/retention/verification/encryption refs, token-fenced operation checkpoint/readback; no arbitrary paths/commands |
| Read projections/evidence refs | Transaction-coherent ledger snapshot/cursor, independently timed provider observations and errors, no mutation of admission records merely for display |

Audit writer has insert-only privileges; application roles cannot UPDATE/DELETE/TRUNCATE audit/approvals/immutable policy/baseline evidence. Use DB constraints/triggers and restricted maintenance roles, external retention/archival policy and evidence digests; a hash chain alone does not protect against an administrator replacing the database. Secrets and MFA assertions are redacted before storage, not only at response time. Audit records failed authorization/validation/enqueue, approvals/revocations, effect claims/readback, canceled/partial/ambiguous outcomes, access/export and runner operations. Retention is owner-approved by data class; purge cannot remove unresolved mutation/idempotency/audit evidence or held backups.

Do not repurpose `dispatches` as HTTP jobs, `operator_telegram_updates` as web idempotency, notification outbox as job completion, or admission `balance_snapshots` as live dashboard cache. Preserve stable entry child IDs, exact cancellation claims, immutable admission/baseline provenance, verified close bindings, existing fill economics and real provider events. Where existing legacy rows lack environment/account/epoch provenance, expose unresolved ownership and refuse unsafe mutation; do not guess LIVE or backfill fabricated current ownership. Resource revision integration and account-scoped projection design are implementation prerequisites because existing financial tables are not uniformly account-scoped.

## 12. Failure response and UI recovery contract

Error envelope: `{request_id, code, message, field_errors:[], retriable_read:false, job_id|null, evidence_refs:[], safe_next_action:null|refresh|repreview|reauthenticate|reconcile|contact_owner}`. Human messages are sanitized; raw provider/DB/runner exception text and secrets are never returned. Errors after an effect claim carry job ID and reconciliation status; HTTP failure does not erase the persisted job.

| Condition | API/UI behavior |
| --- | --- |
| Missing/expired session | 401; pause SSE; retain nonsecret draft locally only if policy permits; reauthenticate and GET existing job, never resubmit with a new key |
| Wrong scope/role or CSRF failure | 403; indistinguishable resource-not-found behavior where required; audit sanitized denial; no preview/side effect |
| Missing/stale step-up | 403 `step_up_required`; bind new assertion to unchanged preview; expired preview must be regenerated |
| Unknown field/decimal/enum/unsupported schema | 422 field error; no effect; unsupported action 409 `action_unsupported` with capability reason |
| Missing required If-Match/idempotency key | 428 `precondition_required`; no enqueue |
| Version/policy/config/identity changed | 412 `resource_changed` or 409 `policy_changed`; invalidate preview/approval, show fresh diff |
| Concurrent resource command | 409 `resource_busy`, authorized existing job link; no extra provider claim |
| Closed/unknown gate, stale proof, changed latch/baseline | 409 `gate_closed`/`proof_invalid`; show exact sanitized failed gate; no force/retry bypass |
| Rate limit | 429 with Retry-After; bounded read retry only, same-key enqueue lookup; no provider POST retry |
| Provider/DB read unavailable | 503 or partial read envelope; previous values explicitly stale; null not empty/flat; dangerous actions gated |
| Timeout before response / response lost after enqueue | GET receipt lookup by scoped idempotency key or repeat exact same-key enqueue; if persisted return existing job; no fresh provider command |
| Unknown provider/runner mutation result | Job reconciliation_pending plus evidence/refusal; GET-only readback; no generic automatic retry |
| Approval expired/revoked while queued | awaiting_approval/rejected with no new claim; refresh preview/consent before proceeding |
| Cancellation races execution | Return accepted request plus actual state/effects; never promise rollback; continue claimed-step readback |
| Bulk mixed outcomes | Exact child statuses/results; partially_succeeded only after no ambiguity; pending child keeps parent reconciliation_pending |
| SSE cursor gap/slow consumer/schema mismatch | Explicit resync_required and GET snapshot; stale banner until evidence refreshed |
| Artifact removed/expired/unauthorized | 410/404 under scope policy; no arbitrary filesystem fallback |

GET `/jobs/by-idempotency-key/{key}` is scoped to the current principal/action/account/environment and returns only its durable receipt/job; unauthorized lookup reveals nothing. Browser never reports success merely from HTTP 200, a response dictionary with `closed`/`cancelled`, a sent notification, or a green config badge. Error dictionary from today's disabled open adapter is an unsupported result, not a job success.

## 13. Implementation gates and acceptance evidence

This document completes UI/API **specification**, not runtime feature implementation. No console source/schema/frontend/auth/job/runner code is added here. Parent's isolated local ASGI smoke observed existing health endpoints returning 200 with degraded/unknown readiness and null orders_enabled; `/api/v1/actions`, `/api/v1/positions`, `/api/v1/session` returned 404. That is local endpoint evidence, not production readiness or provider behavior. No live deploy, trade, latch/risk change or production probe is implied.

Implementation must cover each screen/read DTO and every named action with available/gated/unsupported/read-only status, real reasons, full links, and scoped truthful evidence; unsupported today is not permission to ship fake successful handlers. Mutation enablement requires shared Telegram/web durable command cutover, action-specific real adapters, schema migrations/privileges, MFA/session/CSRF/RBAC, resource version/fencing, immutable approval/audit, ambiguity readback and operations runner integration. Read UI may ship before mutations, but must visibly preserve their unsupported/gated status.

Main integration owner should later run offline/local acceptance checks once all implementation slices land: generated OpenAPI/TypeScript compatibility; all-route responsive/keyboard interaction; null/partial/stale evidence rendering and coherent filters/detail links; source 25/75 sizing and automatic-full-TP versus TP1-half semantics; external/baseline ownership exclusion; exact-owned cancellation and protective survival; close/SL intent-before-POST and readback; same-key timeout/reload/duplicate cross-channel submissions; resource-version conflict/lease takeover/crash at every irreversible boundary; cancel races and bulk ambiguity; revoked/expired approval/session, CSRF/cross-account/MFA/role abuse; secret/log/audit redaction; SSE replay/gap/resync/backpressure; runner allowlist/digest/privilege boundary; backup restore/no-replay policy; immutable audit and migration constraints. These are required checks to execute during implementation, **not checks run or passed by this specification**. Production acceptance requires separately approved read-only runtime evidence and scoped operational authorization under the existing trading and production policy.
