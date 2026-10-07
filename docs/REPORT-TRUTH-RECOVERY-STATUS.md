# Report correction and blocked execution recovery

## Verified production state

The report-only revision is `00e29cbd3ac9d8d1a93e1843e39856f8ca561013`.
The host scheduled report uses this source. `notification-sender` and
`operator-bot` were recreated from an image labelled with this exact revision;
their changed module SHA-256 hashes match the reviewed source. Both containers
are running/healthy. Dispatcher, monitor, source-management, intake, analyzer,
web and PostgreSQL container IDs were preserved.

Provider GET readback: account equity/available `9.17026367 USDT`, unrealized PnL
zero, no positions, and no ordinary pending orders. These are current account
reads, not proof of historical close ownership. All three pending plan-family
reads (`profit_loss`, `normal_plan`, `track_plan`) returned successful objects
with `entrustedList:null`; the conservative diagnostic retained that shape as
unverified rather than inventing a numeric pending-plan count.

Production remains LIVE/LIVE/execution=1. Operator manual, fallback, and stream
mutation gates remain zero. Source-management's separately configured mutation
permission was not changed. Both existing entry latches remain active:
`bitget:clock-skew-exceeded` and
`bitget-protection-stream:socket-not-connected`.

## What was repaired

- Signal analysis cards label local dispatch creation as **antrean eksekusi**,
  explicitly not a provider order/fill confirmation.
- Historical filled recovery with an unresolved protection/close reason is
  **Recovery Diblokir**, not a new successful trade.
- Aggregate health includes global, venue, and stream latches.
- Missing dispatcher/lifecycle readiness is degraded, not a permissive default.
- A gate refusal does not invent DEMO mode.

The canonical host loaders rendered actual production evidence as **DEGRADED**,
**dispatcher RESTARTING**, **lifecycle BLOCKED**, with both latch reasons.
The actual stored TRUMP analysis payload was read from PostgreSQL and rendered
through the deployed notification-sender module as queue-only, without order/fill
confirmation. These verification renders did not send or replay Telegram messages.
The pre-existing TRUMP Telegram message was not edited or claimed corrected.

## Why execution is still blocked

The stored TRUMP payload was `signal-analysis/FALLBACK_ACCEPTED`, one canonical
signal and one queued dispatch. The durable TRUMP dispatch had zero attempts,
no transitions and no entry intent/fill. Its entry levels were 2.008/2.042/1.74
(short/stop/TP). Queue creation is not submission.

Startup invokes `recover_entry_lifecycles()` before entering the dispatcher loop.
Recovery correctly preserves unresolved ownership. Readiness is blocked by:

- 17 unmatched or NULL-environment historical filled entry intents;
- four consumed legacy reservations, total planned margin
  `5.4948119636363636363636363636 USDT`;
- four historical dispatch protection reasons
  `recovery-missing-protection:owned-flat-close-fill-unproven`;
- the two active latches above.

FIL long, WLD long and TRUMP short remain queued. They must not be replayed as
fresh trades. A current flat account does not clear historical commitments or
prove the original close-fill ownership chain.

## Provider history investigation

Authenticated Classic provider history was collected read-only for 12 symbols:
23 historical position rows and 48 fill rows, complete fill pages for all 12.
All 17 entry orders/fills were found and each had a unique economic/time-matching
historical position candidate. This is useful evidence, not direct ownership
proof: zero order/fill rows exposed a direct position-ID binding. Actual
historical position `ctime` differs from the opening-fill time by 6–39 ms;
never relabel the earliest fill time as the actual provider position epoch.

Raw responses, page boundaries, analyses and manifest are retained in the
ignored `artifacts/history-proof-discovery/` directory (0700), files 0600. Do
not commit these private account records. The existing dirty resolver in another
worktree was neither applied nor adopted.

No existing reviewed LIVE recovery command can safely retire the old ledger
under the current ownership contract. Restoring entry admission requires an
explicitly reviewed recovery/baseline policy, not a raw SQL clear or restart.
Such a baseline must preserve historical uncertainty rather than claim direct
provider ownership was proven. A separate owner decision is required before
changing this recovery contract or releasing latches.

## Verification and rollback

- Local final full suite with disposable Unix-socket PostgreSQL:
  **1702 passed, zero failed/skipped**. Disposable database removed after testing.
- Ruff check/format, mypy (103 source files), compileall and diff checks passed.
- GitHub CI on the exact source revision passed both Python 3.11 and 3.12,
  including full PostgreSQL tests and a rejection gate for skipped acceptance.
  https://github.com/hernanda-git/fatty-multi-exchange-trader/actions/runs/37566765246
- Independent verdict: **APPROVE for report-only deployment**;
  **ENTRY remains NOT READY**.
- Verified PostgreSQL custom-format backup:
  `backups/fatty_trader_20261007T032732Z.dump`, 869776 bytes; archive listing passed.
- No provider placement, cancel, close, protection mutation, ledger repair,
  latch release, or stale signal replay was performed.

Rollback report-only source by reverting its commits, rebuild the two report
consumers and recreate only those services. Preserve LIVE/LIVE/1, existing
latches, other services and all historical evidence. A rollback must not rewrite
financial state. The user-visible next bot report still needs delivery/readback
verification; a successful local render is not proof of Telegram delivery.
