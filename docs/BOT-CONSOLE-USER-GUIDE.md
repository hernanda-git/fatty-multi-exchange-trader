# Bot Management Console — operator guide

## Private access and password sign-in

Start `ssh -N -L 18081:127.0.0.1:18081 fspmi-hostinger`, then open
`http://localhost:18081`. Use the configured origin and port for request checks.
The server port remains loopback-only. Public hosting requires separately reviewed
HTTPS ingress and matching origin configuration.

Enter only the configured console password and select **Sign in**. No username,
invitation, passkey, OTP or external sign-in is required. The password is cleared
from component memory on submission and is not stored by the application in browser
storage, URLs, exports or logs. The server stores a salted scrypt verifier.

The shared password establishes one scoped console owner. Sessions, CSRF, exact
origin and rate limits still protect requests. **Sign out** revokes your session;
reloading is not sign-out. Access controls expose the owner policy and owned sessions,
not invitations or unusable identity-management flows. A single shared identity
cannot supply independent two-person approval, so those dangerous actions remain
unavailable under the existing financial policy.

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

| Primary destination | Secondary views & operational use |
|---|---|
| **Overview** (`/overview`) | High-level operating status, admission condition, active containment alerts, portfolio exposure, and drill-down into Account & Performance (`/account`, `/account/pnl`, `/account/reservations`, `/account/admission-snapshots`). |
| **Trading** (`/positions`) | Active owned positions (`/positions`), working orders & intents (`/orders`), fills & ledger economics (`/orders?view=fills`), native SL/TP & fallback protection (`/protection`), and dedicated New manual trade entry (`/trading`). |
| **Signals** (`/signals`) | Telegram signal inbox (`/signals`), immutable original message texts, authenticated media attachments, parsed revisions, and evidence-linked dispatch traces (`/signals/{id}`, `/signals/dispatches/{id}`). |
| **Activity** (`/audit`) | System audit trail (`/audit`), durable command jobs (`/audit?view=jobs`, `/audit/jobs/{id}`), approvals & Tier 3 governance (`/audit?view=approvals`), trade management instructions (`/management`), and notification deliveries (`/notifications`). |
| **Settings** (`/config`) | Effective settings & encrypted secret staging (`/config`), drafts & revisions (`/config/drafts`, `/config/history`), safety latches & recovery proofs (`/recovery`, `/recovery/baselines`), container services & logs (`/services`), release catalog (`/services?view=releases`), backups (`/backups`), and password-only access policy & sessions (`/access`). |

### Protection evidence and legacy registrations

**Native protection verified** requires a fresh `VENUE_PROTECTED` observation,
not a fallback registration or a missing field. The table ages observations even
without new stream events; missing or expired evidence is unverified/stale.
An empty position list is not proof that the provider account is flat.

The protection page separately lists historical fallback registrations with
incomplete environment, owned epoch or position identity. These are ineligible
for close enforcement, not new open positions or proof of active protection.
Their historical database state is preserved. **Review native repair** opens the
repair authorization flow; it does not immediately close a position.

### Automated signal sizing defaults

In **Settings → Configuration**, use **Trading & signal settings**. Fields show
observed consumer settings; an unobserved value is left blank rather than guessed.
Only edited fields are submitted, and the current revision is supplied automatically.

- **Percentage** uses total USDT equity, then limits margin to available funds and
  the existing **1.1 USDT per-trade safety cap**. For example, 20% of 3 USDT equity
  with 0.80 USDT available requests 0.60 USDT margin.
- **Fixed USDT** requests the entered amount, subject to available funds and the
  same cap. It is not reduced by the percentage setting.
- **Default leverage** accepts integers from 5x through 20x. The sizing engine may
  reduce leverage when the stop needs more liquidation headroom.
- These defaults affect new automated signals only. Manual order-ticket defaults
  and existing positions are unchanged.

Select **Save**, review the inline runtime changes and safety gates, complete the
required owner confirmation and approval reference, then select **Save** again.
There is no separate draft/apply command. The durable `config.save` job performs
the safe service transition, persists the private Compose override, and verifies
the affected consumer environments. Unsafe or nonflat inventory refuses disruptive
application; the existing safety cap and authorization policy remain enforced.

Queued does not mean active: wait for **Saved and effective**. An interrupted
submission retains its idempotency key; **Retry Save** recovers the same operation.
If status observation fails, use **Check save status**, not a new save. A pending
reconciliation is not confirmed success. Credential replacement remains a separate
advanced encrypted staging/draft operation.

## Preview, authorize, queue, observe

1. Select the action from **Action catalog**, fill typed arguments, choose only exact
   versioned targets when applicable, and state the operational reason.
2. Generate the read-only preview. Review normalized parameters, scope, targets,
   ownership, effects, exclusions, gates, warnings and expiry. A preview does not
   reserve funds or change margin mode/leverage.
3. Acknowledge the review. Tier 2 requires the exact financial confirmation phrase,
   approval reference and **Authorize reviewed digest**, which obtains consent from
   your existing password session without another password or factor prompt. Tier 3
   remains unavailable because the shared owner cannot provide a distinct approver.
4. Queue the exact operation once. HTTP acceptance/queued state is not execution.
   Follow the job, checkpoints, claim and action-specific readback.
5. After input/resource/evidence changes or expiry, make a new preview only if the
   prior operation is proved unaccepted. Never reuse consent for a changed operation.

A lost response retains the same operation ID and public request in session storage,
not passwords, credentials or CSRF tokens. Use **Recover same operation** to retrieve the
authoritative idempotency receipt, or retry the identical original operation. A new
idempotency key is not a financial retry mechanism. `reconciliation_pending` means
GET-only readback is still needed; do not place a replacement order. Cancellation
can prevent unclaimed work, but cannot undo an accepted provider effect or abandon
its reconciliation.

## Signal execution audit trail

Open a signal or its linked dispatch to see the **Execution timeline**. Each new
Bitget transition records source/revision and dispatch IDs, worker/attempt, UTC
observation, stage duration, outcome and a machine-readable reason. Expand
**Captured diagnostics** for bounded public catalog evidence, selected contract
metadata, catalog SHA-256, and available sizing/routing observations.

The **Entry-order boundary** distinguishes `not_submitted`,
`submission_may_have_occurred`, and `provider_response_observed`. It does not
describe preflight account preparation, and a provider response does not mean a
fill. An ambiguous submission retains its ownership/reservation evidence; do not
retry it as a new operation.

Related records expose original analysis-notification status separately from
execution alerts, source revisions, intents, reservations and fills. A sent
notification is not a trade fill. New `ingested_at` and
`edit_veto_recorded_at` timestamps distinguish database ingestion and edit-veto
recording from source time. Historical unknown timestamps remain null.

Structured JSON transition logs are emitted after the state/evidence/outbox
transaction commits. Raw provider account bodies, credentials and model prompts
are not captured. Historical transitions without structured evidence are labeled
explicitly; diagnostics are never reconstructed as if they had been observed.

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
