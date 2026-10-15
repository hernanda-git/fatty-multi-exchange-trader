# MAGIC signal audit and configuration-console deployment — 2026-10-15

## Verdict

The current MAGIC signal is Telegram message **16310**, not the older 16299 signal.
It was parsed into a canonical LONG signal, claimed once by the Bitget dispatcher,
and rejected during **PREFLIGHT** with:

```text
Bitget contracts response is invalid
```

There are **zero linked margin reservations, zero live order intents, and zero
linked fills**. This was a dispatcher processing attempt, not an entry-order POST
attempt. The missing entry was not recorded as a kill-switch, balance, or
price-distance rejection.

The production contract validation rejects the entire USDT futures catalog if any
contract symbol does not match `[A-Za-z0-9]{2,20}`. A read-only reproduction in the
actual dispatcher container against the current Bitget catalog produced the exact
same error. Three unrelated Unicode contract symbols triggered the check before
the requested `MAGICUSDT` record could be selected.

The configuration-console release **19c4ced is deployed**. It does not change this
separate trading adapter defect. No signal was retried, no order was submitted,
and no trading settings or latches were changed during this audit.

## 1. Evidence scope

- Read-only production PostgreSQL queries at **13:51:34–13:52:28 UTC**, covering
  source revisions, canonical signal, dispatch, transitions, notifications,
  linked reservations/intents/fills, and current latch rows.
- A bounded dispatcher log window: **12:34:30–12:35:30 UTC**.
- Read-only reproduction using the deployed dispatcher package and the public
  exchange contract catalog. The audit client implements only `get_contracts`;
  account and mutation method access raises an assertion.
- Public Telegram source independently checked at
  <https://t.me/fattyfatclub/16310?embed=1&mode=tme>.
- Public Bitget contract and ticker GET responses. Final ticker timestamp:
  **2026-10-15T13:55:58.919Z**.
- No database mutation, synthetic signal, provider order, financial retry,
  configuration save/apply, or latch release was used as verification.

The public channel summary initially exposed an older MAGIC signal. Fresh
production rows and the direct 16310 widget supersede that older result. Message
16299's historical kill-switch rejection is **not** the cause of today's miss.

## 2. Exact signal and identifiers

Source: **UNKNOWN TRADERS ACADEMY / @fattyfatclub**, channel ID
`-1001252615519`, message **16310**.

<https://t.me/fattyfatclub/16310>

```text
#MAGIC $MAGIC LONG TRADE

ENTRY: 0.109

TARGET: 0.182

STOPLOSS: 0.10525
```

- Source timestamp in `telegram_messages.received_at`: `2026-10-15T12:34:31Z`.
- Ingestion origin: `realtime`.
- Analyzed source row: `2b77b1d3-b811-4502-8009-af16d3e329ff`.
- Source revision: `8272adc3f6913a6f554e70461219d7706de6fe0055d4f4c640f104affc9cf0b6`.
- Canonical signal: `f60196d1-5dfd-561f-9f87-991b8f0c190d`.
- Bitget dispatch: `614428b1-d638-4dd2-9062-161e1905f248`.
- Direction/symbol: `LONG`, normalized to `MAGICUSDT`.
- Final dispatch state: `REJECTED`.
- Worker: `dispatcher-bitget`; attempts: **1**.

The canonical row agrees with the source: entry `0.109`, stop `0.10525`, target
array `["0.182"]`. The analysis outbox reports `FALLBACK_ACCEPTED`; the signal was
not lost in parsing or fanout.

## 3. UTC processing timeline

| Time on 2026-10-15 (UTC) | Durable observation |
|---|---|
| 12:34:31 | Source message timestamp |
| 12:34:38.814869 | Canonical signal, dispatch, and analysis notification created |
| 12:34:42.168783 | Dispatch transitioned `QUEUED → PREFLIGHT` |
| 12:34:42.723506 | Dispatch transitioned `PREFLIGHT → REJECTED`, reason `Bitget contracts response is invalid` |
| 12:34:44.714045 | Analysis notification marked sent, attempt 1 |
| 12:34:44.991233 | Preflight-transition notification marked sent |
| 12:34:45.288161 | Rejection-transition notification marked sent |
| 12:34:45.595684 | Dedicated rejection alert marked sent |
| 12:39:31 | Source entry freshness deadline |

The rejection occurred **11.723506 seconds after the source timestamp**, before
the five-minute expiry. That is a source-to-rejection interval, not a measurement
of wire ingestion latency.

Transition IDs:

- `QUEUED → PREFLIGHT`: `b0f3bf85-974a-4cb7-8cc1-f084c1641719`.
- `PREFLIGHT → REJECTED`: `2cda975e-60bc-4a42-8210-a731cf1622e1`.

The bounded dispatcher log corroborates `state=rejected`, followed by `state=idle`.
It does not include the full exchange response.

## 4. Confirmed validation defect

Code path:

1. `BitgetDispatcher.run_once()` in
   `src/fatty_trader/execution/bitget_dispatcher.py:116-184` claims the dispatch,
   passes the earlier entry gates, and records `PREFLIGHT`.
2. `BitgetRestClient.get_contracts()` in
   `src/fatty_trader/exchanges/bitget/client.py:351-352` requests the **whole**
   `USDT-FUTURES` catalog, without a symbol filter.
3. `AsyncBitgetVenue.preflight()` in
   `src/fatty_trader/exchanges/bitget/async_venue.py:137-151` applies an ASCII-only
   regex to **every** catalog row, before `find_contract(contracts, symbol)`.
4. A nonmatching row raises `BitgetApiError("Bitget contracts response is invalid")`.
5. The dispatcher records the exception as the terminal preflight rejection.

Current exchange response used for reproduction:

- Endpoint: <https://api.bitget.com/api/v2/mix/market/contracts?productType=USDT-FUTURES>.
- Exchange response: `code=00000`, data is a list, **834 contracts**.
- Rows rejected by the regex: `龙虾USDT`, `牛来USDT`, `哈基米USDT`.
- The requested `MAGICUSDT` row exists and has `symbolStatus=normal`.
- MAGIC metadata includes minimum notional `5 USDT`, quantity increment `0.1`,
  five price decimals, and venue leverage range `1–25`.

Actual dispatcher-container reproduction output:

```json
{"reproduced_error":"Bitget contracts response is invalid","contracts_count":834,"invalid_symbols":["龙虾USDT","牛来USDT","哈基米USDT"],"account_or_order_methods_called":false}
```

**Historical evidence limit:** the complete catalog response from the original
12:34:42 attempt was not available in the durable record. The original rejection
reason and matching deployed check are proven; the same failure is reproduced
against the current live catalog. The exact offending rows at the original instant
cannot be independently established from the saved historical payload.

**Impact:** this check is not specific to MAGIC. The same catalog validation can
reject other otherwise-valid symbols before their own metadata is examined.

## 5. Financial-effect boundary and ruled-out explanations

Production counts linked to dispatch `614428b1-d638-4dd2-9062-161e1905f248`:

- Margin reservations: **0**.
- Live order intents: **0**.
- Fills joined through those intents: **0**.

The error is raised immediately after the catalog GET, before account preparation,
position checks, sizing, margin reservation, live-intent creation, and entry order
submission. No account/order methods were reached in the reproduction. No provider
order ID exists in a linked intent because no linked intent exists.

These counts and ordering establish that the tracked dispatch did not reach the
bot's provider-entry path. They are not an account-wide provider-history audit and
do not make claims about unrelated manual MAGIC trading.

- **Not today's kill-switch failure:** this dispatch reached `PREFLIGHT`. The
  earlier venue and protection-stream checks did not reject it. At 13:51:34 UTC,
  current `bitget` and `bitget-protection-stream` latch rows were both inactive.
- **Not an original freshness timeout:** rejection was approximately 11.7 seconds
  after the source timestamp, with expiry still in the future.
- **Not an analyzer failure:** canonical signal, fanout, and sent analysis outbox
  exist. `FALLBACK_ACCEPTED` did not mean an entry order had been placed.
- **Not a recorded insufficient-balance refusal:** the catalog check failed first.
  This audit does not claim that all later risk/account gates would have passed.
- **Not a recorded price-distance refusal:** market routing was not reached.
  The market price at the historical rejection instant was not captured here.
- **Not proof MAGIC is unlisted:** its current contract row is present and normal.

## 6. Current price versus signal entry

Venue/product: **Bitget MAGICUSDT, USDT-margined perpetual futures**.
Quote timestamp: **2026-10-15T13:55:58.919Z**, not a continuously live price.

Source: <https://api.bitget.com/api/v2/mix/market/ticker?symbol=MAGICUSDT&productType=USDT-FUTURES>.

| Measure | USDT price |
|---|---:|
| Original signal entry | 0.10900 |
| Last traded price | 0.11081 |
| Mark price | 0.11110 |
| Best bid | 0.11104 |
| Best ask | 0.11107 |
| Source stop-loss | 0.10525 |
| Source target | 0.18200 |

Last traded price minus entry is **+0.00181 USDT / +1.66055%**. Last trade and
bid/ask need not be identical: the former is the most recent executed trade, while
the latter describe the current quote. This is a price comparison, not bot P&L,
a fill guarantee, or a recommendation to enter late. The source author identifies
Blofin as the basis for its levels in channel messages; this quote is Bitget.

## 7. Current replay eligibility

The source entry expired at **12:39:31 UTC**. Both currently persisted revisions
of message 16310 also carry `entry_rejection_reason=edited-source-message`:

- Analyzed revision: `2b77b1d3-b811-4502-8009-af16d3e329ff`.
- Other revision: `ccb5c477-6bab-4dfb-b6d6-96e14a967b91`, state `EXPIRED`, revision
  `d57ed60b9b24d4d6ad164abc6ea185ab2198407dc702e6cd8b442a58c0622aef`.

`src/fatty_trader/intake/freshness.py:77-93` requires no rejection reason, no
message-level edit veto, an unexpired entry deadline, and a source within five
minutes. `intake/persistence.py:89-115` propagates the edit veto across revisions.
The dispatch is already terminally rejected as well.

The edit occurrence time is not stored in the selected source columns, so this
audit does not invent its timestamp or substitute it for the recorded original
preflight failure. At audit time, stale age and the edit veto independently prevent
ordinary replay. Nothing was requeued or manually triggered.

## 8. Configuration-console deployment receipt

- Code release: **19c4ced**, committed and pushed to `main`.
- Public URL: <https://trade.warga-digital.com/config>.
- New JS bundle: `/assets/index-Cr8CyYIZ.js`, fetched from the public application.
- Web image revision label: `19c4ced`.
- Image digest: `sha256:d61c1e6485f65b115320a27dfced8eb987598fdf7180fb1862f435d274a0fee6`.
- New web container: `ed3249d8b34b`, running and healthy.
- Host operations package reinstalled from the release source; its service is
  active and `config.save` is present in `HOST_ACTIONS`.
- Dispatcher `c6568e307f3c`, monitor `7561b69e912e`, console worker `75c4f9fcc308`,
  and all other non-web container IDs were preserved.
- All ten listed application/database containers were running and healthy after
  activation. `/health` reported `status=ok`, readiness `ready`.
- Public browser rendered the sign-in form and fetched the new bundle containing
  the direct-save settings form. Authenticated production save/apply was not
  submitted; no plaintext owner password was available to this session.
- Prior verification: frontend build passed; 168 relevant backend tests passed;
  isolated UI save/recovery scenarios and adapter/executor smoke passed.

The initial image build succeeded, but activation paused because `uv` was not on
the host PATH and SSH temporarily rejected new connections. Access subsequently
recovered. The existing application's trusted `/bin/uv` binary was copied into the
private host runtime and used to reinstall the host worker package. Only web and
the host operations worker were restarted; no trading consumers were restarted.

## 9. Remediation boundary at the initial audit

At the initial audit checkpoint, the console deployment was complete and the
separate catalog-validation defect had not yet been changed. The following
recommendation describes that checkpoint; subsequent remediation is recorded below.

Recommended code change: validate the catalog's structure without requiring every
unrelated exchange symbol to be ASCII; locate the requested symbol exactly and
strictly validate that target contract's execution metadata. Retain fail-closed
handling for malformed catalog data and invalid target metadata. Add a regression
covering a valid MAGIC contract alongside legitimate Unicode contracts.

A correction must not bypass account/protection/risk gates, replay expired/edited
messages, or auto-submit this rejected signal. Clearing latches or altering margin
and leverage would not repair the demonstrated parser defect.

## 10. Owner-requested remediation verification

The contract lookup now accepts legitimate unrelated Unicode symbols while
rejecting malformed catalog identities, ambiguous targets and invalid selected
metadata. A read-only smoke using the live 834-contract catalog reached the
account-read boundary for `MAGICUSDT` and recorded `target_validated`, without
account mutations or order submission.

Migration 27 adds durable transition evidence and new ingestion/edit-veto timestamps
without inventing historical observations. Transitions and their notification
outbox evidence commit atomically; structured JSON logs follow commit. Unknown
submission outcomes retain the entry-submission uncertainty and canary reservation.
Signal/dispatch detail pages expose exact recorded timestamps and expandable
diagnostics, analysis notifications, linked intents, reservations and fills.

Verification before deployment:

- Frontend build and affected-code Ruff checks passed.
- 257 focused unit tests passed; 53 dispatch/repository/manual-console regressions
  passed again after final correlation and ambiguous-submission changes.
- 60 real-PostgreSQL migration, edit-veto, freshness and recovery tests passed
  in disposable isolated schemas.
- An actual intake → analyzer → dispatcher → PostgreSQL smoke recorded correlated
  JSON transitions and catalog evidence, reached the deliberate account-read
  boundary, wrote no order intents, and removed its isolated schema afterward.
- Browser exercised new and legacy transition rendering, expanded diagnostics,
  and a 390px timeline fitting its container.

The broad Windows unit run was not green: 1,991 passed, 107 failed, 2 skipped.
It exposed Linux-only `/proc`/process/permission/Docker assumptions, a websocket
timing assertion, an obsolete Compose-source assertion, and a new ambiguous-path
test-double failure. The ambiguous-path canary release was corrected and focused
checks passed; obsolete source-text assertions were removed rather than repinned.
No claim that the complete cross-platform suite passes is made.

Production activation and release identity are recorded in
`BOT-CONSOLE-UX-PROGRESS.md`. The historical MAGIC dispatch is not replayed by
this remediation; source freshness, edit vetoes and all trading risk gates remain.
