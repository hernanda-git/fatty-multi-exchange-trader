# Signal notification evidence contract

## Meaning of a setup card

`signal-analysis` is emitted when a canonical signal and its local dispatch queue
rows are created. It does not read the provider and is not an order submission or
fill receipt. The card must say **antrean eksekusi**, not claim execution, and
state that this report has no provider order/fill confirmation. A later execution
notification is a separate evidence track.

The captured TRUMP case had one queued dispatch, zero attempts, no dispatch
transitions, and no local TRUMP entry intent/fill. Its stored notification was a
`signal-analysis` payload, not an execution success event. The dispatcher was
restarting because startup lifecycle recovery was not ready. This explains why
queue creation could be delivered while execution never started.

## Recovery is not a new trade

`recovery-missing-protection:*` and
`recovery-filled-protection-unverified*` events represent unresolved historical
entry recovery. Render them as **Recovery Diblokir**. Never present an old
`FILLED -> FILLED` recovery transition as a new entry or proof of current
protection. The underlying recorded fill state is retained, not rewritten.

`cutover-gated` proves an execution gate refused an order, not that the provider
mode is DEMO. Report the gate decision without inventing the configured mode.

## Verification and release boundary

Regression tests cover the exact queued TRUMP shape, zero dispatch count,
historical FILLED recovery, and mode-independent cutover refusal. Existing
notification tests retain HTML, redaction, management, and outbox coverage.

This change modifies presentation only. Aggregate health includes all entry-blocking
latch scopes (`global`, `bitget`, `bitget-protection-stream`), dispatcher state,
and lifecycle readiness. Missing dispatcher/readiness evidence must be degraded,
not replaced with a permissive default. The present loaders do not establish a
positive worker-owned lifecycle-ready observation; they therefore cannot claim
ONLINE from absent evidence. A healthy fixture explicitly proves READY/RUNNING
before testing the negative cases.

It does not release safety latches,
replay queued signals, rewrite ownership, place protective orders, or make entry
admission ready. Deploying the notification sender is separate from restoring
live execution; source tests and a rebuilt image are not live-fill proof.

Rollback: revert the presentation commit, rebuild only `notification-sender`,
and recreate that service. Do not change provider modes or execution gates.
