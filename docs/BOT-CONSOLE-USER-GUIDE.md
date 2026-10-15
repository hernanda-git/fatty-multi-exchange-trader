# Bot Management Console — operator guide

## Private access and enrollment

Start `ssh -N -L 18081:127.0.0.1:18081 fspmi-hostinger`, then open
`http://localhost:18081`. Use that exact origin and port: passkeys are origin-bound.
The server port remains loopback-only. Public hosting requires separately reviewed
HTTPS ingress and matching RP/origin configuration.

Select **First access / accepted invitation**, enter the short-lived token received
through the approved private channel, and register a user-verified passkey. The token
is one-use, stored hashed on the server, and never belongs in a URL, screenshot,
Git commit, or browser storage. Enrollment establishes your own authenticator;
a deployment cannot create it for you. Subsequent sign-in uses the same passkey.

Owner has the scoped privileges; operator can manage supported trading/metadata
work; viewer reads evidence. `risk_owner`, `ops_admin`, and `access_admin` separate
recovery, host operations and access management. Server authorization applies to
every API request, event and job, regardless of visible controls. Session revocation
or role changes invalidate further mutation authority. Sign out through the account
menu. Reloading is not a substitute for signing out.

## Read evidence before action

The persistent header identifies account, venue and LIVE environment. Expand clock,
configuration, latches, stream and account/risk evidence independently. A live SSE
connection means the control plane is connected, not that an entry is admitted.
Unknown, unavailable, stale and partial observations do not mean zero exposure or
safe admission. Ledger observations and authenticated provider observations remain
separate. UTC is the default; the display timezone control changes presentation,
not stored times or authorization.

Use desktop navigation or the mobile **Menu**. Filters are server-side and bounded;
a list is not proof of complete provider inventory. Details link durable IDs and
versions rather than adopting an arbitrary symbol as bot-owned.

| Screen | Operational use |
|---|---|
| Overview | Independent health, admission, containment, account and protection evidence; navigate into the exact blocker. |
| Signals & trace | Inspect original receipt/revision, text/media, classification, canonical signal, dispatches, reservations, intents and management. `signal.annotate` attaches an immutable revision-bound operator note; `signal.reanalyze` records a separate analysis-only result. Neither renews freshness or trades historical signals. |
| Trading desk | Review supported contract, quote, sizing, margin/leverage, direction and SL/TP. `trade.open` creates a new explicitly manual command through existing admission and durable ownership. |
| Positions | Compare provider and owned residual; selected partial/full reduce-only close, exact remainder cancellation and protection changes. Baseline/external exposure is not silently adopted. |
| Orders & intents | Inspect entry legs versus protective/reduce-only orders and fills. Cancel only the exact owned entry or owned remaining entries. Reconcile ambiguous existing intents without another POST. |
| Protection | Inspect native plan identity, trigger and quantity against actual fills; repair missing protection or change supported SL/TP. Fallback operations retain independent mutation and epoch gates. |
| Management | Trace source/manual management and cancellation-before-close outcomes; reconcile existing requests. A queued source instruction is not an execution confirmation. |
| Recovery & safety | Inspect faults, latches, baselines, unresolved ownership and authenticated inventory. Acknowledge an exact incident without clearing it. Pause entries separately from an incident latch; proof/release requires the exact approved binding. |
| Account & PnL | Current provider account, admission snapshots, reservations and ledger economics. Unavailable fees/funding or incomplete evidence stays unknown. |
| Services & deployment | Actual host/container observation, bounded logs, approved release catalog and maintenance plans. No arbitrary shell, path, Git ref or image tag input. |
| Configuration | Immutable desired drafts versus independently observed effective consumer configuration. Stage replacement secrets through the write-only encrypted form; never paste credentials into reasons or general settings. |
| Notifications | Observe outbox/attempt outcomes; queue configured test/health delivery or explicit eligible retry. Duplicate delivery risk must be accepted; retry never replays a trade. |
| Audit & jobs | Inspect actor, immutable arguments/digest, approvals, lifecycle, claims and readback. Export bounded JSON/CSV through `report.export`; CSV escapes formula-leading values. |
| Backups | Create, catalog and verify integrity; deletion and restore require their reviewed prerequisites. Restore refuses changed trading evidence and never downgrades schema or replays signals. |
| Access | Inspect principals, scopes, sessions and policy. Role/invitation changes and session revocation use durable commands. Owner grants require a distinct eligible enrolled approver. |

## Preview, authorize, queue, observe

1. Select the action from **Action catalog**, fill typed arguments, choose only exact
   versioned targets when applicable, and state the operational reason.
2. Generate the read-only preview. Review normalized parameters, scope, targets,
   ownership, effects, exclusions, gates, warnings and expiry. A preview does not
   reserve funds or change margin mode/leverage.
3. Acknowledge the review. Tier 2 and 3 require the exact confirmation phrase,
   an approval reference and fresh passkey consent bound to that digest. Tier 3
   additionally requires a different eligible scoped owner; self-approval is refused.
4. Queue the exact operation once. HTTP acceptance/queued state is not execution.
   Follow the job, checkpoints, claim and action-specific readback.
5. After input/resource/evidence changes or expiry, make a new preview only if the
   prior operation is proved unaccepted. Never reuse consent for a changed operation.

A lost response retains the same operation ID and public request in session storage,
not credentials or passkey assertions. Use **Recover same operation** to retrieve the
authoritative idempotency receipt, or retry the identical original operation. A new
idempotency key is not a financial retry mechanism. `reconciliation_pending` means
GET-only readback is still needed; do not place a replacement order. Cancellation
can prevent unclaimed work, but cannot undo an accepted provider effect or abandon
its reconciliation.

## Incident handling

`incident.acknowledge` records who reviewed the current latch reason. It does not
clear the latch, change risk or enable entries. A changed reason invalidates the
preview. `admission.pause` blocks new ENTRY admission while monitoring, protection,
closes and reconciliation continue; it is not DEMO or execution-disabled mode.
`admission.resume` refuses active incidents and stale/unready independent evidence.
Containment preserves an already active incident reason.

For clock/stream/ledger incidents, inspect evidence and use the existing scoped
recovery runbook. Do not clear a recurring clock latch to make the UI appear healthy.
Flat-only recovery and disruptive maintenance refuse unknown/nonflat inventory.
Account-wide cancellation/flattening, external-position adoption and protective
cancellation without an approved replacement remain prohibited under current policy.

## Maintenance, release and rollback

Read [the host operations boundary](OPERATIONS.md#console-host-operations-boundary)
for environment, user-service, private key/spool, catalog and filesystem requirements.
Create `maintenance.prepare` for only the allowlisted consumers and no-replay policy.
Disruptive operations require fresh authenticated flat evidence and preserve existing
protection. Host jobs run in a separate privileged executor; web has no Docker socket,
provider credentials or private decryption key.

A deploy/rollback selects a registered immutable release ID, exact consumers and
maintenance plan. Its image/source/Compose/schema digests must match; tags/branches
are refused. Keep previous images and a verified backup before rollout. Rollback
never down-migrates PostgreSQL. Do not restore an old database after later provider
claims/fills; reconcile forward instead. An ambiguous host receipt is reobserved,
not replayed, even if the database was restored.

## Verification and support evidence

Record the request/job ID, account/environment, resource version and UTC observation
time. Share sanitized evidence, never tokens, assertions, credentials, raw environment
or private keys. See [progress and receipts](BOT-CONSOLE-PROGRESS.md) for exercised
verification and deployed identity; the existence of this guide is not runtime proof.
