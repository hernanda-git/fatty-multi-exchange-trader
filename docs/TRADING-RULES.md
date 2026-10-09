# Trading rules and durable management safety

This document records the owner-selected behavior and the local implementation contract. It is not evidence of deployment, successful tests, provider-side order placement or a healthy live account. Historical audit documents describe the source at their audit point; use the integrated code and separately collected runtime evidence for current capability.

## Entry and automatic protection

- When an eligible recent source signal has just passed its entry in the trade direction, enter 25% at current market price and leave 75% as a working LIMIT entry at the signal entry. LONG has passed when market is above entry; SHORT when market is below entry. Production routing must use source receipt time and the configured recency window, not just a price-distance heuristic. `route_entry` has a 60-second default recent-source window; effective runtime configuration remains authoritative.
- Both legs need stable child client order IDs rooted in the canonical dispatch and persisted ownership, order type, price and quantity before submission. Working LIMIT/partially filled legs remain owned and reconcilable across restart. They are not an excuse to bypass pending-order admission globally.
- If venue quantity step, minimum size/notional or maximum quantity cannot support the exact 25%/75% split, fail closed with a clear reason. Do not silently convert the split into a full-market order or invent target allocations.
- Keep native SL and first-target automatic TP covering the full actual bot position, including later remainder fills. The selected automatic TP closes the FULL position at the first target; it is not a multi-target allocation scheme.
- Existing account-baseline exposure is not bot-owned entry exposure. Preserve its exclusion from bot admission, lifecycle accounting and protection ownership.

## Source booking and CLOSE

Source messages are management instructions, not new entry setups. An explicit, unambiguous symbol and nonconditional/nonnegated instruction are required. The analyzer durably queues management before marking a message analyzed; `(source_message_id, revision, symbol, action)` deduplicates the same instruction.

Before any recognized booking TP or CLOSE is executed, stop all bot-owned remaining entry legs for that symbol, including when there is no open position. Use only exact durable ENTRY ownership and client order IDs. Never call symbol-wide cancel-all and never cancel native SL/TP, reduce-only closes, plan protection or unrelated/manual orders to accomplish this.

`cancel_pending_entries(symbol, management_id) -> bool` is the boundary: true means every owned remaining entry leg is proved terminal by provider readback. The entry execution lifecycle durably claims each cancellation before its POST. A repeated claim, crash or ambiguous response uses GET-only reconciliation; no blind cancel POST retry. Missing adapter wiring or an unproved result leaves management `reconciliation-pending`, never successful.

After proved cancellation, read provider positions again. A fill can race cancellation, so do not size a close from a pre-cancel position snapshot. The source mutation gate applies before cancellation as well as close/SL mutations.

### Action distinctions

- `CLOSE`: after cancellation, close the freshly observed full position reduce-only and require flat readback.
- `TP1_BOOKED`: preserve the existing explicit source TP1 policy: after cancellation, close half of the freshly observed position, confirm reduction, then request SL at remaining entry. This source-management policy is distinct from FULL-position automatic first-target TP; do not generalize it to other targets. The gateway fails closed when native SL replacement lacks a verified adapter.
- `TP_BOOKED`: generic TP and later numbered TP booked/taken/hit messages cancel the remainder only. They do not authorize another allocation, a partial/full close or SL replacement. TP1-specific parsing takes precedence. Numbered TP labels are not trade symbols.
- `SL_TO_ENTRY`: retains its existing stop-management behavior; it does not itself authorize cancellation of waiting entry.

### Honest terminal outcomes

- `cancelled-flat`: cancellation was proved and a fresh position read found no open position, with no existing close POST intent for that management update. No close/TP execution is claimed or fabricated.
- `entries-cancelled`: a generic/later TP booking proved entry cancellation while an actual position remains; no close/TP fill is claimed.
- `reconciled`: the requested position mutation completed its existing provider readback contract.
- `reconciliation-pending`: cancellation or an existing/ambiguous mutation needs durable reconciliation. An earlier close intent plus a currently flat position alone must not be relabeled as cancellation-only success.

A source relay says management was detected/queued; it is not a provider execution confirmation. Duplicate messages and restart must not repost claimed cancels or previously intended closes.

## Evidence and verification responsibilities

Implementation boundaries: `analyzer/trade_management.py`, `execution/source_management.py`, `operator/bitget_gateway.py`, the dispatch entry execution adapter, live-intent storage and schema migration 25. Source-management storage reclaims pending updates for readback recovery, and orders by last update time so one unresolved item does not permanently starve later instructions.

Local regressions cover flat accounts with waiting entry, fills racing cancellation, unknown cancellation/restart, duplicate updates, mutation gates, generic/later booking semantics and truthful notification rendering. The entry lifecycle regressions must additionally prove exact-owned cancellation, protective-order survival and no cancellation POST retry after a persisted claim. The integration owner runs checks after all slices land. No verification or deployment claim is made by this document.

See [AGENTS.md](../AGENTS.md) for the agentic workflow and [PRODUCTION-LIVE-POLICY.md](PRODUCTION-LIVE-POLICY.md) for production admission/deployment constraints. Live enablement never clears safety gates or authorizes live mutations without the applicable approvals.
