# Bounded Bitget fill, final ENTRY and clock repair

This patch is based on production `b2a41eb`, not a wholesale merge of the older
`04ceca1` audit tree. It changes no LIVE policy, admission limits, ledger readiness,
WebSocket heartbeat behavior, deployment environment, or live database. It does
not authorize ENTRY replay, smoke orders, capacity release, or latch clearing.

## Adopted candidate scope

- `edb0a44`: preserve known exposure on partial pages; prove restart position epoch.
- `55e6594`: bounded recent-fill pagination, `state` order detail normalization,
  durable trade preservation, and independently owned one-way fill direction.
- `20b6ffa`: final source/kill permission transaction spanning the single ENTRY POST.
- `e9d1bde`: authentic fill/position/plan epoch fixtures in real PostgreSQL tests.
- `04ceca1`: provider errors remain errors, not successful empty fill pages.

The prior retry/backoff implementation is deliberately NOT adopted. Production's
HTTP status diagnostics, sanitized GET handling and existing retry behavior remain.
The close-retirement changes in the prior repository snapshot are not adopted.

## Fill read contract

`GET /api/v2/mix/order/fills` freezes `endTime`, requests `limit=100`, and follows
`idLessThan=endId` across short nonempty pages. Only an explicit object containing
`fillList: [], endId: ""` proves exhaustion. The default bound is 20 pages, with
caller bounds 1..100. Missing/null cursor metadata, malformed or repeated pages,
non-descending trade IDs, and bound exhaustion remain incomplete.

Provider HTTP/business failures still raise `BitgetApiError` with the original
code/status. `partial_fill_result` retains the authenticated rows, page boundaries
and unconsumed cursor observed before the failure. Reconciliation can retain these
rows only as UNKNOWN evidence; it cannot infer no fills, complete execution, or a
proved rejection. Empty acknowledged detail is not confirmed 40109 absence.
A zero-row result never proves FILLED, even with a zero requested quantity.

Reconciliation retains durable trade IDs, quantity floors and original economics
instead of synthesizing missing trades or overwriting contradictory trade evidence.
Supplied malformed economics and missing fee/fill-ID evidence cannot establish
completeness. Fee normalization remains the existing canonical nested
`feeDetail[].totalFee`/top-level helper. Contradictory profit or fee currency for a
previously confirmed trade also stays UNKNOWN.

One-way `buy_single`/`sell_single` means direction, not open/close by itself. It is
accepted for opening epoch proof only within independently owned ENTRY order,
client identity, symbol/side, complete quantity, non-reduce-only and non-system
proof. Restart position `cTime` must equal the earliest owned opening fill; plans
must not predate that epoch. Inventory includes the epoch, preventing a same-size
replacement position from inheriting prior protection verification.

Offline signed-transport tests do not verify live provider terminal-page behavior.
If `/fills` refuses continuation, the code retains partial evidence as UNKNOWN and
blocks readiness; it does not fall back to treating a short page as terminal.
The recent-fill API is not a full historical ledger audit or a `fill-history`
endpoint replacement. Historical close receipts remain a separate acceptance track.

## Final ENTRY permission

`entry_permission` locks dispatch, canonical signal and source message first, then
holds a SHARE lock on `venue_kill_switches`. It rereads both global and Bitget
switches and recomputes source eligibility with database wall-clock freshness after
all blocking locks. The transaction spans only the single ENTRY POST, not readback,
protection, recovery or closes. A later latch writer waits until the fence exits;
a latch committed while source locks wait wins and prevents the POST. No migration
or latch-writer convention is required. This is serialization, not cancellation
of an already-permitted POST; provider timeout and database lock availability
remain operational limits. Existing claim/lease and durable intent idempotency
remain unchanged.

## Clock evidence

The previous client sampled local time before awaiting the server GET, incorrectly
counting request latency/backoff as negative clock skew. The repaired estimate uses
the local request midpoint and carries half the elapsed interval plus millisecond
quantization as uncertainty. A request exceeding 1000 ms, negative elapsed time,
or wall-clock/monotonic divergence exceeding 10 ms is explicitly inconclusive.
Retry/backoff time is included, not hidden.

`ClockSkewEstimate` remains an `int` for legacy protocol compatibility and retains
`uncertainty_ms`. Monitor and venue preflight admit only if the entire uncertainty
interval is within their existing, unchanged thresholds. An interval entirely
outside the threshold is proved excess; an overlapping interval is inconclusive.
Monitor reports `clock-skew-inconclusive` separately from `clock-skew-exceeded` and
unavailable reads, while keeping all existing fail-closed latch semantics. Slow
synchronized samples do not masquerade as proved offset or authorize trading.

## Verification and rollback

Regression tests use fake provider transport and the disposable network-none
PostgreSQL container's Unix socket, database/user `fatty_test`, password `testonly`.
No application `.env` is sourced. Original candidate unit tests fail on production
source; final permission tests prove both lock ordering and latch serialization.
New partial-error, empty acknowledgment, economic contradiction and clock cases
also have explicit RED/GREEN evidence. Test logs/JUnit reports stay outside Git.

Independent review and integrated parent verification are required before release.
Rollback is a revert of this bounded commit; there is no new migration or data
rewrite. Nothing here constitutes deployed/runtime/account or live-order evidence.
