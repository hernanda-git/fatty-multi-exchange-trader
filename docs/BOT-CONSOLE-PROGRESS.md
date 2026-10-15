# Bot Management Console — progress and evidence tracker

Created 2026-10-14. Approved scope: [master plan](BOT-CONSOLE-MASTER-PLAN.md). Specification: [UI/API](BOT-CONSOLE-UI-API-SPEC.md). Source investigation: [capabilities](BOT-CONSOLE-CAPABILITIES.md).

## Status vocabulary

NOT STARTED: no work; ACTIVE: actionable work underway; BLOCKED: named missing prerequisite; SPECIFIED: reviewed written contract but no implemented behavior; IMPLEMENTED: integrated code exists; LOCALLY VERIFIED: named exercised behavior; DEPLOYED: running lineage confirmed after authorized rollout; ACCEPTED: phase criteria proved. A document or fixture never promotes runtime status. Checkpoints remain historical rather than silently rewritten as current health.

## Owner instruction and authority

Owner subsequently requested autonomous completion without further input. Full repository implementation, verification, commit/push and exposure-safe console deployment are now in scope. Development/deployment does not authorize synthetic trades, recurring-latch release, risk expansion, optional provider mutation gate activation, credential/account substitution, or unrelated service changes. Existing trading protections and separate per-action approvals remain effective.

## Baseline

Fresh isolated clone of origin/main `ea0114e136ec1ba4adc547f10a61aa533934221d` at `C:/Users/it26/AppData/Local/Temp/fatty-console-20261014`. Existing audit workspace and its unrelated edits remain untouched. Local source inspection only; last conversation's relatch is not a fresh production probe. Current web exposes health endpoints, not the proposed management API. No console has been built or deployed at this checkpoint.

## Phase tracker

| Phase | State | Completed / evidence | Remaining acceptance |
|---|---|---|---|
| P0 Capability/safety | ACCEPTED | Source-backed capability inventory, policy prohibitions and shared adapter boundaries documented and implemented | Natural live trade is separate provider evidence, not a development acceptance gate |
| P1 UI/API contracts | ACCEPTED | All 15 screens, typed actions, scoped evidence, jobs/approvals, SSE and host boundary implemented; operator guide published with source | Runtime receipts below remain separate from specification |
| P2 Authenticated read console | LOCALLY VERIFIED | Real passkey enrollment/sign-in/sign-out; desktop/mobile navigation; scoped APIs; CSRF refusal; outage/reconnect browser proof | Private production rollout and personal owner enrollment |
| P3 Durable shared commands | LOCALLY VERIFIED | Console unit behaviors and 11 isolated PostgreSQL job tests; real browser → durable job → worker → readback for annotations/incident acknowledgments | Final complete Linux suite receipt; production worker lineage |
| P4 Trading/protection controls | LOCALLY VERIFIED | Integrated console trading regressions pass; ownership, gate, claim/readback and protection cases exercised without live credentials | Natural provider lifecycle remains unobserved; no test trade authorized |
| P5 Recovery/operations | LOCALLY VERIFIED | Allowlist/exposure/refusal/restore-replay regressions, real private provisioner boundary smoke, built-image and cross-UID media isolation | Install and observe separate production host executor after safe rollout admission |
| P6 Hardening/release | ACTIVE | Compiled frontend, strict source types, package build, security reviews and browser/API/DB/image proof recorded below | Final full-suite receipt; commit/push; fresh authenticated deployment inventory, backup, approved rollout and lineage |

Phases are sequencing, not reduced scope. No phase is accepted because documentation exists; code and runtime gates above still apply.

## Work-package register

| Package | Scope / integration contract | State |
|---|---|---|
| DOC1 | Approved master plan, full screen/action scope, dependencies, phase gates and release invariants | SPECIFIED |
| CAP1 | Actual gateway/parser/service/lifecycle capabilities and missing boundaries | SPECIFIED |
| SPEC1 | Screen interactions, API/DTO/jobs/auth/event contracts, operations privilege boundary | SPECIFIED |
| VERIFY1 | Local read-only runtime and cross-document link/coverage smoke | LOCALLY VERIFIED |
| PUB1 | Commit explicit files, push without force, prove published SHA; preserve unrelated work | PUBLISHED |

## Prerequisites and risk register

| Risk / prerequisite | Required treatment |
|---|---|
| Existing actions may be parser-only, adapter-incomplete or bypass shared admission | Capability inventory before enabling; implement and migrate one typed command path |
| Process-local confirmations cannot survive concurrent sessions/restart | Durable actor/session/resource-bound expiring approvals before any web mutation |
| Recurring clock latch previously observed | Independent unresolved runtime incident; never hide, widen thresholds or blanket-clear via UI |
| Mixed manual/baseline/bot exposure and bulk actions | Explicit ownership/resource versions; disabled account-wide scope until approved contract |
| Web host privileges | Separate allowlisted executor, no Docker socket/unrestricted shell; network/principal boundary proof |
| Unknown current server/domain/identity configuration | Discover existing deployment conventions before rollout; do not invent DNS/public exposure or copy credentials |
| Live-only production policy versus pause | Pause through approved admission latch, never DEMO/execution=0; maintain existing protection |
| Financial ambiguity / crash recovery | Durable intent before POST, repeated requests GET-only until outcome established |
| Feature/API design does not establish adapter readiness | Record each missing action and implementation acceptance; no fake success or no-op control |

## Verification receipts
Observed local baseline smoke: `uv sync --frozen --group dev` installed the isolated Python 3.12.14 environment. An inline `uv run python -` script constructed the real FastAPI app with an injected empty runtime-health reader and used HTTPX ASGI transport. Both health GET routes returned HTTP 200 with degraded/unknown readiness and `orders_enabled=null`; proposed `/api/v1/actions`, `/api/v1/positions`, and `/api/v1/session` returned 404. No provider or database client was constructed, no network/server mutation occurred, and no management API implementation is claimed. The local default DEMO response is development configuration only, not a production change. Documentation link/scope verification is recorded after integration.

Documentation smoke passed: 81 relative file links resolve; P0 through P6 each appear exactly once in the phase tracker. Coverage review maps all approved screens/actions to capability prerequisites and UI/API contracts. This proves document integrity and baseline route behavior, not an authenticated console or live trade.


## Publication receipts

Planning/specification commit `883f9a4` (`docs(console): approve master plan and specify capability and UI API contracts`) was pushed successfully from baseline `ea0114e` to `origin/main`. This receipt update is a separate documentation commit. Publication is not deployment; no production changes occurred.


## Next implementation gate

After CAP1/SPEC1 integrate, start P2 authentication and first real read surface using shared DTOs. Do not attach financially mutating actions until P3 durable job/shared-command gate passes. Every later work session updates this ledger with completed behavior, exact verification, blockers and next package; do not leave successful work marked ACTIVE or future work marked implemented.


## Durable control-plane implementation

Migration 26 (`console/schema.py`) adds console identities, passkeys, expiring hashed enrollment/session authority, digest-bound step-up grants, immutable previews, jobs, approvals, idempotency receipts, fenced effects/steps/resource locks, append-only audit, replayable events, independent observations, reviewed config/maintenance plans, artifacts/backups/releases and host receipts. It preserves existing financial records and adds only the active native-TP observation column. SQL dollar-quoted function bodies require the quote-aware migration splitter; the web database role receives scoped read access and console authorization writes, never trading-ledger UPDATE/DELETE or table ownership.

`CommandService` validates the same strict action schemas used by adapters. Enqueue atomically consumes immutable preview authority, bound passkey consent and the scoped idempotency receipt together with audit/events. Owner or privileged role grants stay tier 3; one enrolled owner cannot self-approve or downgrade onboarding. Distinct eligible owners must enroll through the separately authorized secure invitation procedure before two-person work is executable. Same-key replay returns the existing job without consuming consent again.

The container console worker collects GET-only observations and executes trading/metadata actions; the separately privileged host worker polls only host actions. Lease token/generation fences, sorted account/exact-resource locks, canonical domain ownership and intent claims remain independent safeguards. An interrupted or taken-over dispatch is readback-only; cancellation prevents unclaimed work but neither reverses a provider effect nor fabricates its outcome. Failed observations retain null data/observation time and their own error rather than publishing zero balances or empty/flat inventory. Event replay is bounded to 24 hours while durable history is retained.

Regression sources: `tests/unit/test_console_store_jobs.py` and `tests/e2e/test_console_jobs_postgres.py`. Integration owner should run these with the full suite and `FATTY_TEST_POSTGRES_DSN` pointing only to isolated PostgreSQL; the E2E fixture creates a disposable per-test schema. Assertions cover atomic concurrent same-key submission, approval consumption, self-approval refusal, stale resource/lease fencing, GET-only takeover, cancellation, immutable evidence, restricted web SQL privileges and unavailable snapshot truth. Worker author ran no build, tests, formatter, deployment or production operations; these are implementation assertions awaiting the integration owner's exercised receipts.

## Continuation checkpoint

Recovered the interrupted console implementation session and its existing workspace. Independent implementation owners are completing current source before integrated checks; no console acceptance or deployment is claimed by recovery alone. The isolated `fatty-console-pg-20261014` database and `fatty-console-runner-20261014` remain available; an actual connection returned database `fatty_console_test` and role `fatty_test`.

Fresh read-only production observations: checkout `ea0114e`, maximum migration 25, authenticated provider positions and ordinary pending orders both empty. The Bitget latch remains active with reason `clock-skew-unavailable`; it is preserved, not released to satisfy console acceptance. These observations do not prove absence of pending plan orders or future trade readiness. Provisioning now reserves a dedicated private `runtime/console-backups` directory rather than changing unrelated backup permissions.



## 2026-10-15 autonomous continuation — exercised acceptance

Recovered the failed continuation chain and existing workspace. The prior cleanup
workers had failed before editing; replacement owners completed behavior-preserving
SQL/literal wrapping, loop-variable binding and type integration. Schema SQL remained
byte-identical at SHA256
`1e3e9e0af01b622cd0a9c8b79560502da9ab7b02d185b14a904d97f51933a248`.
No blanket lint/type suppressions or failure-skipping fixture changes were introduced.

Completed the master-plan metadata actions `signal.annotate` and
`incident.acknowledge`. Their immutable jobs/operation receipts persist operator
evidence without rewriting source receipt/revision/freshness or clearing a latch.
Signal detail and recovery reads expose the scoped records. Stale source revision
or changed active incident reason refuses a new preview. The recovery projection
also includes the independently observed protection-stream latch.

Browser proof used real Chromium, real FastAPI/PostgreSQL and a virtual user-verified
WebAuthn authenticator on an isolated account/database; no authentication bypass or
provider credentials. Exercised enrollment, sign-out (protected read 401), resident
passkey sign-in, all 15 navigation surfaces, desktop 1440×900 and mobile 390×844 menu,
signal list/detail, immutable preview/review/queue/job/readback, and missing-CSRF
preview refusal (403). All declared top-level read sections used by the screens
returned scoped envelopes; Trading desk intentionally reads the positions projection
and action catalog, not a fictitious `/api/v1/trading` route.

Real metadata jobs reached `succeeded`. An annotation left the source `EXPIRED` with
its original previous-day receipt time. Incident acknowledgment left the active
`clock-skew-unavailable` latch and its update time unchanged. Killing/restarting the
isolated HTTP server changed the header to `disconnected — last known data`, then
back to `live` after authenticated SSE reconnection. Mobile had no horizontal
document overflow. Browser inspection found and fixed null freshness thresholds
being coerced to zero; evidence chips now use the source identifier and do not mark
unbounded fresh ledger observations stale.

Verification receipts (overlapping suites; do not add these counts):

- Console/auth/operations/reads/jobs/trading and SQL-boundary unit selection:
  **158 passed**.
- Real isolated PostgreSQL console job regressions: **11 passed**, including new
  immutable annotation/acknowledgment and stale-evidence refusal cases.
- Linux initial complete suite: **2284 passed, 17 failed**. Two failures were the
  new source fixture missing required `intake_state`; fourteen were CRLF shell
  snapshot execution; one was the operator health fixture missing the now-required
  console origin. Fixed all three causes. Added `*.sh text eol=lf` and normalized
  executable shell files; no failing checks were disabled.
- Repaired Linux PostgreSQL/jobs, production-policy, worker-health and Telegram
  polling selection: **77 passed**. Final complete Linux run is pending at this
  checkpoint.
- Ruff source checks passed. Final trailing-blank formatting was applied to the
  affected polling test. Strict `mypy --platform linux src`: **120 source files,
  no issues**. `uv build`: source distribution and wheel built. Frontend
  `npm run build`: TypeScript and Vite production build passed.
- Actual multistage Linux Docker image built as `fattyremediation:reviewcandidate`,
  manifest digest
  `sha256:a02aba4f5719203beee843d777aeaade0c1646c2456f9815940c898494c54d6e`.
  Its revision label is `console-local-candidate`, not a published release SHA.
  Credential-free, network-none image smoke applied schema 26, served compiled
  HTML (200), refused unauthenticated API access (401), and checked CSP/static assets.
- Actual cross-UID image media handoff passed: atomic private publication,
  canonical revision reuse, wrong-hash refusal, analyzer write denials and outsider
  read denial; temporary containers/image/volume were removed by the verifier.
- Real temporary-directory provisioning accepted Compose project lengths 1/63
  and refused length 64 before writes, with private key 0600 and host state 0700.
  This fixes the host review's provisioner/runner project-length mismatch.
- Read-only authentication/durable-authority review found no proven release
  blocker. Full host/secret/allowlist/replay source review found the project-name
  mismatch above and no additional proven blocker. Source reviews are not live
  provider or host-effect proof.

CI inert Compose and cross-UID verifier environments now supply a dedicated
synthetic console database password; production still requires its own generated
password. Build context excludes frontend caches, generated TypeScript build state,
credentials and runtime artifacts. The complete operator/trader/recovery/admin
guide is [BOT-CONSOLE-USER-GUIDE.md](BOT-CONSOLE-USER-GUIDE.md).

### Production rollout admission at this checkpoint

SSH confirms the production checkout remains `ea0114e`; no console rollout,
production migration, passkey enrollment, provider mutation, risk expansion or latch
release has occurred. An independent DB read found Bitget active with
`clock-skew-unavailable` and the stream latch inactive with its prior approved
release reason. A fresh authenticated `/api/v2/spot/account/info` read returned
HTTP 429, preventing current identity and complete positions/ordinary/plan-order
flatness proof. Historical empty positions/orders are not substituted for this
missing fresh evidence. Deployment remains withheld until authenticated inventory
is available; the console worker must not be added blindly to an already
rate-limited provider workload.

