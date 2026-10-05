# Full remediation verification gates

This branch is a remediation candidate, **not a production-ready release**.
It incorporates the previous branch's complete main delta and local source edits;
passing a narrow test selection does not approve those unrelated changes.

## Execution checklist

- [x] Preserve LIVE dirty work and original branch identity; isolate candidate and integration workspaces.
- [x] Credential-free, network/filesystem-isolated baseline and actual disposable PostgreSQL tests.
- [x] Independent component audits, including the full committed main delta inventory.
- [x] Back up the production database and restore the backup to a separate network-none database; delete restore target afterwards.
- [x] Recover the active position's native market protection using a reviewed documented payload variant, without another entry.
- [ ] Finish TDD remediation and close or explicitly justify every audited finding.
- [ ] Full suite, zero failures/errors/skips; fresh and upgrade PostgreSQL schemas and concurrency/crash proofs.
- [ ] Ruff check/format, mypy, compile, wheel/sdist and secret/diff checks on the same final source hashes.
- [ ] Independent final spec/security review with explicit approving verdict.
- [ ] Push verified commit; CI on the exact remote SHA; PR review and merge to main.
- [ ] Owner-approved LIVE/LIVE/1 image/schema rollout with production policy admission; exact running-source/provider readback.

## Native position-market protection

Bitget's classic `place-pos-tpsl` endpoint documents omitted execute-price fields
or zero as market execution. Some provider validation responses reject the explicit
zero sentinel with `43011 ... presetSLExcutePrice must than 0`.
The approved compatibility payload removes only `stopLossExecutePrice` and
`stopSurplusExecutePrice` after market-only input validation. Trigger values/type,
position side, product/margin coin and stable durable client identities stay intact.
Never replace this with a positive limit price; that can leave an unfilled stop.
Never classify generic parameter `43011` as symbol-unsupported capability.

For ambiguous placement, use GET-only exact OID/plan reconciliation rather than
blind POST retry. A provider fill and protection status are different truths:
`FILLED` must never imply the loss stop is verified. Verify pending plan ID, status,
trigger, symbol/side and market order type even if position TPSL fields are null.

## Runtime safety during remediation

The current [production LIVE-only policy](PRODUCTION-LIVE-POLICY.md) requires
LIVE/LIVE/1 and supersedes historical closed-entry remediation instructions.
Do not disable the entry gate or deploy DEMO. Preserve established kill switches,
protection and independent optional mutation approvals; a persisted fallback row
alone is not working protection. Native exchange plans remain independent of the
local dispatcher process. Do not clear a venue latch, replay a historical signal,
or activate a new entry as a test. Require the rendered-policy checker before
any separately approved deployment.

## Candidate integration contracts

- The production async Bitget adapter accepts a final winning-claim veto after
  awaited leverage setup and immediately before the ENTRY request. An unsent
  stale/blocked request is EXPIRED/REJECTED, not ambiguous provider UNKNOWN.
  Already-submitted uncertainty stays consumed and GET-only; source age does not
  free its financial commitment.
- Startup protection recovery is GET-only. Canonical owned exposure requires
  exact actual fill, position epoch, environment and existing plan evidence.
  Missing protection and manual/orphan/provider-only inventory retain a durable
  admission veto even if an optional readiness callback is bypassed.
- Web readiness reads worker-owned progress from per-service tmpfs mounts (web
  read-only) and proves owner identity with fresh Unix-socket challenges. It does
  not require host PID access; an attestor cannot refresh stale work progress.
  Real isolated containers proved cross-UID permission, stop/death/restart and
  generation rejection on this host. Enforcing SELinux/AppArmor compatibility is
  not established by a host with those policies disabled.
- Verified close release must join exact durable close-fill ownership and fresh
  authenticated flat account evidence, with admission-lock serialization. A
  repository API alone is not evidence that monitor/operator lifecycle is wired.

## Local evidence (not included in Git)

Detailed findings, original baselines, RED/GREEN logs, test snapshots and provider
recovery receipts live under the operator's isolated workspace `artifacts/`.
The original baseline included two explicit negated-entry false positives and
script lint/format failures. Remediation expands test coverage; compare each
revision's own inventory rather than equating pass-count growth with readiness.

Production credential/session files, database dumps, local test environments,
internal agent plans and runtime artifacts must never enter the build context or
commits. Docker context is allowlisted; the verification artifacts directory is
ignored by Git. Packaging and image success are distinct from deployed schema,
running source, authenticated account reads and approval to reopen execution.

## Historical operational receipt (not current configuration authority)

Read-only refresh on 2026-10-02: PUMP flat, no pending PUMP plans; the provider
returned a matching 1880-unit close fill at 0.005426. This receipt does not by
itself attribute the close to a specific plan or establish every bot ledger row.
Dispatcher entry and fallback/stream mutation gates remain closed.

See [lessons and invariants](LESSONS_LEARNED.md) for the recovery and test rules.
