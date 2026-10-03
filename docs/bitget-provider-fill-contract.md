# Classic V2 fill readback and restart ownership

This change is offline-only. It does not authorize provider mutation, deployment,
entry replay, historical capacity release, or kill-switch clearing.

## Provider contract and evidence

Official reference:
https://www.bitget.com/docs/catalog/classic-contract-trade/classic-contract-trade
(sections Get Order Detail, Get Order Fill Details, Get Historical Transaction Details).
The old `/api-doc/contract/trade/...` links now redirect to the unrelated UTA intro;
do not use that redirect as a Classic V2 contract.

- GET `/api/v2/mix/order/fills`: `limit` maximum 100; `idLessThan` requests older
  **trade IDs**. `endId` is the final transaction ID, not a boolean has-more flag.
  The documented query horizon is three months.
- GET `/api/v2/mix/order/fill-history`: maximum query window one week, maximum
  page size 100, `idLessThan` uses the preceding response's `endId`. History is not
  supported in DEMO. A recent-fills scan is not a historical full-account audit.
- Order detail uses `state` (`filled`, `partially_filled`, `live`, `canceled`),
  `baseVolume` and `priceAvg`; legacy `status` remains supported.
- One-way fills use `buy_single` / `sell_single` and `posMode=one_way_mode`.
  These mean **buy / sell**, not intrinsically open / close.

`tests/fixtures/bitget_one_way_fills.json` preserves one authentic GET-only WLDUSDT
page from `artifacts/provider_history.json` in the integration audit worktree.
Its two rows have a nonempty final trade ID. This fixture is a page, NOT evidence
of historical exhaustion. All HTTP terminal pages in tests are explicitly offline
fixtures, not claimed live responses.

The existing history probe stopped on short pages and discarded null pages as
ValueError. Neither that error nor that short-page rule establishes completion.
Authenticated history success with `data: null` has been observed, but the official
reference fetched for this repair documents `data` as an object and does not
specify null exhaustion. `_get` continues to return the raw null. No generic
null-to-empty normalization was added. A null recent-fills page remains UNKNOWN;
a separately reviewed, endpoint-specific historical empty-page contract is still
required before a historical audit can claim exhaustion from null.

## Bounded authenticated pagination

`BitgetRestClient.get_fills` signs each GET, freezes `endTime`, sends `limit=100`,
and follows `idLessThan=endId`, including after a short nonempty page. The default
bound is 20 pages (caller bounds 1..100). Successful exhaustion requires an explicit
`fillList: [], endId: ""` object. The returned aggregate clears its cursor only
then. It retains raw pages, including nulls, in `pages`; bounded/failed scans keep
a nonempty cursor so all execution/recovery readers remain UNKNOWN.

Malformed shape, missing/null cursor metadata, duplicate or non-decreasing trade
IDs, cursor mismatch, provider error, and page-bound exhaustion cannot establish a
complete ledger. Partial valid rows are preserved, not synthesized. Economic row
validation also prevents malformed quantities, prices or supplied fee fields from
making the remaining rows appear complete. Valid nested `feeDetail[].totalFee`
fees are normalized through the existing canonical helper.

## Accounting and epoch fences

Empty detail and 40109 use the same GET-only missing-detail classifier. An empty
later read cannot erase durable quantity, average price, fees, IDs or real trade
rows. Nonempty detail and the legacy unknown-intent reader also retain the durable
floor, consume cursor completeness, and preserve previous trades even when a new
page has the same aggregate quantity. A changed quantity/price/fee for the same
trade ID is contradictory evidence: retain the confirmed trade and return UNKNOWN.
No missing trade is invented, and no ambiguous entry is replayed by POST.

One-way direction is accepted for epoch proof only inside an independently owned
ENTRY with exact order/client identity, symbol, side, complete quantity and trade
IDs. Wrong mode/direction, explicit reduce-only, system-source or nonzero/malformed
PnL rows cannot supply this opening-compatible proof. Restart recovery still
requires position `cTime` equal to the earliest owned opening fill and every native
plan created at or after that epoch. Inventory includes the epoch; a replacement
position with the same symbol/side/size cannot inherit old protection. Fallback
identity additionally refuses unconsumed fill cursors. Immediate native placement
readback stays separate from restart ownership proof.

## Verification and rollout limits

Regression tests exercise the real signed client using `httpx.MockTransport`, plus
real PostgreSQL service/recovery persistence in a dedicated `fatty_test` Unix-socket
container with `--network none`. They never access provider credentials. The service
fixture now contains canonical fill/position/plan timestamps and real one-way shape;
replacement-position, old-plan and missing-fill-epoch cases remain blocked.

Deploy only after the integrated branch receives independent review and the owner
approves the closed rollout. Historical liquidation/close attribution, live
provider empty-page semantics, and operational admission release are separate
acceptance tracks. Roll back this code by reverting its commit; no migration is
included, and this repair changes no live database or provider state.
