# Bot Console — autonomous implementation and deployment decisions

**UX redesign:** this document describes existing implementation decisions, not
the redesigned interface. The [new UX master plan](BOT-CONSOLE-UX-MASTER-PLAN.md),
[page specification](BOT-CONSOLE-UX-PAGES.md) and [progress tracker](BOT-CONSOLE-UX-PROGRESS.md)
describe a separate, planning-only frontend workstream. Existing source/runtime
receipts must not be reused as proof of redesigned pages.

Owner requested complete autonomous delivery after approving the full plan. This document records design choices made from repository/server evidence; the progress tracker records observed acceptance separately.

## Authentication and access

Server inspection found the existing web exposed only at 127.0.0.1:18081 and no established OIDC identity service or general-purpose HTTPS ingress for this bot. Do not adopt an unrelated application's identity/credentials, provision a public DNS name, or expose an administrative port on all interfaces.

Owner superseded the initial passkey design with password-only authentication.
`POST /api/v1/auth/login` accepts only a password and issues a scoped opaque owner
session. No username, invitation, passkey, OTP or external sign-in remains.
The deployment stores only a salted scrypt verifier in
`runtime/console-password.hash` (0640), mounted read-only into web. Plaintext is
read from provisioning stdin and is never committed, logged or returned.

Default access remains an SSH tunnel to the private web port; public hosting
requires HTTPS. Exact-origin checks, sessions, CSRF, scoped authorization and
durable rate limiting remain. `POST /api/v1/auth/consent` derives expiring,
digest-bound consent from the current password session after effect review,
without a second authentication factor or password prompt. It rotates the session
and CSRF token together. Old passkey sessions, invitations and grants are revoked
at cutover; a hash/scope-derived principal revision invalidates stale password epochs.
Tier 3 distinct-owner policy is not bypassed: the sole shared owner cannot act as
two independent approvers. Telegram may use an existing password session for the
configured operator bridge, but cannot create a login or consent.

## Runtime isolation

Web serves compiled frontend and authenticated read/control-plane API only, with no Bitget credential, Docker socket, arbitrary shell, or host private decryption key. Use separate PostgreSQL web role limited to console storage and SELECT financial projection grants. A dedicated console-worker owns GET collection and canonical trading execution credentials. A separate user-level host operations worker accepts only allowlisted approved jobs and owns OS/service/backup/private-key operations. Existing bot workers and runtime health remain independent.

Frontend is built into the application image by a Node build stage; no secrets, local node_modules, frontend dist or runtime artifacts enter build context. Origin/account are explicit deployment config. Secret stages use encryption with a web-visible public key and runner-only private key, not a symmetric decryption key shared with web. Logs and DTOs are redacted before persistence/exposure.

## Integration and policy

Additive migration26 introduces control-plane records without reinterpreting old financial history. SQL migration splitting must preserve quoted function/trigger bodies so immutable audit and approval constraints actually execute. Manual entries need genuine console provenance and existing risk/reservation/intent/protection ownership, not fabricated source times. Telegram mutation controls migrate to the same durable command authority as the browser.

A dedicated operator admission pause is distinct from a fault latch. It blocks final ENTRY submission but not monitoring, native protection, required close/readback or source reconciliation. Pause is not DEMO or execution=0. Existing recurring clock fault remains independent; this work never blindly releases it or increases timing bounds.

## Verification boundaries

Run complete integrated checks after all independent edits land; prove actual browser surface, local PostgreSQL jobs/auth/claims, lifecycle/provider simulation and security failures. Simulated financial executions are credential-free local acceptance, not live fills. Approved console deployment uses exposure-safe backup/migration/source/policy checks. No synthetic live trade, recurring safety-latch release, unrelated host service mutation or risk/mutation-gate relaxation is used to obtain a green status.

## Durable authority and observation semantics

Preview, job, idempotency receipt and step-up authority are immutable; consumed
consent cannot be restored. Before a new domain claim, the worker checks current
actor and approver session validity, principal revision, exact scope/action,
consumed grants and required approval. Readback is lease-fenced independently of
expired or revoked mutation authority, so incident reconciliation remains possible.
Financial and maintenance work acquire sorted account/exact-resource locks;
identity recovery uses a separate access lock, while read-only reconciliation does
not wait behind an ambiguous mutation. Partial lock acquisition rolls back to a
savepoint without reserving unrelated resources.

Session rotation revalidates the old session under lock, including principal
revision and idle/absolute expiry. A lost enqueue response can be recovered through
`GET /api/v1/jobs/by-idempotency/{key}?action=...`, bound to the exact authenticated
principal and account scope. Job lists and events enforce each job's permissions;
configuration/log observations retain their own read privileges. Event high-water
marks and cursor revisions are scope-bound.

A failed provider refresh retains the original last-known evidence rather than
inventing a fresh observation. Partial results carry `complete: false` and unknown
totals remain null. Staged recovery evidence is visible separately from a proved or
applied recovery outcome. These are implementation contracts pending the acceptance
receipts recorded in the progress tracker.

