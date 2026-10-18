# Bot Management Console — progress and evidence tracker

**New UX workstream:** the owner reports that the current frontend is too
complicated and requested a detailed redesign plan. Track that work in the
[UX progress and checklist register](BOT-CONSOLE-UX-PROGRESS.md), under the
[UX master plan](BOT-CONSOLE-UX-MASTER-PLAN.md). Redesign implementation is
NOT STARTED. Historical backend/release acceptance here is not acceptance of the
current visual design or evidence that the redesign exists.

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
| P2 Authenticated read console | DEPLOYED | Password-only login, real production wrong-password refusal/correct-password sign-in/logout, opaque sessions/CSRF, all nine application containers healthy | No enrollment, username, passkey, OTP or external login is required |
| P3 Durable shared commands | DEPLOYED | Schema 26, shared Telegram/web commands, fenced worker and digest-bound password-session consent; complete 2312-test Linux suite and real browser Tier 2 job/readback proof | Financial confirmations remain; sole shared owner cannot supply a distinct Tier 3 approver |
| P4 Trading/protection controls | DEPLOYED | Current copied source/image identity proved across all nine applications; ownership, risk, cancellation, protection and replay regressions passed | No synthetic live trade authorized; existing incident latch and independent mutation flags remain unchanged |
| P5 Recovery/operations | DEPLOYED | Separate host executor active, fresh actual service/log/schedule evidence, immutable current release catalog and verified pre-rollout backups; allowlist/restore/replay refusal regressions passed | Dangerous host/provider effects require their normal approvals; no production restore or test rollback was performed |
| P6 Hardening/release | ACCEPTED | Password-only release: 2312 passed/zero skips, strict types/lint, frontend/image/package builds, actual password browser/API/SQL/container/host proof and published runbooks | Natural live lifecycle and unrelated sticky clock-latch recovery remain separate |

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


## Current operator gate

Owner superseded passkey enrollment with password-only sign-in. No username, invitation, OTP or external identity is required. The password-only cutover and current verification receipts are recorded at the end of this tracker. Existing financial confirmations and the sticky clock incident remain separate from authentication; console access never authorizes blanket resume, risk expansion or historical signal replay.


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

### Published implementation and superseding admission observations

Implementation commit **`56d04be`** (`feat(console): implement authenticated durable
bot management console`) was pushed to `origin/main`. This is publication, not
production cutover. Console documentation relative-link smoke checked **88 links**
with no missing targets.

A later paced, independent authenticated observation at
`2026-10-15T02:42:44Z` verified the configured provider account identity, zero active
positions and zero ordinary orders. The initial custom plan probe correctly
withheld proof on an unrecognized null-list response; the existing canonical
`normalize_pending_plan_response` decoder then proved zero `profit_loss`,
`normal_plan` and `track_plan` orders with no continuation in any category.
This supersedes the earlier 429/incomplete-inventory checkpoint; it does not
release the fault latch or establish future request availability.

Before source cutover, the existing integrity-checking backup script completed:
`backups/console-before-20261015/fatty_trader_20261015T024423Z.dump`,
**1,753,550 bytes**, with automatic retention disabled. Previous images for all
eight running application services were retained under
`fatty-console-rollback-<service>:ea0114e-20261015`; the private nonsecret policy/image
baseline is `runtime/console-release-20261015/before.json`. No database rollback
is implied if subsequent provider effects occur.

The dedicated host executor virtual environment was installed from locked,
hash-checked dependencies and the published package without altering another
application environment. Its new user unit was copied but not started at this
checkpoint. The complete final Linux suite and deployment admission still gate
production cutover; production application containers remain unchanged here.

### Final-suite synchronization correction

The next complete Linux run returned **2299 passed, 1 failed**. The failure was
the responsiveness regression's one-second wall-clock deadline for reaching the
selected real PostgreSQL operation, not a clock response failure. That deadline
included preceding database work and was not the responsiveness invariant.
The test now awaits an explicit operation-entry barrier or reconciliation
completion/failure, asserts the operation runs outside the event-loop thread, and
keeps the same one-second clock-completion bound while storage remains blocked.
No runtime timeout, admission gate or production code was relaxed.

All four real-PostgreSQL storage-operation variants then passed in **7.84 seconds**.
The changed regression passed Ruff and formatting; all five shell scripts passed
Linux `bash -n`. The final complete suite is running again without concurrent image
builds. Production cutover remains withheld pending that result.

The published immutable image `fattyconsole:56d04be` was built from the Git archive
of the pushed source, not the Windows verification snapshot. Image identity is
`sha256:2e952a6188a2afff7b1a3d24907ef01c6c252c0ed6998f37e92163ebb066844b`;
copied Python source exactly matches the published tree at digest
`508e6c14b14e1e6bc9013e7520a0a60baab2662b58f635f7e93088f1214bb219`.
Its image revision label is `56d04be`.

### Complete regression gate passed

The complete isolated Linux/PostgreSQL run finished with **2300 passed, zero
failures and zero skips** in **806.24 seconds**. This supersedes both prior failed
full-suite checkpoints. The accepted JUnit receipt is preserved privately at
`runtime/console-release-20261015/acceptance.xml`, SHA256
`d5652da414b2df991dc990ed49288f58d06e1c48a1a2d853bcb10862bfe70ccc`.
The deterministic regression/documentation correction was pushed separately as
`4a4f89f`; runtime source remains identical to the built `56d04be` image.

The published inert Compose configuration validated and its policy checker returned
`production_live_policy=PASS`. The pre-console backup SHA256 is
`1d6f76212773e899d022345f68523cc167defd96c9d63fb9f938fbf4af703694`.
Production cutover now proceeds through fresh authenticated flatness, entry-consumer
quiescence and a second flatness observation before stopping monitoring.

### Initial production rollout and catalog boundary finding

The protected cutover's two authenticated flat observations were recorded at
`03:11:35Z` and `03:12:00Z`, with entry-capable consumers stopped between them;
monitoring remained running through the second proof. After all application
consumers stopped, a second integrity-checked backup completed:
`backups/console-before-20261015/fatty_trader_20261015T031248Z.dump`,
**1,753,547 bytes**. The checkout fast-forwarded to `4a4f89f`; console-only secrets
were provisioned without printing values. Existing effective policy values matched
the pre-rollout baseline before migration.

Migration and initialization exited 0; schema is **26**. All **nine application
containers** became healthy on immutable image
`sha256:2e952a6188a2afff7b1a3d24907ef01c6c252c0ed6998f37e92163ebb066844b`;
each copied Python tree matched the published source digest. The separate host
executor is active/running with zero restarts and fresh services/log/schedule
observations. Actual web binds only `127.0.0.1:18081`, has no provider credentials,
private key or Docker socket mount, and uses `fatty_console_web`: financial SELECT
allowed, financial UPDATE refused, console submission allowed.

The real deployed login surface was exercised through the SSH tunnel: HTML 200,
health 200, unauthenticated overview 401, no-store protected responses and restrictive
CSP. No production authentication bypass or invented owner passkey was used.
A one-use 600-second enrollment invitation was delivered through the configured
private Telegram owner channel; the token was not printed. Trusted host enrollment
registered `console-20261015-56d04be` in the immutable release catalog.

Current latches remain byte-for-byte the prior reasons/update times: Bitget active
with `clock-skew-unavailable`; protection-stream inactive with its prior approved
release reason. Fresh account, identity, clock, position, ordinary/plan-order, fill,
protection and worker observations were independently available. This does not clear
the sticky incident or claim entry readiness.

Post-rollout evidence exposed a real read-model bug: the provider catalog contains
**833 contracts**, including three valid Unicode symbol names, but an ASCII-only
metadata identity validator rejected the whole collection. The correction accepts
bounded uppercase Unicode alphanumeric **catalog identities only**. Position
ownership and all mutation symbol schemas remain ASCII-only; no unsupported market
is newly authorized for trading. New regressions preserve ASCII plus Unicode catalog
rows, refuse unsupported position adoption, and reject missing/control/markup
identities rather than fabricating a complete inventory. The corrected release is
under verification before superseding the initial image.

## Final engineering acceptance and deployed release

The catalog-corrected implementation **`580c346`** was committed, pushed and deployed.
Its focused console selection returned **163 passed**; Ruff passed and strict mypy
reported no issues in **120 source files**. The complete isolated Linux/PostgreSQL
suite then returned **2305 passed, zero failures and zero skips** in **899.84 seconds**.
The final JUnit receipt is preserved privately as
`runtime/console-release-20261015/acceptance-catalog.xml`, SHA256
`9b38258650e03118c7c0739328d5be433ef78f9808e9a0a373e3e5c7303e2d57`.

Final rollout repeated authenticated flatness at `03:38:25Z` and `03:38:49Z`,
with entry-capable consumers stopped between observations and monitoring kept
running until the second proof. All ordinary and all three plan-order categories
were empty. Before the code cutover, the integrity-checking backup completed at
`backups/console-catalog-before-20261015/fatty_trader_20261015T033902Z.dump`,
**3,330,391 bytes**, with retention disabled. No backup was restored.

The running immutable image is
`sha256:44b5c346a5f99f640da444f625c479b3d1493c9b242c3023cd1dccd74066084d`,
revision label `580c346`. The copied Python source digest is
`809f3cfec6713e7771537755d812bf189e81b7481d7b492011854cd395ce7ad1`,
matching the published tree and **all nine healthy application containers**.
The dedicated host executor package was upgraded to the same published source and
the user service restarted; PostgreSQL remains at schema **26**. Rendered and actual
effective policy still matches the pre-rollout LIVE/LIVE/1, risk and mutation-gate
baseline. The web remains loopback-only, credential/host-key/socket isolated, with
financial read permission but no financial UPDATE privilege.

Actual worker collection at `2026-10-15T03:40:07Z` recorded **834 contracts**, complete,
with no error and both ASCII and Unicode identities preserved. The market count
changed naturally after the earlier 833-contract observation; no count was pinned.
The existing mutation/ownership symbol admission rules were not widened.
Trusted host registration enrolled current immutable artifact
`console-20261015-580c346` with the exact image/source/schema binding.

The final deployed browser surface was re-exercised through the private tunnel:
HTML **200**, health **200**, protected overview without authentication **401**.
Production authenticated navigation was not bypassed: the owner must register a
personal user-verified passkey through normal first-access enrollment. Local
authenticated desktop/mobile, preview/job/worker/readback and disconnect/reconnect
proofs remain recorded above.

**Operational boundary:** the Bitget `clock-skew-unavailable` incident remains active
with its original reason/update time; the stream latch retains its prior inactive
state. Engineering deployment is complete, but it is not a claim that new entries
are currently admitted or that a natural live trade executed. No synthetic trade,
incident-latch release, risk expansion, optional mutation-gate activation, credential
substitution, database restore or unrelated host-service change was performed.

Final onboarding refresh delivered a new one-use **600-second** invitation to the
configured private Telegram owner, without printing its token. The executor's final
state is active/running with **zero restarts**, and the container console worker's
real heartbeat check exited 0. Verification-only PostgreSQL/container/image,
temporary source archives/directories and the newly created local smoke harness
were removed; acceptance receipts, private backups, rollback images and the running
release were retained. Final documentation link smoke checked **88 links**, none
broken. Owner passkey enrollment is normal first use, not an implementation stub.

## Owner-requested password-only authentication cutover

Owner explicitly replaced passkey/invitation authentication with one shared password
and no other login factor. The supplied secret is not reproduced in repository
source, docs, tests or tool output. Web uses only a private salted scrypt verifier
file; provisioning reads plaintext from stdin and never stores it. Hash-file mode
0640/read-only mount and a fresh random salt on replacement were exercised against
a temporary deployment directory; plaintext was absent from files and output.

The sign-in UI has exactly one password input and one submit button; no username,
WebAuthn, enrollment, OTP or external sign-in remains. Real Chromium/FastAPI/isolated
PostgreSQL smoke proved wrong-password refusal, cleared input after submission,
correct-password login, logout/protected read 401, removed WebAuthn route refusal
and missing-CSRF consent refusal (403). An actual isolated Tier 2 `admission.pause`
preview/consent/enqueue/worker/readback reached `succeeded` without another password
or factor prompt. Session consent rotates the session and CSRF token together;
browser smoke caught and corrected missing frontend token/session refresh.

The fixed scoped `console-password-owner` is created transactionally after correct
password verification. Hash/scope-derived principal revision binds sessions to the
configured password epoch. Old enrollment/passkey routes and dependency were
removed; historical tables remain inert. Existing session, rate-limit, origin,
CSRF, immutable consent, worker/effect/idempotency and financial ownership gates
remain. A shared owner cannot forge a distinct approver; existing Tier 3 financial
policy remains unavailable rather than being silently weakened. Telegram retains
the typed preview/job bridge only through a current password-authenticated session
and cannot create login or consent.

Initial focused integrated tests returned 187 passed with one removed-route
status assertion failure (static routing rejects POST with 405 rather than 404);
the contract now accepts either unavailable-route response. The first Linux
auth/console/policy/health/PostgreSQL selection returned 250 passed and two obsolete
two-owner approval expectations. Updated those tests to prove shared-owner refusal
without consuming consent and preserve immutable job/receipt tests using supported
Tier 2 authority. No live trade, latch release or other provider mutation was used.
Ruff and strict Linux-platform mypy passed; TypeScript/Vite production build passed.
The complete isolated Linux suite is running before deployment.

### Password-only release acceptance and production proof

Published and deployed **`16b6508`**. The complete isolated Linux/PostgreSQL suite
returned **2312 passed, zero failures and zero skips**, in **823.85 seconds**.
Ruff checked all source, **311 files** passed formatting, strict mypy passed
**120 source files**, TypeScript/Vite built and Python source/wheel packaging passed.
The final JUnit receipt is preserved privately at
`runtime/console-password-release-20261015/acceptance.xml`, SHA256
`62148217309ab482583c96a3a5c53e0c69c99bf8879429beb9425234a88a6488`.

Protected rollout proved the authenticated account flat at `04:34:47Z` and
`04:35:11Z`, quiescing entry-capable consumers between observations and leaving
monitoring running until the second proof. The integrity-checked pre-cutover backup
is `backups/console-password-before-20261015/fatty_trader_20261015T043523Z.dump`,
**11,446,518 bytes**. No restore or test trade occurred.

The supplied password was sent only over SSH stdin to the provisioner; only a
salted scrypt verifier was written to the ignored private 0640 hash file and mounted
read-only into web. The secret is absent from Git and deployment command arguments.
All previous sessions were revoked, old non-password principals disabled and unused
invitations/challenges consumed. Immutable consent records were not rewritten:
revoking their sessions invalidates old authority.

Actual production browser proof: exactly one password field and one sign-in
button; incorrect password refused; supplied password accepted; session identity
`console-password-owner`, account/environment matched the deployed LIVE binding and
auth policy `console-password-v2`. There was no extra authentication prompt after
login. Logout returned to the one-field form and protected overview returned **401**.
The verification session was signed out rather than left active.

All **nine application containers** are healthy on immutable image
`sha256:ed72455b83b574d76c19c2c68892335dd04bc36b604eef501774498b4af4c7e6`,
revision `16b6508`, copied source digest
`f3e8186b7e9c62160a8752fe9704071f6a167df07a7b406f81732f82c99d3b40`.
The same source is installed in the dedicated host executor and current artifact
`console-20261015-16b6508` is registered. Actual SQL evidence shows zero active old
sessions, zero enabled old principals and zero live enrollment invitations.
Web remains loopback-only, restricted to console SQL writes and without exchange
credentials/Docker socket/host decryption key. Production LIVE/LIVE/1, risk values,
optional mutation gates and incident-latch reason/update time match the preserved
baseline. Password-only login does not release the sticky clock incident.

## Public HTTPS access at trade.warga-digital.com (2026-10-15)

The owner directed public access to the console through their existing
`trade.warga-digital.com` subdomain, following the OpenLiteSpeed ingress pattern
already used by the owner's other applications. A proxied Cloudflare A record
`trade.warga-digital.com -> 46.202.155.192` (record
`eeaadd59fcc530156940525ac1f8b0b1`) fronts a new OpenLiteSpeed vhost
`/usr/local/lsws/conf/vhosts/trade.warga-digital.com/vhost.conf` — a
`respBuffer 0` proxy to `127.0.0.1:18081` mapped in the `Default`, `SSL`, and
`SSL IPv6` listeners. The web port remains bound to host loopback; no port was
published on all interfaces and no other application's identity or credentials
were adopted.

The host `.env` now sets `CONSOLE_ORIGIN="https://trade.warga-digital.com"`;
`web`, `operator-bot`, and `console-worker` were recreated together and all three
report the new origin. Exact-origin enforcement was proved on the recreated web:
`POST /api/v1/auth/login` returned **200** with `Origin:
https://trade.warga-digital.com` and **403** with the previous
`http://localhost:18081` origin, so the former SSH-tunnel path is no longer a
sign-in or mutation origin.

Edge verification: `https://trade.warga-digital.com/` served the console HTML
**200** through Cloudflare (`CF-RAY` SIN edge, `cf-cache-status: DYNAMIC`),
`/health/telemetry` **200**, login **200**, and `GET /api/v1/events` SSE **200**
(`eventsource`). A production browser sign-in exercised the live surface through
the public origin: header `5536885345 LIVE bitget live`, Exchange Connection
Synchronized (skew **-5ms**), live equity **$9.17**, and the signal inbox
rendering expanded `MESSAGE / SETUP` cells. All ten application containers report
`unless-stopped` and OpenLiteSpeed is system-enabled, so the public console
restarts with the host. The verification session was left to idle-expire.

## Push notification delivery (2026-10-15)

The owner requested iOS/web push because the Telegram chat was muted. Web push
now fans out from the same durable outbox: migration 28 adds
`web_push_subscriptions` and `web_push_deliveries`; migration 29 grants the
console role the privileges its routes need (SELECT/INSERT/UPDATE on
subscriptions, INSERT on the outbox for the test action). The VAPID keypair is
provisioned by `scripts/provision_web_push.py` into `runtime/`
(`web-push-private.pem` 0600 owned by the container UID, `web-push-public.txt`
0644) and mounted read-only into `notification-sender` and `web` respectively.

Verified before deploy: 13 push unit tests plus surrounding console/notification
suites, ruff, strict mypy on changed modules, TypeScript build, and a
real-crypto harness proving RFC 8291 decryption of the sent payload plus a valid
VAPID ES256 JWT (`aud` = push origin, `k` matching the provisioned key).

Verified in production after deploy (branch `feat/web-push`, based on `04a77da`):
schema version **29**; `/api/v1/push/config` returns `enabled: true` with the
public key; `POST /push/subscriptions` and `POST /push/test` return **200**; the
sender's fan-out created a durable delivery row whose first attempt was
classified `retryable` with a scheduled retry (synthetic endpoint, removed
afterwards; the subscription row and its deliveries remain as audit evidence).
The service worker registers at scope `/` on the public origin and the Settings
card renders with the iOS install guidance. A push-ledger fault is isolated so
it can never suppress the Telegram lane.

Production runs this branch until `feat/web-push` is merged into `main`; the
host checkout stays on `feat/web-push` for that reason.

## Runtime hardening deployment (2026-10-16)

Owner directive: close the remaining hardening findings autonomously, then
commit, push and deploy. Release `668d82b` on `main` does that, so `main` now
carries the web-push lane plus the hardening release and the host checkout is
back on `main`.

Findings closed: unbounded container logs, a superuser runtime database role, a
backup older than the deployed schema, unbounded console telemetry growth,
missing transport hardening, and a protection display that could show `ACTIVE`
without evidence.

### Verified before deploy (Linux/PostgreSQL, CI-equivalent)

- Full suite on the final tree: **2514 passed, 0 failed, 1 skipped**, against a
  disposable `fatty_test`/`testonly` PostgreSQL over a private unix socket
  (matching `.github/workflows/ci.yml`). The skip is
  `tests/e2e/test_postgres_backup_restore.py`, which needs local
  `initdb`/`pg_ctl`; the real restore drills below cover that behaviour.
- Focused hardening set on a second disposable instance: 284 passed, zero skips.
- `ruff` clean on every changed file; frontend `tsc -b && vite build` green.
- Restore drill of a hardened fresh dump (schema 29): `restore_verified` and
  `restore_verification_cleanup_verified`; 19 fills, 63 dispatches, 21 320
  transitions, 356 817 events, 520 notifications readable.
- Migrations 30+31 on a restored production copy: applied in 4.3 s; schema 31;
  five retention indexes; `fatty_worker` non-superuser without
  createdb/createrole/inherit, no CREATE/TEMP on the database, `schema_migrations`
  read-only; the retention delete plan uses `console_snapshot_retention`.
- Retention on real data: the exact delete predicate predicted 44 715 snapshots
  and 44 877 events for an aged slice, and the deployed batches deleted exactly
  those (worst batch 159 ms, no statement-timeout cancels) while keeping the
  newest and newest-good sample per kind/account/environment, the last event per
  replay scope, and every trading-history table. A worst-case 356 k-row aged
  backlog converged (309 517 + 311 939 rows; six bounded cancels, fail-safe).
- New verifier gates covered by unit tests: unbounded logs and a wrong runtime
  database identity both fail closed before any provider probe.

### Deployed and verified

| Check | Observed |
|---|---|
| Revision and schema | `668d82b`; schema **31** (migrations 30 and 31 applied 2026-10-16 02:37:54 UTC) |
| Containers | all ten healthy after recreation, no restarts |
| Container logs | `json-file:10m:5` on all ten services, postgres included |
| Runtime database identities | eight services as `fatty_worker`, web as `fatty_console_web`; none superuser/createdb/createrole/inherit |
| Runtime verifier | `runtime_check=PASS` (policy, source lineage, log bounds, role boundary, ledger readiness, read-only provider probes) |
| Ledger readiness | `ready`, no issues |
| Console telemetry retention | cycling every 60 s as sanitized UTC lines (`status=completed`, 0 deletions: nothing is older than the 7-day/48-hour windows yet) |
| Privilege boundaries | `fatty_worker` 269 DML grants with `TRUNCATE=false` and no `schema_migrations` DELETE; web keeps its push grants while database CREATE/TEMPORARY are revoked |
| Latches | both remain released/inactive; this deployment cleared no latch |
| Account | fresh authenticated flat proof; no positions, no pending orders |
| MAGIC dispatch | unchanged `REJECTED`, one attempt, zero intents/reservations — no replay |
| Transport | public origin serves HSTS `max-age=31536000`, `nosniff`, `no-referrer`; `/health/telemetry` ready |
| Post-deploy backup | `fatty_trader_20261016T024031Z_viHI0u.dump`, mode 0600 |
| Post-deploy drill | `restore_verified` and `restore_verification_cleanup_verified`, schema 31; 369 276 snapshots / 369 354 events readable |
| Host artifacts | backup unit reinstalled with `UMask=0077`; ops-worker venv package byte-identical to the deployed source |

### Explicitly unproven

- No new signal has arrived since the ARC signal of 2026-10-15 12:34 UTC (newest
  intake message 2026-10-15 16:12 UTC; catch-up armed every 60 s), so the next
  naturally arriving signal's end-to-end execution and audit trail remain
  unverified.
- Host CPU steal (hypervisor contention) is an external capacity risk that
  application changes cannot remove.
- The broad Windows suite fails only on platform-inherent contracts (process
  identity via `/proc`, POSIX signals, ownership and modes); the eight
  `test_auth_health_*` cases and the script-contract tests were fixed in the
  follow-up below.

### Follow-up: verification gates and Windows developer runs (2026-10-16)

All three gates in `.github/workflows/ci.yml` were red on `main` at `018bb1e`:
`ruff check` reported three over-long lines, `ruff format --check` fifteen files,
and `mypy src` thirteen errors. `3171111` and `100379d` make them pass on Linux
and on Windows:

- formatting is mechanical. The type fixes are behaviour-preserving: narrow
  catalog symbols to `str` before `.upper()`/`.isascii()`, annotate the
  mixed-value sanitize/notification/evidence dictionaries, assert the
  already-claimed dispatch inside its transition/reject closures, and use
  `getattr(signal, "SIGKILL", signal.SIGTERM)` with platform guards on the
  Unix-socket worker-health paths.
- `tests/support.py` resolves a real POSIX bash (Git Bash on Windows, never the
  WSL launcher), gives child processes the platform paths they need, maps
  `python3` to the interpreter running the tests, and probes whether the host's
  Compose CLI can render a project at all.
- contracts that are POSIX-inherent skip elsewhere with a precise reason;
  `pythonpath = ["."]` makes the `tests` package importable.

| Gate | Linux | Windows |
|---|---|---|
| full suite | **2514 passed, 0 failed**, 1 skipped (needs local `initdb`) | **2082 passed, 0 failed**, 433 skipped (platform contracts) |
| `ruff check` / `ruff format --check` | pass / 325 files formatted | pass / 325 files formatted |
| `mypy src` | pass (123 files) | pass (123 files) |

`3171111` and `100379d` were rebuilt and redeployed so the copied-source digest
matches the host checkout again (`runtime_source_digest=d7a4e74e…`), the runtime
verifier passes from the deployed revision, schema remains 31, the staged rollout
repeated the authenticated flat-account gate, and no signal was replayed.

## LIVE margin cap raised to 1.1 USDT (2026-10-17)

Owner directive: the 2026-10-16 NEAR signal was refused and must be fixed.

**Why it was refused.** Dispatch `7d8d9fd3-1dbc-4679-884b-a10741c2854e` (message
#16314, received 13:20:51 UTC) reached the 25/75 split route because price had
passed the signal entry inside the 60-second window. That route needs a total
quantity on a four-step venue grid: 4 NEAR = 20.88 USDT notional = 1.044 USDT
margin at 20x, while the pinned 1 USDT cap afforded only 3.83 NEAR. The grid
quantised to zero and the dispatcher refused with `split-sizing-refused` before
submitting anything (`provider_effect: not_submitted`; no live intent, no
reservation). Reproduced with the deployed code plus live NEARUSDT metadata and
maintenance-margin tiers: the refusal tag is
`split-market-min-qty: child 0 < min_qty=1`, and the acceptance boundary is
1.04 USDT (refused) versus 1.045 USDT (accepted, qty 4, margin 1.044).

**Change.** `_LIVE_MAX_MARGIN_PER_TRADE_USDT` and both Compose pins are `1.1 USDT`;
the startup guard still rejects any other value, so a mismatch fails closed. The
console hint, user guide, operations doc, MMR/cap doc and the contract tests
follow. At 20x this covers symbols whose `size_step x price <= 5.5`; the split
route is still refused — never approximated, never replaced by a full-market
entry — for pricier symbols.

**Verified before deploy:** Linux full suite `2515 passed, 0 failed, 1 skipped`
(needs local `initdb`), `ruff check`/`ruff format --check` clean (325 files),
`mypy src` clean (123 files), Windows full suite `2083 passed, 0 failed`.

**Deployed:** revision `caa6377`; ten containers healthy; `runtime_check=PASS`;
runtime source digest `fa2b2111...`; schema unchanged at 31; the staged rollout
repeated the authenticated flat-account gate and replayed no signal. All four
Bitget consumers report `BITGET_MAX_MARGIN_PER_TRADE_USDT=1.1`.

**The NEAR signal itself was not traded.** The channel edited message #16314 at
13:21:41 UTC (`edited-source-message` veto), its entry window closed at 13:25:51
UTC, and price then traded to 5.02-5.05 — below the original 5.095 stop, so a late
entry would place the stop above the entry. No manual entry was placed and none is
pending.

## Source-edit veto scoped to real text changes (2026-10-17)

Owner report: the STBL signal — and every signal before it — stopped triggering.

**Three stacked blockers, in the order they were found.**

1. *Edit veto (systemic, fixed here).* Every message since 2026-10-15 carried
   `entry_rejection_reason='edited-source-message'` — 5 of 5 on Oct 16, pure
   chatter such as `Goodmorning❤️` included — and the shared eligibility predicate
   treats such a row as a permanent message-level veto, so no signal could reach
   dispatch. The intake set that reason for any Telegram `edit_date`, and the
   publisher edits every post (chart attachment/formatting). Across all ten
   messages since Oct 15 the signal text never changed
   (`count(DISTINCT md5(raw_text)) = 1` per message; only the media identity
   differed), so the veto blocked 100 % of signals while protecting nothing.
2. *Split sizing (fixed in `caa6377`).* NEARUSDT needed a 4-step split quantum
   (1.044 USDT margin) against the pinned 1 USDT cap.
3. *Venue admission latch (active, owner approval required).* `venue_kill_switches`
   holds `bitget` latched with reason `clock-skew-unavailable` since
   **2026-10-16 17:55:35 UTC**, so no entry can be admitted at all. The clock is
   healthy now (offset −115…−127 ms, RTT 260–420 ms against the 5 000 ms limit)
   and the account is flat, but the documented recovery is owner-approved and has
   not been run.

`#STBL` is additionally unlisted: 0 of 834 Bitget USDT-FUTURES contracts and 0 of
3 496 spot symbols, so even without the veto it could only have been rejected as
`contract-not-found`.

**Change.** `edit_date` is provenance (`telegram_messages.source_edited_at`,
migration 32), not a veto. Each revision is classified against the retained ones:
the first revision observed stays eligible (the entry window still runs from
publication), a same-text revision is stored as `superseded-source-revision`
(audit-only, never a second ENTER revision) and leaves the retained revision's
authority intact, and a revision whose text differs revokes every revision of that
message, reverted content included. The console source and dispatch views expose
the new column. Historical rows are untouched and no signal was replayed.

**Verified before deploy:** Linux full suite `2517 passed, 0 failed, 1 skipped`
(needs local `initdb`); `ruff check` and `ruff format --check` clean (325 files);
`mypy src` clean (123 files). The intake contracts cover all three paths.

**Deployed:** revision `1c8cc05`; schema **32**; ten containers healthy; runtime
source digest `37cf20fd...` matches the host checkout in every container; ledger
readiness `ready`; provider read-only probe PASS. The runtime verifier stops at
`runtime_blocked=kill_switch_active action=preserve_latch` — that latch, preserved
by design, not a deployment failure.

**Latch released (2026-10-17 06:04 UTC).** Under the approval reference
`owner-unblock-live-20261017`, the documented recovery ran: mutation consumers
stopped, dry-run proof reviewed — fresh clock offset **+2 ms** against the 5 000 ms
limit, flat inventory across USDT/USDC/COIN futures, public and private V2 streams
authenticated with fresh pongs, ledger readiness clean, resource latches locked and
re-validated — digest `48f6beee...` applied, consumers resumed, and the full
runtime verifier now reports `runtime_check=PASS`. `venue_kill_switches` reads
`bitget: released:owner-unblock-live-20261017`; entries are admitted again, so the
next listed signal dispatches with the intake fix and the 1.1 USDT cap live.

## Split-leg binding, leftover reconciliation and single-character tickers (2026-10-17)

**Split-leg reservation binding — `1f269f7`, schema 33.** A 25/75 entry submits two
legs under one reservation, and the composite FK compared the child's
`<root>-limit` client order id against the reservation's root id, so every split
entry died with `ForeignKeyViolation`. The ENA signal (#16318) failed this way
before any POST left the host. The binding now compares a generated column that
strips the split suffix, while every other guard (wrong root, snapshot, symbol,
margin) still refuses. 45 targeted, 2174 unit and 344 e2e tests pass on Linux.

**Leftover cleared through the approved baseline.** The failed attempt left an
expired reservation with no entry intent, which made the dispatcher report
`recovery-blocked issues=reservation-without-entry-intent` — readiness false, so
no entry could be admitted on restart. Cleared with the approved baseline script
(`hernanda-approved-clean-baseline-20261017`; reviewed dry-run digest
`6295a40d…`, exactly one reservation plus its dispatch, nothing newer). The
dispatcher is healthy, the orphan count is 0, the venue inventory is flat, and
retiring the row released its canary slot.

**Single-character tickers — `34e23db`, schema 33.** The source posted
`#Q $Q SHORT TRADE / ENTRY: 0.0707 / TARGET: 0.0494 / STOPLOSS: 0.0733`. Every
pair-token gate required two or more characters, so no canonical signal was
produced and the setup fell to `MANUAL_REVIEW` even though `QUSDT` is a listed
contract. Widened to one character in the deterministic parser, the
`CanonicalSignal` model and the trade-management symbol patterns; admission still
validates the contract at the venue, so an unknown token cannot become an order.
2176 unit and 51 analyzer/intake e2e tests pass, and the deployed container parses
the message into `pair_token='Q'` with its levels.

**Both revisions deployed:** `34e23db` on the host checkout with all ten
containers rebuilt and healthy, schema 33, runtime verifier `runtime_check=PASS`,
venue flat (0 positions, 0 open orders).

**Open observations.** Image analysis is enabled and healthy — a probe against
the stored #16319 chart returned a complete setup in 22 s against the 30 s cap —
so the two `FAILED` image results were transient timeouts. The explicit-text
fallback is the designed safety net and now covers single-character tickers. Two
September source-management updates (PUMPUSDT, PENGUUSDT) stay
`reconciliation-pending`; they gate only channel-driven TP/close actions, not
signals, dispatch or the monitor. No console action can retire them yet —
follow-up: `source.reconcile`.

**Source-management retire on a proven-empty venue — `671ddc2`.** The two rows
above (PUMPUSDT from #16224, PENGUUSDT from #16228) carried durable close intents
and had been re-claimed every 30 s for ~2.5 weeks: their source windows closed, so
`management_is_eligible` is false, and the stale-close branch had no terminal path
— it only re-raised `reconciliation-pending`, while `claim()`'s stale cleanup
cannot touch rows that own a provider intent. GET-only readback now retires such a
claim when the symbol shows no position, no pending provider order and no
non-terminal owned entry claim; unproven readback or surviving work keeps it
pending, and no POST is ever repeated. Both rows read `reconciled` within one poll
of the rollout, non-terminal source updates are 0, and the worker prints
`state=idle`. 2179 unit tests, mypy strict and ruff check/format pass.

**Same-day pipeline audit.** Dispatches are terminal (REJECTED 36, FILLED 11,
EXPIRED 10, RECONCILED 3); live intents terminal (filled 12, rejected 8,
reconciled 4); reservations 0; canary in-flight 0 of 5; unresolved protection 0;
dispatcher `idle`; verifier `runtime_check=PASS`; ten containers healthy. ENAUSDT
`SL_TO_ENTRY` from #16322 ("move sl to entry on $ENA") is terminal `failed` with
no position to manage, and the 19 messages still in `RECEIVED` all carry the
documented `edited-source-message` veto (newest 2026-10-16) — inert history, not a
backlog.

**Operator grammar for one-character base assets — `b039b3b`.** Same >=2-character
assumption as the source parser: `/trade … QUSDT …` was rejected by
`operator/command_parser.py` before it could become a typed command. The live entry
path was never affected — `bitget_dispatcher.py:165` normalizes tokens through
`_bitget_symbol` (`Q` -> `QUSDT`) before `service.py:536` validates the symbol — and
the catalog-evidence regex (`metadata.py:77-88`) only records `requested_symbol:null`
for such tokens. Deployed and probed inside the running container: `pair= QUSDT`.
2180 unit tests, ruff and mypy strict pass.

**Deep-audit findings (2026-10-17), punch list.** One defect class across all three
incidents: a durable fence with no terminal transition for the provably-moot case,
no deadline/attempts, no alert, and (for two gates) no exit except an operator.
Priority items: (1) deadline/attempts/evidence columns plus proof-based sweepers for
`source_management_updates`, `console_jobs` and `dispatches` — copy the
attempts/`next_attempt_at` pattern already proven by notifications/web-push; (2) a
watchdog that alerts on non-terminal rows older than X, `recovery-blocked`, canary
exhaustion, stale leases and health staleness (parked rows are log-only today);
(3) runbooks for the two human-only gates (baseline account/environment mismatch;
non-allowlisted latch, which the approved recovery refuses by design); (4) fix the
`services["lifecycle_recovery"]` field that nothing writes, and expose the live lane
in web health (`web/health.py:14` covers only postgres/intake/analyzer/notification);
(5) CHECK constraints for `dispatches.state`: the DB currently holds `RECONCILED`,
a state absent from `domain/enums.py`. Also recorded: no daily-loss/drawdown stop and
zero-default slippage floors (`domain/models.py:94-99`), stop-the-world rollouts that
silently age signals out of the 5-minute window, `MANUAL_REVIEW` has no requeue path,
and container logs are lost on recreation.

**Health card truthfulness — `a0f87f0`.** The six-hourly Telegram card could never be
ONLINE: `scripts/health_report.py::get_service_status` produced `Lifecycle UNKNOWN`
unconditionally and only upgraded it to `BLOCKED` when a *restarting* dispatcher
logged a phrase the service no longer emits, so the verdict stayed DEGRADED while
every other line was green. Lifecycle now reads the dispatcher's own durable issue
predicates (orphan entry intent, reservation without entry intent, unresolved
protection, baseline account/environment binding) plus the latest dispatcher cycle
line from a bounded five-minute window: READY requires that evidence, a block reports
the dispatcher's exact issue list, a non-running dispatcher blocks explicitly, and an
unreadable cycle reads UNKNOWN rather than an inference. The safety-evidence block
also stopped claiming UNKNOWN where durable evidence exists: stored stream rows render
as `STORED · <symbol> <state> · <age>` (never as a live probe, never as latch-release
proof), an allowlisted active latch renders `RELEASABLE`, no latch renders `CLEAR`,
the fallback gate renders the effective flag, and the monitor line reflects container
health. The operator-container `/health` card shares the same loader, so it now
carries a truthful `Lifecycle` too (its service counts stay unprobed by design).
2187 unit tests, ruff check/format and mypy strict pass; verification rendered the
card from the deployed tree: `🟢 ONLINE` with `Lifecycle READY`, `Recovery CLEAR`,
`Fallback gate DISABLED`, `Stream now STORED · PUMPUSDT HEALTHY · 0s`.
