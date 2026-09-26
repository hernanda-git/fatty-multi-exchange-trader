# Bitget LIVE — missed signals: MMR tiers, venue leverage cap, margin cap

Status: remediation implemented and deployed to the LIVE Compose stack.
Scope: `dispatcher-bitget`, `monitor-bitget`, and the shared image used by every
service in this repo. Migration `17` is the only schema change.

## Incident

Every Bitget LIVE signal was silently skipped while the bot reported healthy.
`plan_live_position()` raised `maintenance-margin tiers are required`, so no live
entry could ever be sized. The per-trade margin cap was **not** the cause; it was a
separate blocker found while fixing this one.

## Root cause 1 — MMR tiers were never loaded

`metadata_from_contract()` built `SymbolMetadata` from `/api/v2/mix/market/contracts`
and never populated `mm_tiers`. That endpoint does **not** publish maintenance-margin
rates, so the liquidation guard always failed closed.

Fix: `mm_tiers_from_position_lever()` in `src/fatty_trader/exchanges/bitget/metadata.py`
reads `/api/v2/mix/market/query-position-lever` (a new
`BitgetRestClient.get_position_lever()`), where each row carries

- `startUnit` / `endUnit` — the tier's size range, and
- `keepMarginRate` — the maintenance-margin **rate** (not a complement).

Bitget marks the unbounded final tier with `endUnit == 0`; that tier becomes the
catch-all `upper_bound_notional = None` so `select_mmr()` can never fall through a
gap. Missing symbol, unparseable rate, or a rate outside `(0, 1]` fails closed.
`AsyncBitgetVenue.symbol_metadata()` treats an empty position-lever payload as an
error rather than sizing against an empty tier tuple.

## Root cause 2 — venue leverage ceiling

Bitget now advertises `maxLever` up to `150`, while `SymbolMetadata.max_leverage`
was capped at `125`, which rejected the contract outright. The cap is widened to
`150`. This is venue metadata, **not** trading leverage: executable leverage is
pinned separately (below).

## Policy invariants (LIVE)

- Leverage is exactly `20x`. `min_leverage`/`max_leverage` must both be `20`
  (`BitgetLiveRiskConfig`, `_MIN_LIVE_LEVERAGE`/`_MAX_LIVE_LEVERAGE` in
  `risk/live_policy.py`, and the legacy `BitgetLiveConfig` in `config/bitget.py`)
  and `BITGET_MIN_LEVERAGE`/`BITGET_MAX_LEVERAGE` must be exactly `20`; a range such
  as `20/50` is a startup error. The second config class previously defaulted to
  `20/50`, so a caller that omitted the fields would still have traded at 50x.
- Per-trade isolated margin is capped at exactly `1 USDT`.
  `BITGET_MAX_MARGIN_PER_TRADE_USDT` is mandatory and fail-closed: missing, blank,
  non-numeric, `NaN`, `Infinity`, zero, negative, or anything other than exactly `1`
  is rejected at startup. The cap applies to the allocation request, to the
  all-in/fallback branch, and again to the exchange-step-rounded quantity, and the
  legacy no-reservation seam is capped too.
  `PostgresBitgetMarginReservationRepository.reserve()` takes the cap as a
  **required** argument and re-checks the planned margin before writing a
  reservation, so a caller cannot silently reserve uncapped.
  Caveat: this bounds the *planned* margin. Entries are market orders, so fill
  slippage can leave exchange-side realized margin (`filled_notional / 20`)
  marginally above 1 USDT. The risk clauses below are stated that precisely.

## Kill switch

An earlier change pinned the Bitget kill switch to alert-only, including a database
constraint (`bitget_kill_switch_alert_only`: `CHECK (scope <> 'bitget' OR active = FALSE)`).
With enforcement restored, that constraint is actively harmful: `latch_kill_switch()`
writes `active = TRUE`, so the constraint makes an anomaly raise `CheckViolation` and
kill the LIVE monitor instead of blocking new entries.

- Migration `17` drops the constraint. Migration `16` is **removed from `MIGRATIONS`**:
  it is already recorded on the deployed database (so only `17` runs there), and on a
  database that still held an `active = TRUE` bitget row its `ADD CONSTRAINT` would
  have aborted the single migrate transaction, which would have left `17` unapplied.
  Fresh installs must never create the constraint at all.
- `bitget_kill_switch_enforced()` enforces on the LIVE lane only, and
  `BITGET_FALLBACK_MUTATIONS_ENABLED` remains gated behind that enforcement.
- The dispatcher's kill switch is wired in **every** mode, not only LIVE/LIVE: a
  POST-capable graph exists whenever `BITGET_EXECUTION_ENABLED=1`, so a DEMO lane
  must not ignore the operator's stop. The latch itself stays LIVE-only.
- Operator mutations remain closed (`BITGET_OPERATOR_MUTATIONS_ENABLED=0`).

Remaining gates, stated plainly: the per-worker in-process degraded gate still
exists, and the durable post-fill mismatch entry-admission latch is intentionally not
wired (removed at the owner's request), so a post-fill margin/leverage mismatch
records a `bitget_post_fill_reconciliations` row and marks the worker degraded for the
lifetime of that process — it does not block a replacement worker. The
monitor/reconciliation kill switch is the mechanism that blocks new entries after an
anomaly, and it does not currently read post-fill mismatches.

## Rollback

Revert the commit and recreate the containers from the previous revision. Migration
`17` is additive-safe (it only drops a constraint) and `16` is already recorded. Do
not reintroduce the alert-only constraint without also reverting
`bitget_kill_switch_enforced()` to `False`, or the LIVE monitor will raise
`CheckViolation` on the next anomaly.

## Rollout notes

- The rollout left every provider mutation gate at its pre-existing value; no gate was
  opened or closed as part of this change.
- `docker compose restart` does not reload `.env` or copied source. Recreate with
  `docker compose up -d --force-recreate <service>` or `docker compose up -d` after a
  rebuild.
- Verify the running image actually contains the fix rather than trusting the commit:
  `docker compose exec -T dispatcher-bitget grep -c query-position-lever /app/src/fatty_trader/exchanges/bitget/client.py`
