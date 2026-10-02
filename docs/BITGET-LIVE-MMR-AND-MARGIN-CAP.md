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

## Why signals were still refused after the MMR fix

With MMR tiers loading again, signals stopped dying on `maintenance-margin tiers are
required` and started dying on `sl-guard: stop-loss not safely before liquidation`.
That guard is not a bug — it refuses a trade whose stop would sit at or beyond the
estimated liquidation price — but the pinned 20x ceiling left it no alternative:

- `RAREUSDT` (SHORT, entry 0.0225, SL 0.02389 = 6.18% away): the symbol reports
  `keepMarginRate = 0.025`, so at 20x the headroom is `5% − 2.5% − 0.06% ≈ 2.44%`.
  A 6.18% stop can never be inside 2.44% of room, at any margin size.
- `QNTUSDT` (SHORT, signal entry 152.5, SL 157.2 = 3.08%): at the signal entry the
  geometry fits, but the preflight prices the plan off the *live* market price. When
  it ran, the market was ~151.8 — 0.5% better for the short — which widened the stop
  to 3.55% while the headroom at 20x (MMR 1%) is `5% − 1% − 0.06% ≈ 3.94%`, and the
  guard's 10% span buffer demands `0.9 × 3.94% ≈ 3.55%`. It missed by ~0.01%.

Both cases are the same shape: a stop wider than `1/leverage − MMR − fee` allows.
The fix is the leverage floor described below, not a looser guard.

## Policy invariants (LIVE)

- Leverage **ceiling** is exactly `20x` and is never exceeded: `max_leverage` must be
  `20`, `_MAX_LIVE_LEVERAGE` is `20`, and `BITGET_MAX_LEVERAGE` must be `20`. A range
  such as `20/50` is a startup error.
- Leverage **floor** is configurable (`BITGET_MIN_LEVERAGE`, 5–20). The sizing policy
  walks candidates downward from the ceiling and takes the highest one that is both
  exchange-legal (step-rounded quantity at or above min-notional, inside the margin
  cap) and clears the liquidation guard. An ordinary signal therefore still trades at
  `20x`; a signal whose stop needs more room than `20x` leaves backs off only as far
  as its own stop geometry requires. Lower leverage is safer: the position simply
  sizes smaller inside the same 1 USDT margin cap. Set
  `BITGET_MIN_LEVERAGE=20` to restore the old "20x or skip" behaviour.
- Why the floor exists: the guard's headroom is `1/leverage − MMR − taker fee`, and
  per-symbol maintenance-margin rates differ a lot. At 20x, a symbol reporting
  `keepMarginRate = 0.004` (BTC) has ~4.5% of room while one reporting `0.025` (RARE,
  `maxLever = 20`) has only ~2.4%. Before the floor existed, signals whose stop sat
  wider than that were refused with `sl-guard: stop-loss not safely before
  liquidation` even though a lower leverage would have been perfectly safe.
  Margin size cannot fix this (the margin ratio is `1/leverage`), so the only cure is
  a lower leverage — which is why the search backs off instead of sizing up.
- `BITGET_LIQUIDATION_BUFFER` (default `0.10`) is now actually read by the LIVE
  preflight; previously it was silently ignored and the model default always applied.
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
- A persisted post-fill mismatch is a latchable anomaly for the monitor, so it blocks
  new entries on the LIVE lane and survives a restart (see the gates note below).
- Operator mutations (`operator-bot`, the Telegram command surface) remain closed
  (`BITGET_OPERATOR_MUTATIONS_ENABLED=0`). This gate is per-service: `source-management`
  is hard-coded `1` in Compose on purpose, so source TP1/SL/CLOSE copy-trade actions are
  applied automatically. Do not read `.env` alone and conclude mutations are shut.

Remaining gates, stated plainly: the per-worker in-process degraded gate still
exists, and the durable post-fill mismatch entry-admission latch is intentionally not
wired (removed at the owner's request), so a post-fill margin/leverage mismatch marks
the current worker degraded rather than blocking a replacement worker at entry POST.

Instead, a mismatch now blocks entries through the monitor's kill switch: once
`balance_reservations.record()` has persisted a `bitget_post_fill_reconciliations` row
with `status = 'mismatch'`, the monitor treats it as a latchable anomaly
(`post-fill-margin-or-leverage-mismatch`) and latches the Bitget kill switch on the
LIVE lane, which makes the dispatcher reject new dispatches with
`kill-switch-latched`. It survives restarts. Releasing the switch with an approval
reference is what marks the mismatch handled: the predicate only counts mismatches
newer than the kill switch's `updated_at`, so a released switch does not re-latch on
the historical row while a genuinely new mismatch latches again.

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
