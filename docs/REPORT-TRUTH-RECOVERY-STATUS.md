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

## Approved clean baseline: controlled operator procedure

The audited baseline is a different policy from verified-close recovery. It
preserves every historical entry, fill, fee, provider identifier and dispatch
reason as unresolved history; it **does not prove historical closure or PnL**.
Migration 24 creates append-only baseline receipts and immutable exact-record
snapshots. Only the recorded intent/dispatch/reservation UUIDs are excluded from
startup recovery and historical admission vetoes. There is no global timestamp
skip: a newly unresolved intent still blocks. Consumed/reserved/unknown financial
reservations are released with `audited-baseline:<receipt>:unresolved-history`;
their complete prior state is retained in the receipt, not relabelled verified.

The command is `scripts/audit_bitget_baseline.py`. It never places, cancels,
closes, modifies protection, releases latches, or changes LIVE/DEMO, risk limits
or mutation feature gates. Before applying, the deploying operator must:

1. Retain the database backup and deploy the reviewed code/migration.
2. Stop **all account mutation consumers** (dispatcher, protection/fallback
   workers, source-management, operator mutations, and any external manual/API
   trading). Database locks alone cannot stop another actor trading at Bitget.
   Keep them stopped through apply and readback. Active local positions,
   unexpired worker leases, and unresolved non-entry intents refuse baseline.
3. Independently identify the account UID; choose an explicit approval reference
   and a fixed timezone-aware historical cutoff. The cutoff selects records for
   review only, never runtime signal eligibility. Use the same values for both
   commands. Never advance the cutoff merely to silence a new error.
4. Run the dry-run using the deployed container's credentials and libpq `PG*`
   environment (or `DATABASE_URL`). Do not put keys or DSN secrets on the command
   line. For example, replacing the UID/cutoff/reference with approved values:

   ```sh
   docker compose run --rm --no-deps dispatcher-bitget \
     /app/.venv/bin/python scripts/audit_bitget_baseline.py --dry-run \
     --environment LIVE --account-id "$APPROVED_BITGET_UID" \
     --historical-before "$APPROVED_HISTORICAL_CUTOFF" \
     --approval-reference "$APPROVAL_REFERENCE"
   ```

5. Review the returned exact-record counts and `candidate_digest` against the
   retained ledger/audit evidence. Apply with the reviewed digest:

   ```sh
   docker compose run --rm --no-deps dispatcher-bitget \
     /app/.venv/bin/python scripts/audit_bitget_baseline.py --apply \
     --environment LIVE --account-id "$APPROVED_BITGET_UID" \
     --historical-before "$APPROVED_HISTORICAL_CUTOFF" \
     --approval-reference "$APPROVAL_REFERENCE" \
     --expected-digest "$REVIEWED_CANDIDATE_DIGEST"
   ```

Every invocation authenticates the account UID and checks server time, then
reads positions and ordinary pending orders plus `normal_plan`, `profit_loss`
and `track_plan` for **USDT-, USDC- and COIN-FUTURES**. Pending evidence must be
an explicit empty `entrustedList` with a terminal `endId`, or the exact paired
`{"entrustedList":null,"endId":null}` empty response observed on authenticated
2026-10-09 reads for every product and family. The latter is retained verbatim
in the private receipt, not rewritten into an invented list. CCXT's Bitget
[`fetch_open_orders`](https://github.com/ccxt/ccxt/blob/master/python/ccxt/bitget.py)
also decodes null order lists as empty; Bitget's documentation shows the populated
list, not an empty example. Missing keys, null with a non-null cursor, malformed
quantity, stale evidence, clock skew, API errors, or any positive position/order
refuse. This narrow wire-format decision does not prove old close ownership.

Apply locks and rechecks the database, verifies the dry-run digest, and repeats
the entire fresh provider GET inventory while the write locks are held. All
record snapshots and reservation releases commit atomically. Existing baseline
account/environment binding must match. Output includes only counts, approval
reference, digest and receipt ID, never credentials or raw account inventory.
Receipt evidence is private database data; do not publish it.

After apply, read back the receipt, exact snapshots, unchanged fill truth and
released reservation reasons before restarting consumers. A current fresh
provider inventory must still gate startup; an active/unknown current position
or order cannot be bypassed by historical exclusions. Runtime must also bind
the receipt to the current authenticated account and environment.

The existing source-freshness checks remain authoritative. Old queued FIL/WLD/
TRUMP signals must expire, not replay; only **future fresh signals** may receive
new admission after every ordinary gate/latch is genuinely ready. Baseline is
not a readiness guarantee and does not itself authorize clearing either latch.

There is no destructive rollback of a committed financial baseline. Restoring
source alone does not undo its receipt/released commitments; any further ledger
change needs a separate reviewed account-safe audit policy. PostgreSQL regression
cases are in `tests/e2e/test_audited_bitget_baseline_db.py`; the integrating owner
must run them with `FATTY_TEST_POSTGRES_DSN` after all changes land.
