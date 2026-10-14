# Bot Management Console — progress and evidence tracker

Created 2026-10-14. Approved scope: [master plan](BOT-CONSOLE-MASTER-PLAN.md). Specification: [UI/API](BOT-CONSOLE-UI-API-SPEC.md). Source investigation: [capabilities](BOT-CONSOLE-CAPABILITIES.md).

## Status vocabulary

NOT STARTED: no work; ACTIVE: actionable work underway; BLOCKED: named missing prerequisite; SPECIFIED: reviewed written contract but no implemented behavior; IMPLEMENTED: integrated code exists; LOCALLY VERIFIED: named exercised behavior; DEPLOYED: running lineage confirmed after authorized rollout; ACCEPTED: phase criteria proved. A document or fixture never promotes runtime status. Checkpoints remain historical rather than silently rewritten as current health.

## Owner instruction and authority

Owner: approved master plan and requested comprehensive documentation, phase-by-phase tracker, commit/push, and work to start. Repository work/publication authorized. New UI deployment, financial/provider mutations, live canary, latch clearing, risk changes, optional mutation-gate activation and credentials remain separately approved operations. No server operation is part of this kickoff.

## Baseline

Fresh isolated clone of origin/main `ea0114e136ec1ba4adc547f10a61aa533934221d` at `C:/Users/it26/AppData/Local/Temp/fatty-console-20261014`. Existing audit workspace and its unrelated edits remain untouched. Local source inspection only; last conversation's relatch is not a fresh production probe. Current web exposes health endpoints, not the proposed management API. No console has been built or deployed at this checkpoint.

## Phase tracker

| Phase | State | Completed / evidence | Remaining acceptance |
|---|---|---|---|
| P0 Capability/safety | SPECIFIED | Source-backed capability inventory complete; existing local health routes exercised; manual open refusal, unsafe legacy mutations, preflight side effects and state/ownership gaps documented | Implementation prerequisites remain tracked; no new adapter enabled |
| P1 UI/API contracts | SPECIFIED | All 15 routes/screens, evidence DTOs, permissions, approvals/jobs/idempotency, SSE and operations boundary documented | Validate contracts against implemented auth/read/command adapters in subsequent phases |
| P2 Authenticated read console | NOT STARTED | None | Real auth/session/CSRF, compiled responsive console/read APIs and browser proof |
| P3 Durable shared commands | NOT STARTED | None | Shared Telegram/web cutover, durable approvals/jobs, idempotency/resource-lock/restart proofs |
| P4 Trading/protection controls | NOT STARTED | None | Same-admission manual entries, owned cancel/close/protection/source actions and adapter/readback coverage |
| P5 Recovery/operations | NOT STARTED | None | Recovery/host allowlist/config/secrets/backup/deploy implementations and exposure-safe verification |
| P6 Hardening/release | NOT STARTED | None | All-scope acceptance, security review, operator runbooks, separately approved deployment evidence |

Phases are sequencing, not reduced scope. No phase is accepted because documentation exists; code and runtime gates above still apply.

## Work-package register

| Package | Scope / integration contract | State |
|---|---|---|
| DOC1 | Approved master plan, full screen/action scope, dependencies, phase gates and release invariants | SPECIFIED |
| CAP1 | Actual gateway/parser/service/lifecycle capabilities and missing boundaries | SPECIFIED |
| SPEC1 | Screen interactions, API/DTO/jobs/auth/event contracts, operations privilege boundary | SPECIFIED |
| VERIFY1 | Local read-only runtime and cross-document link/coverage smoke | LOCALLY VERIFIED |
| PUB1 | Commit explicit files, push without force, prove published SHA; preserve unrelated work | ACTIVE |

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

Pending: commit and origin SHA recorded after successful publication. Commit/push is not deployment. Progress records should refer to Git history rather than insert a self-referential commit hash into its own committed file.


## Next implementation gate

After CAP1/SPEC1 integrate, start P2 authentication and first real read surface using shared DTOs. Do not attach financially mutating actions until P3 durable job/shared-command gate passes. Every later work session updates this ledger with completed behavior, exact verification, blockers and next package; do not leave successful work marked ACTIVE or future work marked implemented.

