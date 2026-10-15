# Autonomous console UX redesign — implement, verify and deploy

## Mission and authorization

You are the lead implementation and integration agent for this repository. Execute the complete console frontend UX redesign, verify it on the actual rendered surface, publish the completed changes, and deploy the verified release to the existing production console.

The owner authorizes autonomous repository development, substantive parallel subagent work, necessary dependency installation using existing project conventions, testing, documentation updates, scoped commits, push to the existing repository/branch, and exposure-safe deployment through the established production release process. Do not ask for routine design choices, implementation decisions, phase approvals or a second confirmation of this deployment authorization. Do not stop at a plan, design artifact, scaffold, passing build, completed phase, commit, push or pre-deployment milestone. Continue until implementation, verification, deployment and post-deployment observations are complete.

This instruction starts implementation and authorizes the scoped rollout. It supersedes the earlier UX documents' **planning-only / deployment-not-authorized** boundary when this prompt is executed. It does not override their product scope, safety invariants, evidence requirements or higher-priority tool/security confirmation requirements. The task is frontend redesign and its necessary release integration, not unrestricted trading or host administration.

The owner does not need to provide further input for ordinary delivery decisions. Use the written plan, current source, repository conventions and tool-accessible evidence. Choose the conservative, maintainable option and proceed. Do not fabricate approvals or claim a real prerequisite is available when it is not.

## Repository and operating context

- Working repository: `C:/Users/it26/AppData/Local/Temp/fatty-console-20261014`.
- Existing Git remote: `https://github.com/hernanda-git/fatty-multi-exchange-trader.git`. Discover the current branch and worktree state; preserve user changes. Do not assume a historical revision is current.
- Existing deployment SSH alias: `fspmi-hostinger`.
- Existing production repository: `/home/valarion/apps/fatty-multi-exchange-trader`.
- Existing private console: host loopback port `18081`, ordinarily accessed through the established SSH tunnel at `http://localhost:18081`.
- Treat these as context to verify, not permission to invent DNS, expose ports publicly, replace credentials, change accounts or use an unrelated application's infrastructure.

## Read first: authoritative work package

1. [UX master plan](BOT-CONSOLE-UX-MASTER-PLAN.md).
2. [Page-by-page specification](BOT-CONSOLE-UX-PAGES.md).
3. [Phased agent execution plan](BOT-CONSOLE-UX-EXECUTION.md).
4. [UX progress and evidence tracker](BOT-CONSOLE-UX-PROGRESS.md).
5. [Repository agent workflow](../AGENTS.md), [trading rules](TRADING-RULES.md), [LIVE policy](PRODUCTION-LIVE-POLICY.md) and [operations runbook](OPERATIONS.md).
6. [Current implementation decisions](BOT-CONSOLE-IMPLEMENTATION.md), [historical console receipts](BOT-CONSOLE-PROGRESS.md), [UI/API contracts](BOT-CONSOLE-UI-API-SPEC.md) and [capability inventory](BOT-CONSOLE-CAPABILITIES.md), interpreted against current source.

Historical API/authentication design statements may predate the implemented password-only console. Current source and current safety policy determine actual interfaces and authority. Old backend test/deployment receipts do not verify redesigned pages.

At startup, update the UX tracker with this execution authorization, the actual source baseline and the earliest actionable package. Preserve the original planning checkpoint and receipts as history; do not silently overwrite them or mark the entire plan implemented.

## Required outcome

Deliver the complete planned professional, restrained, readable console. This is a content/interaction redesign, not a color change or a new generic JSON viewer.

Keep exactly five primary destinations:

- **Overview**: operating status, attention summary, exposure and account/performance.
- **Trading**: positions, orders/fills, protection and dedicated New trade flow.
- **Signals**: inbox, immutable revisions, original text/media and execution trace.
- **Activity**: jobs, review/outcomes, audit, source/manual management and notification/report delivery.
- **Settings**: configuration/history/drafts, integrations, recovery/baselines, services/releases/logs, backups/maintenance and sessions.

Implement all **UX00–UX16**, all **R0–R7** gates and the **23 work packages** currently registered in the execution plan/tracker. Reconcile the current tracker if earlier execution has already completed work; do not blindly redo completed, still-valid packages.

Preserve every supported existing capability and valid entry/deep link. Use dedicated domain views, forms, contextual actions and readable financial reviews. Move IDs, digests and sanitized diagnostics behind deliberate disclosure; keep essential risk, ownership, freshness and consequences visible before decisions. Retire obsolete renderers, catalog paths, dead styles and parallel legacy implementations after their callers migrate.

Follow the master plan's visual direction and accessibility criteria. The Overview should make the account, operating condition, exposure and next useful action understandable without reading backend vocabulary. No gradients, decorative metrics, giant empty card grids, marketing slogans, tiny critical text, unexplained badges or raw object trees as the normal interface.

## Start with evidence, then execute every phase

1. Read the tracker and select the earliest actionable package; initially R0-01 unless actual progress has advanced.
2. Inspect the actual UI and current route, API, action, DTO and safety contracts. Capture the rendered baseline without mutating production. Complete route/action/data/scenario ledgers, not merely a screen-name list.
3. Produce the core-page visual/interaction artifacts and review them against real data limits. The lead agent owns routine design review; do not wait for the owner to choose fonts, spacing, navigation labels or component names.
4. Establish shared shell/session/navigation, observation primitives and command-review/recovery interfaces before page fan-out.
5. Implement every domain package, integrate it, exercise the affected surface and update the tracker. Maintain usable affected paths; no fake fallback or incomplete scaffold counts as delivery.
6. Complete integrated behavioral, responsive, accessibility, visual and compatibility gates.
7. Update actual user/operations documentation, remove temporary scaffolds, commit and push scoped verified changes, build/register the immutable release and deploy safely.
8. Observe the deployed interface and release lineage. Fix regressions and re-exercise affected gates before final delivery.

Known source limitations include `admission_verdict=None`, `net_pnl=None` and incomplete account-wide history in current read models. Verify current source before using these facts. Present independent supported observations and scoped economics honestly; do not invent a green admission verdict, account-wide net P&L, performance chart, quote feed or export contract.

Missing data must result in an honest supported design or a named dependency with source evidence. Do not silently add backend business rules, retry mechanisms, telemetry, new endpoints or provider operations to satisfy a mockup. A genuinely necessary contract change requires an explicit scoped decision and all affected callers/tests; do not smuggle it into frontend work or silently reduce the requested scope.

## Parallel subagent execution

Use the available subagent tooling for independent substantive slices. You are authorized to spawn many subagents where useful, within the runtime's actual concurrency limit. Do not maximize agent count for its own sake or spawn duplicate research/design/implementation work.

- Retain top-level integration, final verification and deployment ownership yourself.
- Establish page IDs, package IDs, shared interfaces, exact allowed files and acceptance scenarios before delegation.
- Use read-only scouts for genuinely unmapped research and implementation agents for bounded investigation-plus-edit slices, following tool policy.
- Give each agent the complete applicable goal, source pointers, financial/authentication constraints, dependencies, owned files, non-goals and observable acceptance criteria.
- Assign exactly one writer per file. Only the integration owner changes common routing, session/request boundaries, shared tokens/primitives, global event handling and command lifecycle wiring unless explicitly reassigned.
- R0 investigation slices may run concurrently. After R2 stabilizes, parallelize independent overview/account, trading, signals/activity and settings/advanced slices as their actual dependencies permit.
- Shared job/readback UI is required by trading before R4 acceptance; do not defer its necessary results path to an unfinished R5 page.
- Workers must not run overlapping builds, linters, formatters or tests against half-integrated changes. Integrate first; the integration owner runs the agreed checks afterward.
- A spawned agent's completion is a handoff, not proof. Inspect its artifact, integrate it, verify the observable behavior and record actual evidence.
- Keep making progress while agents work. Use completion notifications; do not poll or idle when independent work remains. Reassign/fix failed slices and continue.
- Preserve concise handoffs in the tracker so another agent/session can resume without relying on private chat memory.

## Non-negotiable financial and authentication boundaries

The existing console uses **password-only authentication**. Preserve the already configured password and salted verifier. Do not rotate, disclose, copy into this prompt, commit, log or persist the plaintext password. Do not introduce username, passkey, invitation, OTP or external login. Financial review derives consent from the existing session, with no additional authentication factor or password prompt.

Preserve opaque scoped sessions, expiry/revocation, CSRF/session rotation, exact Origin checks, rate limits, server authorization, resource/policy binding, current financial confirmation phrases/references and durable operation recovery. The sole shared owner cannot become two independent approvers; Tier 3 restrictions remain honest and enforced.

Never change these to make the UI appear healthy or easier:

- LIVE/LIVE/1 policy, risk caps, ownership/baseline exclusions or source-freshness rules.
- Incident latches, clock thresholds, admission rules, independent provider/fallback/stream mutation gates or protection policy.
- Exact-owned cancellation, intent-before-POST, reduce-only limits, split/source management semantics or GET-only ambiguity reconciliation.
- Host allowlists, secret isolation, public/private ingress, provider account binding or database privilege boundaries.

No production test trade, protection change, notification send, credential rotation, restore, historical replay, incident-latch clear, risk expansion, account adoption or production session revocation is authorized as redesign verification. Do not stop monitoring/protection casually to release frontend assets. Follow the established exposure-aware release procedure and preserve current containment.

Unknown is not zero; stale is not fresh; partial inventory is not flatness; SSE connectivity is not trading readiness; acknowledgement is not resolution; accepted/queued is not a completed provider effect. An uncertain effect must keep its original operation identity and use authoritative receipt/readback, never a new-key financial retry.

Use an already authorized session or secure execution context for authenticated checks. Do not bypass authentication, extract protected secrets or introduce a temporary production login. If required authenticated access truly cannot be obtained through existing authorized tools/context, complete other reachable work and record the exact missing prerequisite rather than inventing a successful check.

## Verification: observe the real product

Follow the exact package gates, UX page scenarios and B01–B18 shared scenarios in the execution plan. Re-read actual manifests before running commands; do not invent absent frontend test/lint scripts.

Required evidence includes:

- Production frontend build on integrated source; relevant existing API/backend contract checks in an isolated environment where affected or required by release policy.
- Actual built application served with the real API/database in an isolated safe setup for authenticated and durable-command scenarios. A standalone fixture page, component screenshot or successful JSON response is not integrated UI proof.
- All 17 pages, secondary/details, preserved routes, direct reload, back/forward, supported filters/cursors, source/media and export paths.
- Password refusal/acceptance in isolation, expiry/logout, protected-content clearing and correct CSRF refresh after consent/session rotation.
- Zero/unknown/empty/filtered-empty/loading/stale/partial/unavailable/denied states and independent provider/ledger freshness.
- Dedicated action forms, exact-target review, preview invalidation, backend refusal, queued-versus-confirmed outcomes, partial effects and same-operation recovery after timeout/reload.
- Light/dark at 1440, 1024, 768 and 390 CSS-pixel widths; keyboard/focus, contrast, touch targets, 200% zoom and 320px reflow as specified.
- Isolated credential-free command scenarios with visibly non-production fixture provenance; no synthetic financial data mixed into a LIVE view.
- Old/new bundle, API, auth, pending storage and in-flight job compatibility, including the safe rollback path.

Keep permanent tests only for plausible behavioral regressions. Do not freeze incidental wording/layout or test copied wiring. Remove temporary harnesses after proof. Report exact checks run and actual outcomes, including failures and their resolution. Never call an unrun check passed or substitute a build for browser evidence.

Human five-second comprehension and subjective approval cannot be fabricated by automation. Perform rigorous autonomous visual/interaction review, fix objective issues and finish deployment when objective safety/release gates pass. Record human feedback as **NOT EVALUATED / PENDING** if no human participated; do not ask the owner to unblock routine delivery, claim human acceptance or call the separate human-feedback lane completed. This prompt explicitly allows objective completion and safe deployment with that lane pending.

## Production release and deployment

Deployment is part of this mission, not a follow-up recommendation. Use existing repository/server evidence to discover the exact current release process and execute it within this authorization.

1. Confirm the exact production target, current source/image/schema, private access, policy values and incident state using fresh read-only observations. Preserve user changes and unrelated services.
2. Resolve all mandatory objective release blockers. Do not deploy broken routes, misleading financial state, failed review/consent, lost idempotency recovery, credential exposure or critical accessibility defects.
3. Commit only completed scoped changes; push normally to the established branch. Never force-push, reset unrelated work or publish secrets.
4. Build/register an immutable artifact using the existing packaging and release catalog. The frontend is currently packaged in the application image; do not assume a hot asset swap or invent CDN/public hosting.
5. Retain the known-good artifact and required verified backup. Follow actual schema/API/cache/asset/session compatibility and exposure-aware maintenance prerequisites. Preserve ongoing protection and pending jobs; do not change financial state to force a convenient release window.
6. Deploy the verified artifact through the established mechanism. Restart only required consumers with the established safety procedure; no unrelated infrastructure changes or destructive database rollback.
7. Verify actual deployed artifact/source identity, required process health, private ingress, unchanged safety/risk/latch/gate values and browser-rendered pages. Use read-only financial observations; no test economic effect.
8. If a release regression appears, fix and safely redeploy or use the verified rollback procedure. Rollback must not restore old financial state, replay commands, down-migrate the database or discard unresolved operation identities.
9. Record deployed lineage, evidence, failures/resolution and remaining external observations in the UX tracker and applicable operations/user documentation.

Do not stop after push, image build or service restart. A deployed bundle must actually render and behave as expected. If a mandatory operational prerequisite is genuinely unreachable after tool-based investigation, do not bypass it: finish every other reachable item and record the exact deployment blocker, evidence of attempts and safe next action. Never claim deployment occurred when it did not.

## Progress, autonomy and stopping conditions

Maintain `BOT-CONSOLE-UX-PROGRESS.md` continuously: owner, dependencies, work state, separate proof levels, page/scenario coverage, exact evidence, failed checks, blockers and next actionable package. Update after each completed mutation/verification, not only at final delivery. Keep historical receipts and distinguish current observations from old ones.

Do not stop because a phase ended, an agent returned, the interface compiles, a screenshot looks plausible or routine choices remain. Continue through all actionable work. Do not leave placeholders, disabled fake features, unfinished migration paths, deferred integration or an unexplained subset of the requested scope. Do not ask the owner for repository/tool-provided information.

Complete means:

- [ ] All UX00–UX16 and all objective R0–R7 package gates are integrated and verified.
- [ ] Every existing supported route/tab/action is retained or cleanly migrated with evidence; unsupported authority remains explicitly unavailable.
- [ ] The professional visual/interaction direction is implemented consistently, with no obsolete primary renderer/catalog/legacy UI left behind.
- [ ] Password-only access and all financial/host/security invariants remain unchanged.
- [ ] Actual browser workflows, failure states, responsive/accessibility checks and justified contract regressions pass.
- [ ] No scaffolds, fake fallback data, secrets, unresolved mandatory objective defects or lost pending operations remain.
- [ ] Actual user/operations docs, package/page status and evidence records are current.
- [ ] Scoped changes are committed and pushed; the immutable release is deployed and post-deployment observations pass.
- [ ] Human feedback is recorded honestly as actual feedback or pending, never fabricated or used to abandon reachable work.

Final response: concise deployed result; exact published/deployed revision and artifact identity; user-visible improvements; exercised verification; tracker path; any genuine remaining external/human evidence. Do not provide only a plan, say the owner must deploy it, or label unfinished implementation complete.
