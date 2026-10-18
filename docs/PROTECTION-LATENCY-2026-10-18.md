# Protection latency — measured, 2026-10-18

First read-only run of `scripts/protection_latency_report.py` against the production
database (revision `4d59465`, account flat, no venue calls, connection opened
`default_transaction_read_only=on`). It reads the last 40 dispatches: 7 reached an entry
intent, 33 never did (`no-entry`: rejected or expired before submission).

## Headline numbers

| metric | median (s) | p95 (s) | samples |
| --- | --- | --- | --- |
| signal receipt → dispatch | 13.744 | 38.007 | 5 |
| **entry fill → protection claim (pre-POST)** | **1.072** | 1.459 | 2 |
| entry fill → first recovery readback | 34.651 | 35.127 | 2 |
| **entry fill → close fill** | **2.361** | 2.823 | 2 |

Verdicts across the 40 dispatches: verified 0, missing-protection 7, settled-closed 0,
no-entry 33, other 0; entry-intent join coverage 7 linked / 33 unlinked.

## What this establishes

1. **The race window is ~1.3 seconds wide, and it is now covered by 15 seconds.**
   The protection claim (the durable step immediately before the native plan POST)
   happens a median **1.072 s** after the entry fill. Both live trades were then closed
   by containment **2.361 s** after the fill — so the venue had roughly **1.3 s** to make
   the accepted plans readable before the bot gave up and washed the trade. The grace
   budget is now 15 s: more than ten times the window that actually decided the outcome,
   and the first grace readback already lands after the moment containment used to fire.
2. **`entry fill → first recovery readback` is *not* the protection-confirmation
   latency.** It measures the dispatch recovery lane, whose cadence is 30 s
   (`BITGET_DISPATCH_POLL_SECONDS`); the ~35 s figure is that tick, not the venue. It is
   recorded here so the two numbers are never confused: the grace loop runs inside
   `protect_filled_position` and is not what these rows measure.
3. **Signal receipt → dispatch is ~14 s median, p95 38 s.** That is Telegram intake →
   canonical signal → dispatch creation, before any venue call. The NEAR trail shows the
   worst case: the message arrived 11:42:07, the dispatch was created 11:42:45 (38 s),
   the signal expired at 11:51:53 on source freshness, and only reached submission after
   the operator re-admission at 12:25.

## Per-trade evidence (the two wash cases)

```
NEARUSDT SELL dispatch ff94fe45 (FILLED, attempts=1,
          verdict recovery-missing-protection:owned-flat-close-fill-unproven)
  11:51:53  QUEUED → EXPIRED            source_freshness     stale-source-message
  12:25:50  EXPIRED → QUEUED            operator_re_admission  owner-approved-late-near
  12:53:57  QUEUED → PREFLIGHT          canonical_validation
  12:53:59  PREFLIGHT → SIZED           preflight
  12:53:59  SIZED → VALIDATED           execution_admission
  12:53:59  VALIDATED → SUBMITTING      provider_submission
  12:54:04  SUBMITTING → FILLED         provider_submission  missing-protection-escalated
  12:54:35  FILLED → FILLED (×8)        recovery_readback    owned-flat-close-fill-unproven
  entry fill 12:54:00.754 → close fill  ~+2.4 s

WLDUSDT BUY dispatch 3318e814 (PARTIALLY_FILLED, attempts=1, same verdict)
  09:16:14  QUEUED → PREFLIGHT          canonical_validation
  09:16:17  PREFLIGHT → SIZED           preflight
  09:16:17  SIZED → VALIDATED           execution_admission
  09:16:17  VALIDATED → SUBMITTING      provider_submission
  09:16:20  SUBMITTING → PARTIALLY_FILLED provider_submission missing-protection-escalated
  09:16:52  PARTIALLY_FILLED → PARTIALLY_FILLED (×286)  recovery_readback
  entry fill 09:16:18.191 → close fill  ~+2.4 s
```

Both are the same shape the grace now protects: entry fills, native plans are accepted at
the venue but not yet readable, containment fires ~2.4 s later.

## Transition churn (audit noise, measured)

| dispatch | symbol | transition rows | distinct sequences |
| --- | --- | --- | --- |
| f19dee96 | PUMPUSDT | 5285 | 3 |
| 5e34070a | PUMPUSDT | 5285 | 3 |
| b8ab618c | PUMPUSDT | 5284 | 2 |
| 7d4f4d8c | PUMPUSDT | 5284 | 2 |
| 3318e814 | WLDUSDT | 291 | 2 |

Recovery re-transitions the same unresolved lifecycle on every tick even when
`(to_state, reason)` is unchanged. That is 21 410 redundant rows and unbounded growth;
follow-up P0-1b is to skip a no-op transition and store a repeat counter instead.

## What the durable record cannot answer yet

- **protection POST → first provably-visible readback**: no `native_plan_posted_at` /
  `native_plan_visible_at` exists. The grace writes attempts only to the capability error
  trail (`|grace:N`) and a log line (`native-protection-verified-late`). A per-attempt row
  (symbol, root, attempt, observed_at, outcome) is the missing instrumentation, and it is
  the one number that would let the 15 s budget be tuned from data.
- `bitget_protection_capabilities.last_verified_at` is local `datetime.now(UTC)` at
  capability update, per `(exchange, environment, symbol)` — not per trade, and NULL for
  the failed cases above.
- `last_stream_at` is the provider event timestamp, overwritten rather than sampled, so
  staleness distribution cannot be reconstructed after the fact.
- close POST and flat-proof timestamps are absent; close latency is measured from
  `fills.filled_at`.

## Sampling caveat

The key metrics have n=2 (the two live trades of 2026-10-18). They are directionally
decisive — a 2.4 s containment window versus a 15 s budget — but not a distribution. The
first month of trades under the new grace, plus the per-attempt instrumentation above,
turn this into one.

## Reproducing

Both tools are read-only and run from inside a running app container (they need only
`psycopg` and the container's `PG*` environment; stdin avoids any copy or permission
issues):

```
docker compose exec -T dispatcher-bitget /app/.venv/bin/python - --limit 40 \
    < scripts/protection_latency_report.py

docker compose exec -T dispatcher-bitget /app/.venv/bin/python - \
    --client-oid live-bitget-NEARUSDT-0a6a0c8deae15e56 < scripts/dispatch_trail.py
```

`dispatch_trail.py` also accepts `--latest` (newest dispatch), `--dispatch <uuid>`, and
`--json` on both scripts. Exit code 2 means not found.
