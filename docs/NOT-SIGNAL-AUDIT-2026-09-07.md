# NOT Signal Execution Audit — 2026-09-07

## Scope

Exact source message:

```text
#NOT $NOT LONG TRADE

ENTRY: 0.0004715

TARGET: 0.00058

STOPLOSS: 0.000458
```

This report separates Telegram intake, semantic parsing, dispatch routing, venue
availability, and execution policy. It contains no credentials.

## Verified production trace

- Telegram message ID: `16100`
- Raw intake state: `ANALYZED`
- Canonical signal:
  - pair token: `NOT`
  - direction: `LONG`
  - entry: `0.0004715`
  - stop: `0.000458`
  - targets: `[0.00058]`
- The analysis notification reached the durable outbox and was marked sent.
- Bitget dispatch was rejected by the previous DEMO kill-switch policy.
- Binance dispatch remained `QUEUED` because the Binance dispatcher service is
  disabled by its Compose profile.
- No provider order intent was created for this signal.

## Root causes

1. The source and deterministic parser were correct. The message was not lost or
   misparsed.
2. The analyzer hard-coded fan-out to Binance and Bitget even when only the
   Bitget engine was deployed. This created false queued work.
3. DEMO dispatch and monitoring still used a persistent kill switch, contrary to
   the operator's execution-test policy.
4. DEMO was restricted to one canary symbol (`BTCUSDT`), preventing other
   provider-listed source signals from reaching preflight.
5. The authenticated Bitget DEMO contract catalogue currently does not contain
   `NOTUSDT`. This is a provider limitation, not a parser defect. The engine must
   not fabricate an unsupported contract.

## Fixes

- Analyzer fan-out is configurable and this deployment explicitly uses
  `DISPATCH_EXCHANGES=bitget`.
- DEMO dispatcher does not consult the persistent kill switch.
- DEMO monitor continues to detect/report degraded provider state but does not
  latch a kill switch.
- LIVE mode retains fail-closed kill-switch enforcement.
- DEMO may execute any provider-listed symbol under the global bounded order cap;
  it is no longer restricted to BTC.
- LIVE still requires an explicit canary symbol.
- Added an exact parser regression for message `16100`.
- Existing disabled Binance queues were moved from `QUEUED` to audited
  `REJECTED / engine-disabled` states.

## Runtime recovery and safety

- Pre-change Postgres backup:
  `backups/fatty_trader_20260907T154349Z.dump` (`39,368` bytes).
- Authenticated read-only Bitget DEMO probe passed for server time, account,
  contracts, positions, open orders, and fills.
- The previous DEMO kill switch was released after clean reconciliation.
- DEMO execution was authorized by the operator and remains bounded by the
  configured total-order cap.
- Venue preflight, instrument metadata, sizing, provider read-back, native
  protection, reconciliation, and emergency containment remain active.

## Honest tradability result

The signal format is supported and future messages of this shape are executable
when the selected DEMO venue lists the normalized contract. This exact `NOTUSDT`
contract is not listed in the authenticated Bitget DEMO catalogue, so it cannot
be legitimately submitted there. Making it execute would require either:

- a DEMO venue that actually lists `NOTUSDT`, or
- a separately authorized LIVE route.

Neither condition may be simulated or inferred. Unsupported symbols must fail at
venue preflight with a precise reason and zero provider mutation.

## Verification

- Exact NOT parser regression: passed.
- Active-engine fan-out RED/GREEN and mutation test: passed.
- DEMO no-kill-switch dispatcher/monitor tests: passed.
- Dynamic DEMO symbol under global cap test: passed.
- Full suite before deployment: `295 passed`.
- Ruff, compileall, and `git diff --check`: passed.
