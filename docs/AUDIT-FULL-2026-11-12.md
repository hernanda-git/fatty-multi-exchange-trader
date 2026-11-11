# Full post-deployment audit and operations record — 2026-11-12

Owner-facing. Written to be read cold at 03:00 with no other tab open.

Everything below is one of three things, and the label is always explicit:

- **[LIVE]** — runtime fact verified against the deployed system by the operator/parent
  session during this deploy. Not re-verified by the author of this document.
- **[SRC]** — read directly from the source tree at the cited `path:line`.
- **[INF]** — inference or reasoning from the two above. Not observed.

Deployed trading revision: **`9481197`** on branch `fix/audit-2026-11-12`.
Rollback revision: tag **`pre-audit-2026-11-12`** = **`d9075a5`** (verified: `git rev-parse --short pre-audit-2026-11-12^{commit}` → `d9075a5`).

Detail runbook for rolling this back: [`docs/AUDIT-ROLLBACK-2026-11-12.md`](AUDIT-ROLLBACK-2026-11-12.md).

---

## 1. Status card

### 1.1 Mode and lane state

| Field | Value | Evidence |
|---|---|---|
| Mode | `LIVE` / venue `LIVE` | [LIVE]; dispatcher pins `TRADER_MODE: ${TRADER_MODE:-LIVE}` (`docker-compose.yml:210`) and `BITGET_MODE: ${BITGET_MODE:-LIVE}` (`docker-compose.yml:220`) |
| Execution | enabled (`BITGET_EXECUTION_ENABLED=1`) | [LIVE]; `docker-compose.yml:223`; reported as `live_execution_enabled` in telemetry (`src/fatty_trader/web/health.py:66-72`) |
| Health status | `ready`, `/health/telemetry` answering | [LIVE]; handler `src/fatty_trader/web/app.py:59-61`, payload builder `src/fatty_trader/web/health.py:12-18` |
| Kill switch `bitget` | **inactive** | [LIVE]; monitor scope defaults to `bitget` (`src/fatty_trader/execution/bitget_monitor.py:297`) |
| Kill switch `bitget-protection-stream` | **inactive** | [LIVE]; `src/fatty_trader/execution/bitget_protection_watchdog.py:43` |
| Containers | all healthy, 10 required services | [LIVE]; required set at `scripts/live_watchdog.py:40-51` |
| Watchdog | `fatty-watchdog.service` (systemd user unit) active, last status `ok`; cards delivered through the bot's outbox | [LIVE]; state file `~/.local/state/fatty-watchdog/status.json`; card path `scripts/live_watchdog.py:430-463` |
| Migration | 38 applied | [LIVE]; DDL `src/fatty_trader/storage/migrations.py:530-546`; ledger table `schema_migrations` (`src/fatty_trader/storage/migrations.py:41`) |
| Trading revision (deployed) | `9481197` | [LIVE] + `git log` at handoff |
| Host HEAD | `5eba213` — six ops-only commits after `9481197` (`6704b77`, `5eb6f6c`, `61e918a`, `db0fc04`, `f15b43e`, `5eba213`), all under `scripts/` | [SRC] `git log --oneline -6`; `git log 9481197..HEAD -- src docker-compose.yml Dockerfile` prints nothing |
| Rollback revision | `pre-audit-2026-11-12` = `d9075a5` | [SRC] `git rev-parse` |

### 1.2 Open positions and protection

| Symbol | Side | Size | Entry | Stop | Target | Notes |
|---|---|---|---|---|---|---|
| `BRETTUSDT` | LONG | 5920 | 0.0036875 | 0.003595 | 0.00407 | [LIVE]; native SL/TP working; both intents moved `unknown` → `acknowledged` during the deploy |
| `ICPUSDT` | LONG | 1.07 at entry | 2.562 | 2.562 (= entry) | 2.7 | [LIVE]; `TP1_BOOKED` executed correctly during the deploy: half closed at 2.601, the 75% LIMIT leg cancelled, stop moved to entry. Venue now holds the TP1 remainder — read the exact size with the probe in §4.3, never recompute it from 1.07 |

Both positions were protected natively at handoff [LIVE]. "Protected" means: a live `pos_loss`
plan exists on the venue for the position's own size. It does **not** mean the local fallback
lane would cover a gap [INF].

### 1.3 Effective knobs at this revision

| Knob | Value | Where defined | What it would reverse |
|---|---|---|---|
| `BITGET_MANAGEMENT_MAX_SOURCE_AGE_SECONDS` | `1800` | `docker-compose.yml:439` | `300` restores the old effective 5-minute management budget |
| `BITGET_LATCH_TRANSIENT_STRIKES` | `3` | `docker-compose.yml:316` | `1` latches a transient read failure on the first cycle |
| `BITGET_LATCH_TRANSIENT_AUTORELEASE` | `1` | `docker-compose.yml:317` | `0` keeps a transient latch until an operator releases it |
| `BITGET_PROTECTION_VERIFICATION_STALE_SECONDS` | `3600` | `docker-compose.yml:227` | a very large value disables native-verification freshness |
| `WORKER_HEALTH_ATTESTATION` | `token` (default) | `src/fatty_trader/web/runtime_probe.py:61-62` | `identity` is opt-in hardening, not a rollback |
| `BITGET_PROTECTION_CAPABILITY_GATE_ENABLED` | `0` | `docker-compose.yml:225` | unchanged; **the capability admission path is dormant** |

### 1.4 Three facts that will mislead you if you assume otherwise

1. **The protection capability gate is off.** `build_bitget_protection_admission` returns `None`
   before it reads any capability row (`src/fatty_trader/service.py:195-199`). The native
   verification freshness fix and the "fallback lane must exist at runtime" fix are therefore
   compiled in and **not in force** [SRC + LIVE].
2. **The health position count is bot-owned entry exposure only.** It counts the newest ENTRY
   dispatch per symbol whose state is `FILLED`/`PARTIALLY_FILLED`
   (`src/fatty_trader/operator/health.py:381-391`). A manual or baseline position on the venue
   is not counted, by design.
3. **The watchdog can only observe and page.** It has no provider mutation path
   (`scripts/live_watchdog.py:11-13`). Do not treat a watchdog card as an action already taken.

---

## 2. What changed and why — one row per commit

Eight commits sit between the rollback tag and the deployed trading revision
(`git log --oneline pre-audit-2026-11-12..9481197` → 8). Six further ops-only commits sit on top of
`9481197`; they change only `scripts/` and are listed after this table.

| Commit | Defect it fixed | Observable symptom before | Fix (source) | Reverse with |
|---|---|---|---|---|
| `33a9c1d` | Management instructions shared the five-minute *entry* clock, and an ineligible instruction failed without a durable reason | A `CLOSE`/`TP1_BOOKED`/`SL_TO_ENTRY` posted 6+ minutes earlier silently failed; no reason on the row | Management gets its own eligibility predicate and age cap (`src/fatty_trader/intake/freshness.py:164-178`), used under the update lock (`src/fatty_trader/storage/source_management.py:219-256`), with stable reason codes written to `last_error` (`src/fatty_trader/storage/source_management.py:168-176`, `src/fatty_trader/execution/source_management.py:158-163`). Analyzer selection widened to a 60-minute backfill (`src/fatty_trader/intake/freshness.py:141-158`, used at `src/fatty_trader/analyzer/postgres_worker.py:30`) | `BITGET_MANAGEMENT_MAX_SOURCE_AGE_SECONDS=300` restores the old age budget; the 60-minute analysis window is a constant, so revert the commit |
| `530872a` | A cancel POST could be issued by a management instruction that did not own the cancellation claim; a pre-POST veto on a split leaked its margin reservation; recovery re-appended same-state dispatch edges | Two instructions could both claim one cancel; `bitget_margin_reservations` accumulated `reserved` rows after a split veto; `dispatch_transitions` filled with `from_state = to_state` rows | Cancellation claim bound to `management_id` (`src/fatty_trader/storage/live_intents.py:516-536`, `src/fatty_trader/exchanges/bitget/live.py:223`, `src/fatty_trader/console/trading.py:554-577`) and a refusal when another instruction owns it (`src/fatty_trader/console/trading.py:857-870`, `src/fatty_trader/execution/bitget_dispatch_execution.py:689-750`); split veto now resolves the reservation (`src/fatty_trader/execution/bitget_dispatch_execution.py:644-658`); same-state transition is a no-op (`src/fatty_trader/execution/bitget_dispatch_repository.py:483-488`) | Revert `530872a`; no environment knob |
| `587c42c` | Native position TP/SL are *plan* orders: they never appear in order detail or pending orders, so the generic classifier reported them `unknown` forever. The reconcile loop also rewrote unchanged intents every cycle | SL/TP intents stuck `unknown`; constant `live_order_intents` UPDATE churn | Plan-endpoint reconciliation for `SL`/`TP` roles (`src/fatty_trader/exchanges/bitget/reconciliation.py:138-162`), wired to the monitor (`src/fatty_trader/execution/bitget_monitor.py:461`); durable cause on unresolved intents (`src/fatty_trader/exchanges/bitget/reconciliation.py:240-241`); write only when observation changed (`src/fatty_trader/execution/bitget_monitor.py:452-464`, `src/fatty_trader/execution/bitget_monitor.py:705-716`); migration 38 (`src/fatty_trader/storage/migrations.py:530-546`) | Revert `587c42c`; **do not** drop migration 38 — the columns/indexes are harmless to old code |
| `da59b71` | A single failed provider read latched the entry lane permanently; the latch healer would release a latch it could not observe | On 2026-10-18 a single timeout closed the lane for days (documented at `src/fatty_trader/execution/bitget_monitor.py:39-46`) | Transient reasons enumerated (`src/fatty_trader/execution/bitget_monitor.py:60-70`), bucketed (`:73-78`), counted per cycle and latched only at the strike limit (`:380-397`), auto-released on the first clean cycle (`:404-421`); healer restricted to reasons it can verify (`src/fatty_trader/execution/latch_healer.py:21-27`, `:123-125`); knobs parsed and validated (`src/fatty_trader/service.py:1447-1461`) | `BITGET_LATCH_TRANSIENT_STRIKES=1` + `BITGET_LATCH_TRANSIENT_AUTORELEASE=0` |
| `8629de6` | Health derived exposure from the `positions` table, which the live Bitget lane does not populate; `reconciliation_state` was never written by the monitor | Health/diagnostic showed 0 open positions and 0 pending orders while the venue held a live trade; the console reconciliation panel showed stale or missing data | Exposure/pending derived from `live_order_intents` joined to `dispatches` (`src/fatty_trader/operator/health.py:87-98`, `:381-398`); monitor upserts `reconciliation_state` every cycle (`src/fatty_trader/execution/bitget_monitor.py:320-334`, `src/fatty_trader/storage/reconciliation.py:304-330`), table at `src/fatty_trader/storage/schema.py:311-318`, read by the console at `src/fatty_trader/console/reads.py:813-816` | Revert `8629de6`; no knob |
| `0a1692d` | A months-old native "VERIFIED" capability was treated as current proof; protection admission could promise a fallback lane the process cannot register; worker health required a token that a token-free deployment cannot supply | Old capability rows admitted symbols with no current verification; a fallback verdict could be admitted by a process with fallback mutations disabled | Freshness on its own clock (`src/fatty_trader/exchanges/bitget/protection_capability.py:14-15`, `:104-118`), fallback admission requires the runtime lane (`src/fatty_trader/service.py:224-249`), optional token-free attestation (`src/fatty_trader/worker_health.py:32-34`, `:75-90`, `:110-116`; `src/fatty_trader/web/runtime_probe.py:28-43`, `:61-62`) | `BITGET_PROTECTION_VERIFICATION_STALE_SECONDS` very large (disables freshness); `WORKER_HEALTH_ATTESTATION=token` is the default |
| `4e00097` | Regressions did not match the new behaviour; no rollback runbook | Tests would fail or assert the old semantics; no documented per-commit revert | Test and signature alignment plus the runbook (`docs/AUDIT-ROLLBACK-2026-11-12.md`); one new regression: three strikes then auto-release (`tests/unit/test_bitget_monitor.py:932`) | Revert `4e00097` (tests/docs only) |
| `9481197` | The new knobs existed in code but were not plumbed, so a `.env` change never reached the container | Tuning `.env` had no effect until a code edit | Knobs plumbed with the same defaults (`docker-compose.yml:227`, `:316-317`, `:439`) and rollback documented | Revert `9481197`; the in-code defaults then apply |

### Ops-only commits on top of `9481197`

Six commits, each touching only `scripts/` (verified with `git show --stat`): no `src/`,
`docker-compose.yml` or `Dockerfile` change, so the container's trading behaviour is still the
audited one. `Dockerfile:23` copies `scripts/` into the image, so an image that can run the probe
was built at or after `6704b77`.

| Commit | Change |
|---|---|
| `6704b77` | adds the read-only host watchdog and the in-container venue probe |
| `5eb6f6c` | keeps `scripts/live_watchdog.py` importable on the host's Python 3.9 |
| `61e918a` | carries reason codes into the card text |
| `db0fc04` | parses compose v2 NDJSON `ps` output; the probe is carried into the monitor image by the rebuild |
| `f15b43e` | no `ok` card on first run; a failed plan read no longer escalates as a false `position-unprotected` |
| `5eba213` | warnings must repeat 3 consecutive cycles before paging; criticals page immediately |

---

## 3. Ranked remaining risks

Ranked by (severity if triggered) × (likelihood in the next week). Nothing here is fixed.

### R1 — A management instruction up to 30 minutes old will still execute

- **Trigger:** the asleep/away owner posts `SL_TO_ENTRY`, `CLOSE` or `TP1_BOOKED`, the
  source-management container is down or busy, and it recovers within 30 minutes.
- **Symptom:** a stop moves, or a position closes, from an instruction the owner may already
  consider stale. Row shows `state='claimed'` then a terminal outcome with `last_error IS NULL`.
- **Mechanism:** `BITGET_MANAGEMENT_MAX_SOURCE_AGE_SECONDS=1800` (`docker-compose.yml:439`,
  default at `src/fatty_trader/storage/source_management.py:63`) gates only age; revision
  identity is the integrity contract, not recency
  (`src/fatty_trader/storage/source_management.py:219-256`).
- **Blast radius:** real stop movement or a real reduce-only close on a live position. One
  symbol per instruction. The 1.1 USDT per-trade cap bounds *entries* only; it does not bound the
  size of a close.
- **What NOT to do:** do not "cancel" an in-flight management update by `UPDATE`-ing
  `source_management_updates.state`; the executor re-checks eligibility under a row lock and a
  half-edited row becomes unreconcilable. Do not stop the `source-management` container while a
  claim is unproven.
- **Safe response:** if the instruction is no longer wanted, edit or delete the *source message*
  only if that is genuinely what happened (an edited message is detected as
  `management-source-revision-superseded`); otherwise let it reach a terminal state and verify the
  resulting position/protection readback before intervening. Lower the knob to `300` and restart
  if 30 minutes is unacceptable tonight — that is a pure environment change
  (`docker-compose.yml:439`).

### R2 — The protection capability gate is disabled, so the newest protection fixes are inert

- **Trigger:** someone enables `BITGET_PROTECTION_CAPABILITY_GATE_ENABLED=1` expecting the
  freshness and fallback-lane fixes.
- **Symptom:** with the gate at `1`, symbols whose capability row is stale or absent are refused
  at admission with `native-verification-stale` / `native-verification-never-observed` /
  `fallback-runtime-disabled` — i.e. entries stop. With the gate at `0`, none of those checks run
  at all.
- **Mechanism:** early return `None` when the gate is `0`
  (`src/fatty_trader/service.py:195-199`); the checks live behind it
  (`src/fatty_trader/service.py:224-249`, `src/fatty_trader/exchanges/bitget/protection_capability.py:104-118`).
- **Blast radius:** enabling it with a cold `bitget_protection_capabilities` table closes the
  entry lane for every symbol. Leaving it off means a stale capability can never block an entry.
- **What NOT to do:** do not enable the gate and the fallback lane in the same restart; you lose
  the ability to tell which one refused the entry.
- **Safe response:** treat the gate as a deliberate migration project, not a knob. Read the
  capability table first (§4.3), confirm `last_verified_at` is fresh for the symbols you trade,
  and only then enable, during a session where someone is watching the dispatcher logs.

### R3 — Dispatcher recovery re-persists the open ICP ENTRY pair every poll

- **Trigger:** every dispatcher recovery cycle while a bot-owned entry intent is filled and its
  dispatch is not `CLOSED`/`RECONCILED`.
- **Symptom:** a GET-only pass of roughly 35–60 s per cycle [LIVE], with an unconditional
  `live_order_intents` UPDATE even when nothing changed
  (`src/fatty_trader/execution/bitget_dispatch_execution.py:991` called from
  `:128`), because recovery candidates keep including the pair
  (`src/fatty_trader/execution/bitget_dispatch_repository.py:219-251`: exclusion is only
  `d.state NOT IN ('CLOSED','RECONCILED')` plus baseline receipts).
- **Mechanism:** the "write only when changed" guard added in `587c42c` covers the **monitor's**
  unknown-intent loop (`src/fatty_trader/execution/bitget_monitor.py:452-464`), not
  `_persist_readback` in the dispatcher. Readback *is* protected against erasing fills — a
  quantity regression raises (`src/fatty_trader/execution/bitget_dispatch_execution.py:973-975`).
- **Blast radius:** provider rate-limit budget, journal/IO churn, and a slower critical
  reconciliation path. Not a correctness defect in trading state.
- **What NOT to do:** do not "fix" it by transitioning the ICP dispatch to `CLOSED` by hand, and
  do not delete the intent. Both destroy the ownership record the recovery path depends on.
- **Safe response:** leave it running. If the latency matters, the correct fix is to apply the
  same observation-comparison guard inside `_persist_readback` — a code change, not an
  operational action.

### R4 — Three stale October dispatches are still non-terminal

- **Trigger:** already true. One of the three is excluded from recovery only by an audited
  baseline receipt (`docs/AUDIT-ROLLBACK-2026-11-12.md:82-83`).
- **Symptom:** `dispatches` rows from October with non-terminal states; they can surface as
  recovery candidates or inventory issues on every pass.
- **Mechanism:** recovery excludes rows with a matching `bitget_baseline_records` entry
  (`src/fatty_trader/execution/bitget_dispatch_repository.py:238-251`) and reports the rest as
  `orphan-entry-intent` / `reservation-without-entry-intent`
  (`src/fatty_trader/execution/bitget_dispatch_repository.py:378-424`).
- **Blast radius:** recovery readiness noise and repeated work; a manual "cleanup" is the real
  danger.
- **What NOT to do:** do not mass-transition them to `REJECTED`/`CLOSED`, and do not invent
  baseline rows to silence them.
- **Safe response:** reconcile them as a separate, evidence-backed data operation with an
  approval reference — the same way the existing baseline was produced. Until then, treat
  `orphan-entry-intent` in the inventory as a known constant [INF: compare counts across cycles
  before reacting to a change].

### R5 — Web-push fan-out depends on the Telegram worker being configured

- **Trigger:** web-push VAPID config present, Telegram bot config absent or invalid.
- **Symptom:** the console never receives push notifications for trades/alerts, while the
  standalone push lane still reports `idle`.
- **Mechanism:** `fan_out` is only wired into the `NotificationWorker`, and that worker is only
  constructed when Telegram settings parse
  (`src/fatty_trader/notification_service.py:166-175`); the independent push worker delivers only
  rows that already exist (`:151-165`, `:180-190`).
- **Blast radius:** silent loss of the outbound alerting path — including watchdog cards.
- **What NOT to do:** do not "test" it by sending a real trade alert; and do not assume
  `notifications_outbox` rows being `sent` means push was delivered.
- **Safe response:** verify by reading the sender's log line
  (`service=notification-sender state=... push=...`,
  `src/fatty_trader/notification_service.py:192-195`). If Telegram is missing, fix Telegram
  config; push alone will not fan out.

### R6 — Health "open positions" is a narrow join that can read 0 while the venue holds a position

- **Trigger:** a position that is (a) manual/baseline, or (b) whose newest ENTRY dispatch is not
  `FILLED`/`PARTIALLY_FILLED`.
- **Symptom:** health/diagnostic says 0 open positions; the venue probe shows a live position.
- **Mechanism:** `DISTINCT ON (i.symbol) ... WHERE latest.state IN ('FILLED','PARTIALLY_FILLED')`
  (`src/fatty_trader/operator/health.py:381-391`). The state machine also declares `PROTECTING`
  and `ACTIVE` (`src/fatty_trader/domain/state_machines.py:32-38`) which no runtime path currently
  sets [SRC: no `PROTECTING`/`ACTIVE` assignment exists outside `domain/`], so today only
  `FILLED`/`PARTIALLY_FILLED` describe a live bot trade. If a future path starts setting
  `ACTIVE`, this count silently drops to 0.
- **Blast radius:** a false "we are flat" reading during an incident. No trading effect.
- **What NOT to do:** do not conclude the bot is flat from health alone.
- **Safe response:** the venue probe in §4.3 is the authority for "what is on the exchange";
  health is the authority for "what the bot owns".

### R7 — `BitgetOperatorGateway.close_position` synthesizes a close

- **Trigger:** any future caller wiring this method into a live path.
- **Symptom:** when the symbol already reads flat, it returns `state: "closed"`; when a POST
  succeeds and the position reads flat, it sets `intent.filled_qty = position["size"]` and marks
  the intent `reconciled` **without** provider fill evidence
  (`src/fatty_trader/operator/bitget_gateway.py:271-274`).
- **Mechanism:** it treats "flat now" as proof of "this close filled" — the exact evidence
  contract the verified-close path exists to enforce.
- **Blast radius:** fabricated close/fill truth in the ledger if ever wired.
- **What NOT to do:** do not wire it, do not "improve" it in place.
- **Safe response:** it has no production caller today [SRC: only `tests/` reference it]. Leave it
  dead. If a close is needed, use the console/verified-close path.

### R8 — The watchdog is installed and working, but nothing tests it

- **Trigger:** a future edit to `scripts/live_watchdog.py` or `scripts/watchdog_probe.py`.
- **Current state [LIVE]:** installed as the systemd user unit `fatty-watchdog.service` (enabled,
  lingering on, active), 60 s interval, last status `ok`, 2 positions, 0 open orders, clock skew
  -26 ms, `reconciliation_state` age ~10 s. A transient clock read failure produced a degraded card
  and an `ok` recovery card, both delivered (`sent_at` set); after the 3-strike policy the same
  blip produced no new card, and `FATTY_WATCHDOG_ALERTS=0` dry runs produced `status: ok` with no
  reasons. The compose v2 NDJSON parser bug was real and is fixed
  (`scripts/live_watchdog.py:134-171`), and the probe is present in the monitor image after
  `docker compose up -d --build`.
- **Residual:** no regression tests cover either script [SRC: no `tests/` reference to
  `live_watchdog` or `watchdog_probe`]. The parser and the 3-consecutive-warning policy
  (`scripts/live_watchdog.py:522-533`, first-observation suppression at `:539-540`) are verified
  only by live observation, so an edit can regress them silently.
- **Blast radius:** alert fatigue, or a missed page. Neither script can mutate the venue, so the
  trading blast radius is zero.
- **What NOT to do:** do not act on a single card without running the §4 command for that symptom;
  do not silence alerts with `FATTY_WATCHDOG_ALERTS=0` and then forget it.
- **Safe response:** after any edit, restart the unit and confirm one card in, one card out; before
  acting on a card, verify it with the matching §4 command — if they disagree, the watchdog is
  wrong.

---

## 4. Symptom → first command → interpretation

Run from the deployment checkout. One wrapper, defined once per shell:

```bash
cd /home/valarion/apps/fatty-multi-exchange-trader
q() { docker compose exec -T postgres psql -U fatty_app -d fatty_trader -At -c "$1"; }
probe() { docker compose exec -T monitor-bitget sh -lc \
  'cd /app && PYTHONPATH=src /app/.venv/bin/python scripts/watchdog_probe.py'; }
```

`q` is read-only in every command below. The role matches the one the watchdog itself uses
(`scripts/live_watchdog.py:114-132`); the probe is GET-only
(`scripts/watchdog_probe.py:30-50`).

### 4.0 Summary table

| Symptom you observe | First command | Read it like this |
|---|---|---|
| Bot stops taking trades | `q "SELECT scope, active, reason FROM venue_kill_switches WHERE active;"` (§4.1) | any row = latch closed the entry lane; no rows = look at dispatches/messages, not policy |
| Latch active | `q "SELECT scope, reason, latched_at FROM venue_kill_switches WHERE active;"` (§4.2) | strike-counted reason older than a few cycles = real; `unexpected-*` = durable, needs the approved release |
| Position with no plan | `probe` (§4.3) | missing `pos_loss` plan on a live position = critical; `errors[]` = unknown, not missing |
| Management failed | `q "SELECT action, symbol, last_error FROM source_management_updates WHERE state<>'reconciled' ORDER BY updated_at DESC LIMIT 20;"` (§4.4) | the reason code names the cause; never re-post by hand |
| Health says 0 positions, venue has one | `probe` + the DISTINCT ON query (§4.5) | health counts bot-owned entries only; the probe is the venue authority |
| Intents stuck non-terminal | `q "SELECT client_order_id, role, state, last_error FROM live_order_intents WHERE exchange='bitget' AND state NOT IN ('filled','rejected','cancelled','reconciled');"` (§4.6) | SL/TP resolves via the plan endpoint within a cycle; `provider-order-unresolved` = real divergence |
| Clock-skew latch | `q "SELECT reason FROM venue_kill_switches WHERE active AND reason LIKE 'clock-skew%';"` + probe `clock_skew_ms` (§4.7) | unavailable = transient; exceeded with a persistently large skew = fix the host clock |
| Container unhealthy | `docker compose ps --format 'table {{.Service}}\t{{.State}}\t{{.Health}}'` (§4.8) | dispatcher unhealthy = stale cycle heartbeat, not necessarily a crash |
| `reconciliation_state` stale | `q "SELECT scope, now()-last_run_at FROM reconciliation_state;"` (§4.9) | above ~90 s the monitor is not completing cycles; no row = the write is failing |

### 4.1 Bot stops taking trades

```bash
q "SELECT scope, active, reason, latched_at FROM venue_kill_switches ORDER BY scope;"
q "SELECT state, count(*) FROM dispatches WHERE exchange='bitget'
     AND created_at > now() - interval '6 hours' GROUP BY 1 ORDER BY 2 DESC;"
q "SELECT intake_state, count(*) FROM telegram_messages
     WHERE received_at > now() - interval '6 hours' GROUP BY 1;"
```

Interpretation, in order:

- Any row with `active = true` → the entry lane is closed by a safety latch. Go to §4.2.
- No dispatches at all in 6 h → the signal lane, not trading, is the suspect. `intake_state`
  all `ANALYZED` with no dispatches means preflight rejected or nothing arrived.
- Dispatches present but all `EXPIRED`/`REJECTED` → read `terminal_reason` on those rows; the
  reason is durable by design [SRC: `src/fatty_trader/execution/bitget_dispatch_repository.py:474-500`].
- Dispatches `QUEUED` and not moving → the dispatcher container, not policy. Go to §4.8.

### 4.2 A latch is active

```bash
q "SELECT scope, reason, latched_at, now()-latched_at AS age FROM venue_kill_switches WHERE active;"
docker compose logs --since 30m monitor-bitget | grep -i "released transient kill switch" | tail -5
```

Interpretation:

- `reason` in `{clock-skew-unavailable, provider-fills-read-failed,
  provider-orders-read-failed, provider-positions-read-failed,
  verified-close-evidence-unavailable}` or prefixed `unknown-intent-unreconciled:` → the
  *strike-counted* class (`src/fatty_trader/execution/bitget_monitor.py:60-70`). It needs three
  consecutive cycles to latch (`:385-388`) and clears on the first clean cycle (`:404-421`).
- `reason` in `{clock-skew-unavailable, clock-skew-exceeded,
  post-fill-margin-or-leverage-mismatch}` → *auto-releasable* (`:47-52`), but only
  `clock-skew-exceeded` and the post-fill mismatch latch immediately; the release branch is the
  same one (`:405-421`).
- A latch younger than ~2 minutes on a strike-counted reason is expected behaviour, not an
  incident. A latch older than a few cycles on any transient reason is not.
- `unexpected-order`, `unexpected-position`, or a post-fill mismatch → **durable**. It will not
  self-clear; the healer refuses it explicitly (`src/fatty_trader/execution/latch_healer.py:123-125`).
  Release requires the audited, digest-bound path with an approval reference
  (`docs/OPERATIONS.md:220-222`).
- Do not `UPDATE venue_kill_switches`. A hand-cleared latch leaves the cause in place and removes
  the only signal that the entry lane is unsafe.

### 4.3 A position with no protection plan

```bash
probe
q "SELECT symbol, native_state, fallback_allowed, last_verified_at, stream_state, last_error
     FROM bitget_protection_capabilities WHERE exchange='bitget' ORDER BY symbol;"
```

Interpretation:

- `probe` prints `positions[]` and `plans{}` keyed by symbol. For each live position look for a
  plan with `plan_type = "pos_loss"` and `plan_status = "live"`. Missing `pos_loss` on a live
  position is the emergency: the bot's own watchdog raises `position-unprotected:<SYMBOL>` as
  critical (`scripts/live_watchdog.py:386-400`).
- Missing `pos_profit` is only `position-no-target` (warning, `scripts/live_watchdog.py:401-409`).
- `errors[]` in the probe output (`plans-unavailable:<symbol>:...`,
  `scripts/watchdog_probe.py:68-71`) means the plan read failed — that is an unknown, not a
  missing plan. Re-run before escalating.
- Capability rows do **not** gate anything while the gate is off (§1.4), so a stale
  `last_verified_at` is evidence, not a block.

### 4.4 Management instruction failed

```bash
q "SELECT action, symbol, state, last_error, attempts, deadline_at, updated_at
     FROM source_management_updates
     WHERE state NOT IN ('reconciled') ORDER BY updated_at DESC LIMIT 20;"
```

Interpretation of `last_error` (all produced at
`src/fatty_trader/storage/source_management.py:249-256`):

| `last_error` | Meaning |
|---|---|
| `management-source-too-old` | Source older than `BITGET_MANAGEMENT_MAX_SOURCE_AGE_SECONDS`. Expected for an old instruction; not corruption. |
| `management-source-revision-superseded` | The channel message was edited after enrollment. The instruction is void by design. |
| `management-source-edited-source-message` / `management-source-superseded-source-revision` | Same class, expressed from the message row. |
| `management-source-not-analyzed` | `intake_state` is not `ANALYZED` — the analyzer never finished this message. |
| `management-source-ineligible` | Predicate failed for another reason; re-read the message row. |
| `management-update-missing` | The update row vanished between claim and recheck. Treat as a data anomaly. |

A `failed` row is also announced by a deduplicated card (`src/fatty_trader/execution/source_management.py:158-163`),
and the watchdog reports it as `management-failed:<ACTION>` with the symbol and error
(`scripts/live_watchdog.py:306-313`). Nothing should be re-posted by hand: the executor dedups on
`(source_message_id, revision, symbol, action)` and will not re-claim a terminal row.

### 4.5 Health says 0 positions but the venue has one

```bash
probe
q "SELECT DISTINCT ON (i.symbol) i.symbol, d.state, d.terminal_reason, d.updated_at
     FROM dispatches d
     JOIN live_order_intents i ON i.dispatch_id=d.id
       AND i.exchange='bitget' AND i.role='ENTRY'
     WHERE d.exchange='bitget'
     ORDER BY i.symbol, d.created_at DESC;"
curl -s http://127.0.0.1:18081/health/telemetry | python3 -m json.tool | head -40
```

Interpretation:

- Probe shows the position, the query shows `state` outside `FILLED`/`PARTIALLY_FILLED` (or no
  row at all) → the position is baseline/manual or its dispatch is closed. Health is correct
  about *bot ownership* and wrong about *venue exposure*; that is the documented scope
  (`src/fatty_trader/operator/health.py:381-391`).
- Probe shows the position and the query shows `FILLED`/`PARTIALLY_FILLED` → health and DB
  disagree with the code, which means the web container is serving a stale payload: check §4.9.
- Never treat health as flat/exposure authority. Use the probe.

### 4.6 Intents stuck non-terminal

```bash
q "SELECT client_order_id, role, state, last_error, provider_order_id, filled_qty, updated_at
     FROM live_order_intents
     WHERE exchange='bitget' AND state NOT IN ('filled','rejected','cancelled','reconciled')
     ORDER BY updated_at;"
```

Interpretation:

- `role IN ('SL','TP')` with `state='unknown'` → before `587c42c` these could never resolve.
  Now the monitor proves them through the plan endpoint
  (`src/fatty_trader/exchanges/bitget/reconciliation.py:138-162`); a plan leg is matched by
  provider order id or by client oid (`:150-160`). Give it one monitor cycle
  (`BITGET_MONITOR_POLL_SECONDS`, default 30 s, `docker-compose.yml:242`).
- `last_error='provider-order-unresolved'` (`src/fatty_trader/exchanges/bitget/reconciliation.py:240-241`)
  means the provider read succeeded but nothing matched. That is a real divergence: do not
  cancel, do not re-POST, do not rewrite `state`. The monitor alerts it as
  `intent-error:<client_oid>` (`scripts/live_watchdog.py:290-297`).
- `role='ENTRY'`, `order_type='limit'`, non-terminal → this is the waiting 75% leg by design
  (`docs/TRADING-RULES.md` describes the 25/75 split). A waiting limit leg is not "stuck".
- `cancel_post_claimed = true` with a non-terminal state → a cancel POST was claimed; only GET
  reconciliation is allowed from here (`docs/TRADING-RULES.md`, cancellation boundary).

### 4.7 Clock-skew latch

```bash
q "SELECT scope, reason, latched_at FROM venue_kill_switches
     WHERE active AND reason LIKE 'clock-skew%';"
probe | python3 -c 'import json,sys; print(json.load(sys.stdin)["clock_skew_ms"])'
```

Interpretation:

- `clock-skew-unavailable` → the venue clock read failed. Transient: 3 strikes to latch,
  auto-release on a clean cycle (`src/fatty_trader/execution/bitget_monitor.py:60-70`, `:404-421`).
- `clock-skew-exceeded` → skew above `BITGET_MAX_CLOCK_SKEW_MS` (default 5000,
  `docker-compose.yml:240`); the monitor also auto-releases it once a cycle reads inside the
  limit (`src/fatty_trader/execution/bitget_monitor.py:47-52`, `:405`). If the probe's
  `clock_skew_ms` stays above 5000, the host clock is wrong — fix NTP on the host, not the
  database.
- A stuck skew latch can also be cleared through the approved path, which requires a fresh
  bounded midpoint sample and a digest-bound approval reference (`docs/OPERATIONS.md:220-222`).

### 4.8 A container is unhealthy

```bash
docker compose ps --format 'table {{.Service}}\t{{.State}}\t{{.Health}}'
docker compose logs --since 20m --tail 120 dispatcher-bitget monitor-bitget
q "SELECT (SELECT count(*) FROM notifications_outbox WHERE sent_at IS NULL AND failed_at IS NULL) AS outbox_pending,
          (SELECT count(*) FROM notifications_outbox WHERE failed_at IS NOT NULL
             AND failed_at > now() - interval '24 hours') AS outbox_failed;"
```

Interpretation:

- `dispatcher-bitget` unhealthy specifically means the cycle heartbeat is stale — its healthcheck
  validates configuration *and* a fresh cycle, added because of the 3 h 50 m silent stall
  (`docker-compose.yml:318-322` comments).
- `monitor-bitget` unhealthy means the monitor cycle stopped; that also stops
  `reconciliation_state` (§4.9) and stops latch auto-release.
- A large `outbox_pending` with `outbox_failed = 0` is a slow sender, not a trading fault. Any
  `outbox_failed` means cards are being lost — that is how a watchdog page silently disappears.
- Prefer `docker compose restart <service>` over a full `up -d`; a full recreate re-runs
  migrations and re-reads the environment.

### 4.9 `reconciliation_state` is stale (or absent)

```bash
q "SELECT scope, last_run_at, last_success_at, mismatch_count,
          extract(epoch FROM (now()-last_run_at)) AS age_seconds
     FROM reconciliation_state ORDER BY scope;"
```

Interpretation:

- Written by the monitor every cycle (`src/fatty_trader/execution/bitget_monitor.py:320-334`
  → `src/fatty_trader/storage/reconciliation.py:304-330`). With a 30 s poll, `age_seconds`
  above ~90 means the monitor is not completing cycles.
- No row at all for `bitget` → the monitor has not completed a cycle since this fix was deployed,
  or the write is failing; the monitor logs `reason=reconciliation-state-write-failed` and
  continues (`src/fatty_trader/execution/bitget_monitor.py:330-334`). A failing write is
  swallowed by design, so absence is the only symptom — check the monitor log.
- `mismatch_count > 0` means that many reasons were counted in the last cycle, excluding
  alert-only protection reasons (`src/fatty_trader/execution/bitget_monitor.py:326-329`). Compare
  it against §4.2 rather than treating the number as a position count.
- The watchdog pages `reconciliation-never-ran` / `reconciliation-stale` for exactly this
  (`scripts/live_watchdog.py:271-281`).

---

## 5. Fixed audit findings with evidence

Each finding: what was wrong, why it was wrong (the reasoning that must survive), what shipped, and
how to confirm it. "Not fixed" is stated explicitly so nobody re-opens a closed item.

### F1 — Management instructions were judged by the entry clock

**Wrong:** `source_management_updates` was gated by `SOURCE_ELIGIBLE_SQL`, the five-minute entry
recency predicate. A `CLOSE` posted six minutes before the worker got to it was rewritten to
`failed`. The failure also left no reason, so at 03:00 the row said `failed` and nothing else.

**Why it mattered:** management is not an entry. Its integrity contract is *revision identity*
(the message was not edited or superseded), not recency. Coupling them meant the bot silently
ignored instructions precisely when it was slow.

**Shipped:** three distinct predicates — analysis (60-minute backfill,
`src/fatty_trader/intake/freshness.py:141-158`), entry admission (unchanged,
`src/fatty_trader/intake/freshness.py:102`), management (`MANAGEMENT_ELIGIBLE_SQL`, validation of the injected integer
at `src/fatty_trader/storage/source_management.py:63-68`,
`src/fatty_trader/intake/freshness.py:164-178`). The ineligibility reason is a stable code, not a
bare `ValueError` (`src/fatty_trader/storage/source_management.py:219-256`), threaded through
`_finish` (`src/fatty_trader/execution/source_management.py:158-163`) and re-checked before a new
mutation (`:367-372`).

**Confirm:** `last_error` is populated on a failed row (§4.4). The analyzer's selection predicate
is asserted in `tests/unit/test_analyzer_worker.py` (aligned by `4e00097`).

**Not fixed:** the 30-minute budget still executes an old instruction (R1), and
`source-age expiry is not a failure` only holds for the *executor* recheck — the worker's own
dead-letter path still writes `failed` when `deadline_at` passes
(`src/fatty_trader/storage/source_management.py:74-77` deliberately narrowed this).

### F2 — Two instructions could both own one cancellation

**Wrong:** `claim_cancel` bound a claim to `management_id`, but the POST claim
(`claim_cancel_post`) only required `cancel_requested_by IS NOT NULL`. Any instruction could
therefore ride another instruction's claim and POST a cancel it did not own. The in-memory store
had the same hole (`src/fatty_trader/exchanges/bitget/live.py:322-330`).

**Why it mattered:** duplicate/racing management instructions could produce a cancel POST with no
owning instruction to reconcile it — an unreconcilable provider mutation.

**Shipped:** the POST claim requires the same `management_id`
(`src/fatty_trader/storage/live_intents.py:516-536`,
`src/fatty_trader/console/trading.py:554-577`), the protocol and
`console_cancel_claim` hook carry it (`src/fatty_trader/exchanges/bitget/live.py:223`,
`src/fatty_trader/execution/bitget_dispatch_execution.py:674-677`), and a lost claim against a
different owner is refused or skipped instead of POSTed
(`src/fatty_trader/console/trading.py:857-870`,
`src/fatty_trader/execution/bitget_dispatch_execution.py:689-750`).

**Confirm:** the console reports `cancellation claim is owned by another instruction` rather than
cancelling. Existing coverage:
`tests/unit/test_bitget_dispatch_execution_adapter.py:424` (a stale cancellation reads the
existing claim only and never POSTs).

**Not fixed:** a *proved* terminal readback is still required for every path; nothing here relaxes
that.

### F3 — A pre-POST veto on a split leaked the margin reservation

**Wrong:** when the market leg of a 25/75 split raised before submission, the exception path
returned the outcome without resolving `bitget_margin_reservations`, leaving the row `reserved`
forever. `EXPIRED` (a source retirement) leaked the same way.

**Why it mattered:** leaked reservations consume the per-trade margin ledger and can eventually
refuse new entries — a slow, silent entry-lane failure with no obvious cause.

**Shipped:** every veto path resolves the reservation — `FILLED` when the market leg is live,
`REJECTED` for `EXPIRED`/`REJECTED`, pass-through otherwise, `UNKNOWN` on an unexpected exception
(`src/fatty_trader/execution/bitget_dispatch_execution.py:644-658`).

**Confirm:** no `state='reserved'` rows older than ten minutes:
`q "SELECT count(*) FROM bitget_margin_reservations WHERE state='reserved' AND created_at < now() - interval '10 minutes';"`
must be `0`. The watchdog raises `reservations-stale` as critical otherwise
(`scripts/live_watchdog.py:71-74`, `:298-305`).

**Not fixed:** reservations for *legacy* entries without ownership still need the separate
migration/operator reconciliation (`src/fatty_trader/execution/bitget_dispatch_repository.py:226-229`).

### F4 — Recovery appended same-state dispatch transitions

**Wrong:** `transition()` accepted `expected_state == target_state` and appended a
`dispatch_transitions` edge. Recovery legitimately re-applies the current state on every pass, so
the table grew without carrying information.

**Why it mattered:** two reasons. The audit trail became unusable for reconstruction (edges that
mean nothing), and every recovery pass paid an INSERT.

**Shipped:** an early return when the states match, with the rationale stated in the code
(`src/fatty_trader/execution/bitget_dispatch_repository.py:483-488`).

**Confirm:** `q "SELECT count(*) FROM dispatch_transitions WHERE from_state=to_state AND created_at > now() - interval '10 minutes';"`
should be `0` (the watchdog warns above 5,
`scripts/live_watchdog.py:314-321`).

**Not fixed:** the transition *state machine* itself is unchanged; illegal edges still raise
(`src/fatty_trader/domain/state_machines.py:52-55`).

### F5 — Native TP/SL intents could never be proved, and unchanged intents were rewritten every cycle

**Wrong:** native position TP/SL are plan orders. They do not appear in order detail and they do
not appear in ordinary pending orders, so `reconcile_unknown_intent` classified them `UNKNOWN`
forever — the intent stayed non-terminal and the monitor had no way to converge. Separately, the
monitor wrote each intent back every cycle whether or not the observation changed.

**Why it mattered:** a permanently non-terminal protective intent is indistinguishable from a
missing protection plan during an incident, and it keeps the intent in every non-terminal query
(health's `active_intents`, the console's unresolved list, the watchdog's `intents-unknown`).

**Shipped:** a plan-endpoint branch for `SL`/`TP` that matches by provider order id **or** client
oid and sets `acknowledged` with the plan's provider id
(`src/fatty_trader/exchanges/bitget/reconciliation.py:138-162`); the monitor passes the plan
reader (`src/fatty_trader/execution/bitget_monitor.py:461`); the write is skipped when the
observation is unchanged (`:452-464`), comparing exactly the fields the reconciler can change
(`:705-716`); an unresolved intent now carries `provider-order-unresolved`
(`src/fatty_trader/exchanges/bitget/reconciliation.py:240-241`) which is durable via
`last_error = COALESCE(%s, last_error)` (`src/fatty_trader/storage/live_intents.py:784`,
`src/fatty_trader/storage/reconciliation.py:186`).

**Evidence it worked live:** [LIVE] BRETT's SL and TP intents moved `unknown` → `acknowledged`
during the deploy.

**Not fixed:** the same "write only when changed" guard does **not** cover the dispatcher's
`_persist_readback` (R3).

### F6 — One failed provider read closed the entry lane permanently; the healer released latches it could not observe

**Wrong:** `provider-*-read-failed` and `clock-skew-unavailable` latched the global kill switch on
the first occurrence. The latch then persisted across cycles that were perfectly healthy, because
the only release paths were an operator or a narrow auto-releasable list. A single 19:02 timeout on
2026-10-18 kept the lane closed the next day while the venue measured -28 ms
(`src/fatty_trader/execution/bitget_monitor.py:39-46`). The healer had the mirror-image bug: it
would release a latch whose cause it never checked
(`src/fatty_trader/execution/latch_healer.py:123-125`).

**Why it mattered:** an absorbing safety state is worse than a noisy one — it trains the operator
to clear latches by hand, which is exactly how a genuine latch gets dismissed.

**Shipped:** a transient set and a bucket normalizer
(`src/fatty_trader/execution/bitget_monitor.py:60-70`, `:73-78`); per-cycle strike counting with
clean-cycle reset (`:380-397`); auto-release on the first cycle with no effective reason
(`:404-421`); a healer allowlist of exactly three observable reasons
(`src/fatty_trader/execution/latch_healer.py:21-27`); validated knobs
(`src/fatty_trader/service.py:1447-1461`) plumbed with defaults
(`docker-compose.yml:316-317`).

**Confirm:** `provider-*-read-failed` on one cycle produces no latch; three consecutive cycles
produce one; the next clean cycle releases it with reference
`auto:<reason>-recovered`. Regression: `tests/unit/test_bitget_monitor.py:932`, plus the older
`tests/unit/test_bitget_monitor.py:131`.

**Not fixed / deliberate:** malformed payloads, `unexpected-order`, `unexpected-position` and an
unreleased post-fill mismatch still latch immediately and still require an approved release
(`docs/TRADING-RULES.md:65-74`). Making those transient would be a safety regression.

### F7 — Health reported exposure from a table the live lane never populates; the reconciliation panel was never written

**Wrong:** `open_positions` counted `positions WHERE closed_at IS NULL`, and `pending_orders`
counted `orders`. The Bitget live lane persists neither; its truth lives in `live_order_intents` +
`dispatches`. Separately, nothing wrote `reconciliation_state`, so the console panel had no
`last_run_at` to display and no mismatch history.

**Why it mattered:** during an incident, health said "0 open positions" while a real position was
open — the most dangerous possible false reading, because it invites a second entry or a manual
close.

**Shipped:** exposure and pending counts derived from the durable intent/dispatch join, with
`pending_orders` restricted to owned LIMIT ENTRY legs
(`src/fatty_trader/operator/health.py:87-98`, `:381-398`), and a per-cycle upsert of
`reconciliation_state` (`src/fatty_trader/execution/bitget_monitor.py:320-334`,
`src/fatty_trader/storage/reconciliation.py:304-330`; table
`src/fatty_trader/storage/schema.py:311-318`), consumed by the console
(`src/fatty_trader/console/reads.py:813-816`).

**Confirm:** §4.5 and §4.9.

**Not fixed:** the count's definition is narrow (R6), and the write failure is swallowed to a log
line by design (`src/fatty_trader/execution/bitget_monitor.py:330-334`).

### F8 — A months-old "VERIFIED" capability was accepted as current proof, and admission could promise a fallback lane the process cannot run

**Wrong:** `can_admit_symbol` returned `native-protected` on any `VERIFIED` native state without
checking when it was verified. It also returned `fresh-fallback-stream` from the capability row
alone, even when the admitting process had fallback mutations disabled — a promise the runtime
could not keep.

**Why it mattered:** native placement can be revoked by venue changes; an old proof is not proof.
And an admission based on a lane that will never register means a filled position whose protection
was "guaranteed" by a lane that is not running.

**Shipped:** `NATIVE_VERIFICATION_STALE_SECONDS = 3600`
(`src/fatty_trader/exchanges/bitget/protection_capability.py:14-15`), enforced with distinct
reasons `native-verification-never-observed` / `native-verification-stale`
(`:104-118`), on its own clock, separate from the stream's 5-second horizon
(`:107-113`). Admission now refuses a fallback verdict when
`BITGET_FALLBACK_MUTATIONS_ENABLED != 1` in *this* process, with reason
`fallback-runtime-disabled` (`src/fatty_trader/service.py:224-249`). The knob is validated and
plumbed (`:219-233`, `docker-compose.yml:227`).

**Confirm — and the important caveat:** with the gate at `0` this code never executes
(`src/fatty_trader/service.py:195-199`). The fix is real, compiled, and inert (R2).

### F9 — Worker health required a token even where no token can exist

**Wrong:** the file evidence always carried `owner_token`, and the socket probe always sent it, so
a token-free deployment could never be attested. `identity` is the strictly weaker-but-sufficient
proof on a single host.

**Shipped:** an explicit `WORKER_HEALTH_ATTESTATION` mode, validated on both sides
(`src/fatty_trader/worker_health.py:32-34`), token omitted from the payload in `identity` mode
(`:110-116`), token check skipped server-side in `identity` mode (`:75-90`), probe request built
accordingly (`src/fatty_trader/web/runtime_probe.py:28-43`), mode read and defaulted safely
(`:61-62`).

**Confirm:** `token` remains the default, so nothing changed for the current deployment
[LIVE: default]. Switching to `identity` is opt-in hardening; the process-identity check
(`_process_identity`) is still required in both modes.

**Not fixed:** there is no attestation of the *contents* of the status file, only of the owning
process. That is by design.

### F10 — Migration 38: durable cause plus the indexes the scans actually need

**Shipped:** additive, nullable, no backfill
(`src/fatty_trader/storage/migrations.py:530-546`): `live_order_intents.last_error TEXT`; a
partial index on non-terminal intents (`exchange, role, state`); a `dispatch_transitions
(created_at DESC)` index.

**Why it is safe to leave in place during a rollback:** the column is nullable and read only via
`COALESCE`, and an older image simply ignores it. Dropping it would destroy the only durable
cause for an unclassifiable intent (`docs/AUDIT-ROLLBACK-2026-11-12.md`).

**Confirm:** `q "SELECT max(version) FROM schema_migrations;"` → `38`.

### F11 — Live evidence that the deploy landed correctly

Collected by the parent session, not re-verified here:

- `TP1_BOOKED` on ICPUSDT executed end to end *during* the deploy: half closed at 2.601, the 75%
  LIMIT leg cancelled, stop moved to entry 2.562. This exercises the cancellation-ownership path
  (F2), the split/reservation path (F3) and the management-source path (F1) in one action.
- BRETT SL/TP intents moved `unknown` → `acknowledged` (F5).
- `/health/telemetry` reports ready; both kill switches inactive; all containers healthy.

---

## 6. Remaining audit findings

Same format. These are open; none is a regression introduced tonight.

### O1 — Dispatcher recovery churns the open ENTRY pair every poll

**Finding:** `recover_entry_lifecycles` runs on every dispatcher cycle
(`src/fatty_trader/service.py:1071` calls it from the loop; the console also invokes it at
`src/fatty_trader/console/trading.py:644`) over every candidate
(`src/fatty_trader/execution/bitget_dispatch_execution.py:80`), and
`_persist_readback` unconditionally writes the intent
(`:991`), even when the readback is byte-identical to the durable row.
**Why:** the fix in `587c42c` added the comparison in the monitor
(`src/fatty_trader/execution/bitget_monitor.py:452-464`) and did not extend it to recovery.
**Impact:** 35–60 s of GET work per cycle plus avoidable UPDATEs [LIVE]; provider budget and log
volume, not ledger correctness. The fill-regression guard prevents a GET gap from erasing fills
(`src/fatty_trader/execution/bitget_dispatch_execution.py:973-975`).
**Required change:** compare `_intent_observation`-equivalent fields before `update()`, exactly as
the monitor does. Not a config change.
**Do not:** transition or delete the dispatch to make it stop.

### O2 — Three stale October dispatches remain non-terminal

**Finding:** three dispatches from October are still non-terminal; one is excluded from recovery
only by an audited baseline receipt (`docs/AUDIT-ROLLBACK-2026-11-12.md:82-83`).
**Why:** reconciling them requires provider evidence that no longer exists for at least one row.
**Impact:** recurring recovery candidates and `orphan-entry-intent` inventory noise
(`src/fatty_trader/execution/bitget_dispatch_repository.py:378-424`).
**Required change:** a separate, evidence-backed data operation with an approval reference —
identical in shape to how the existing baseline was created. Nothing in code should be changed to
hide them.
**Do not:** bulk-transition them, or add baseline rows you cannot evidence.

### O3 — `BitgetOperatorGateway.close_position` is test-only and fabricates close truth

**Finding:** returns `state: "closed"` off a flat read and synthesizes `filled_qty` on success
(`src/fatty_trader/operator/bitget_gateway.py:271-274`). No production caller
[SRC: only `tests/e2e/*` and `tests/unit/test_bitget_operator_gateway.py`].
**Why it survived:** it predates the verified-close evidence contract and is convenient in e2e
fixtures.
**Impact:** zero today; a fabricated close if ever wired.
**Required change:** delete it, or route it through `verified_close_lifecycle` so flat readback is
not accepted as fill proof.
**Do not:** reuse it as a "quick close" during an incident.

### O4 — Web-push fan-out depends on the Telegram worker

**Finding:** `fan_out` is only attached to `NotificationWorker`, which exists only when Telegram
settings parse (`src/fatty_trader/notification_service.py:166-175`).
**Impact:** push alerts (including watchdog cards) are silently absent when Telegram config is
missing or invalid, while logs report the push lane as healthy.
**Required change:** construct the fan-out independent of `settings` — the outbox row exists
either way, and the push delivery table does not depend on Telegram.
**Do not:** rely on push as the only paging path.

### O5 — The protection capability gate is disabled (`BITGET_PROTECTION_CAPABILITY_GATE_ENABLED=0`)

**Finding:** `docker-compose.yml:225`; the admission callback returns `None` before touching any
capability row (`src/fatty_trader/service.py:195-199`).
**Why:** the capability table has to be populated and fresh per symbol before enabling; enabling it
cold would close the entry lane.
**Impact:** F8's checks are inert; a stale capability cannot block an entry, and a disabled
fallback lane cannot be caught at admission.
**Required change:** a deliberate rollout: populate/age out `bitget_protection_capabilities`, then
enable, with a watcher on the dispatcher log.
**Do not:** flip it during a live position you are not watching.

### O6 — No regression test references the two new operational scripts

**Finding:** `scripts/live_watchdog.py` and `scripts/watchdog_probe.py` have no entry in
`tests/`; the compose-`ps` JSON parsing remains the fragile part
(`scripts/live_watchdog.py:134-171`). The parser was fixed in `db0fc04` and is verified live, but
only by observation.
**Impact:** false criticals at 03:00, or a silent failure to parse and therefore a missed real
critical (`compose-ps-unparseable` is reported critical, which at least fails loud).
**Required change:** a unit test with a fixed compose-v2 NDJSON fixture, and commit the parser fix.
**Do not:** tune thresholds to hide a parser bug.

### O7 — The heartbeat-and-health payload does not expose kill-switch state

**Finding:** `/health/telemetry` reports configuration, liveness and readiness only
(`src/fatty_trader/web/health.py:65-93`); kill switches are in the database
(`venue_kill_switches`, `src/fatty_trader/storage/migrations.py:104-112`) and reachable through
the operator card or SQL. `orders_enabled` is `None` for the web process on purpose
(`src/fatty_trader/web/health.py:79`).
**Impact:** a lane-closing latch is invisible to anything that only polls health — which is why
the watchdog queries the ledger directly (`scripts/live_watchdog.py:53-95`).
**Required change:** none required for trading; know the limitation.
**Do not:** use health `status=ok` as proof the entry lane is open.

---

## 7. Operational boundaries

### 7.1 The watchdog's contract

`scripts/live_watchdog.py` (host, every 60 s by default; `WATCHDOG_INTERVAL_SECONDS`, min 15 —
`scripts/live_watchdog.py:36`).

**It MAY:**

- run `docker compose ps` and read the resulting service state/health
  (`scripts/live_watchdog.py:174-220`);
- run `docker compose exec -T postgres psql -U fatty_app -d fatty_trader -At -c <SELECT>`
  (`:114-132`) with the read-only ledger query at `:53-95`;
- run `docker compose exec -T monitor-bitget ... scripts/watchdog_probe.py`
  (`:343-352`), which performs venue GETs only — clock skew, all positions, plan orders per
  symbol, pending orders (`scripts/watchdog_probe.py:44-96`);
- GET the local health endpoint (`scripts/live_watchdog.py:221-253`);
- write its own state to `~/.local/state/fatty-watchdog/` (`status.json`, append-only
  `events.jsonl`; `:30-35`, `:464-487`);
- enqueue **one** `system-event` notification card per state change, deduplicated, and a reminder
  every 6 h while not ok (`:430-463`, `:537-540`).

**It MUST NOT, and cannot:**

- place, cancel or close an order, move a stop, repair a plan, or register fallback protection —
  there is no provider mutation method in either script
  (`scripts/live_watchdog.py:11-13`; the probe calls only `get_clock_skew_ms`,
  `get_all_positions`, `get_pending_plan_orders`, `get_pending_orders`,
  `scripts/watchdog_probe.py:44-96`);
- release, clear or set a kill switch, or write any `venue_kill_switches` row — its only SQL
  mutation is the outbox INSERT (`scripts/live_watchdog.py:454-459`);
- change configuration, restart a container, or touch the database schema;
- mark a notification sent, or send anything itself; `notification-sender` delivers.

**Therefore:** a watchdog page is a *prompt to run §4*, never evidence that a remediation
happened. If a card says `position-unprotected:ICPUSDT`, the correct next action is §4.3 — not a
plan repair, and not closing the position.

### 7.2 Running and watching it

systemd is the installed mode: `fatty-watchdog.service` is a **user** unit
(`~/.config/systemd/user/`), enabled, with lingering on so it survives logout.

```bash
# installed, enabled, active?
systemctl --user status fatty-watchdog.service
systemctl --user is-enabled fatty-watchdog.service     # enabled
loginctl show-user "$USER" -p Linger                  # Linger=yes

# apply an edited script or a changed environment
systemctl --user daemon-reload
systemctl --user restart fatty-watchdog.service

# read it
journalctl --user -u fatty-watchdog.service -n 50 --no-pager
journalctl --user -u fatty-watchdog.service -f        # follow live

# inspect the last observation without waiting for a card
python3 -m json.tool ~/.local/state/fatty-watchdog/status.json | head -60
tail -n 5 ~/.local/state/fatty-watchdog/events.jsonl
```

Stopping it (`systemctl --user stop fatty-watchdog.service`) stops the observation only; nothing
else depends on it. Keep it in the user scope — it needs this user's `docker` access and `git`
checkout, so a system-scope unit would observe the wrong host state.

Environment it honours: `WATCHDOG_INTERVAL_SECONDS`, `FATTY_WATCHDOG_ALERTS` (`0` = observe only,
no outbox write), `FATTY_WATCHDOG_HEALTH_URL`, `FATTY_WATCHDOG_DOCKER`,
`FATTY_WATCHDOG_STATE_DIR` (`scripts/live_watchdog.py:30-39`). Paging policy: a critical pages
immediately; a warning pages only after three consecutive cycles, and the first observation never
pages an `ok` card (`scripts/live_watchdog.py:522-533`, `:539-540`).

Reason codes it emits and their severity: `container-missing|container-<state>|container-<health>`
(critical), `compose-ps-failed|compose-ps-unparseable` (critical), `health-unreachable` /
`health-not-ready` (critical), `health-status:<s>` (warning), `ledger-unreachable` (critical),
`kill-switch-active:<scope>` (critical), `reconciliation-never-ran` / `reconciliation-stale`
(critical, >180 s), `intents-unknown` (critical), `intent-error:<oid>` (warning),
`reservations-stale` (critical), `management-failed:<action>` (warning), `transition-churn`
(warning, >5 in 10 min), `outbox-backlog` (warning, >100), `outbox-failed` (warning, any in 24 h),
`venue-probe-failed|venue-probe-unparseable|venue-probe-invalid` (warning), `venue-read:*`
(warning), `clock-skew` (warning, |skew| > 5000 ms), `position-unprotected:<symbol>` (critical),
`position-no-target:<symbol>` (warning), `unexpected-order:*` (warning),
`watchdog-observation-failed` (critical) — `scripts/live_watchdog.py:174-426`, `:513-521`.

### 7.3 Boundaries for a human at 03:00

- Read-only first. Every command in §4 is a SELECT or a GET.
- Never `UPDATE venue_kill_switches`, `live_order_intents`, `source_management_updates`,
  `bitget_margin_reservations` or `dispatches` by hand. Each has an audited path that records who
  and why.
- Never cancel or replace a protective order manually while the bot is running; the bot's own
  repair only places missing plans (`docs/TRADING-RULES.md:111`), and a manual replacement races
  it.
- Never let an old database backup be restored after provider mutations
  (`docs/OPERATIONS.md:220`).

---

## 8. Rollback summary

Full procedure, per-commit table and rollout ordering: [`docs/AUDIT-ROLLBACK-2026-11-12.md`](AUDIT-ROLLBACK-2026-11-12.md).
Short form:

| Scope | Action |
|---|---|
| One fix is suspect | `git revert <commit>` for `33a9c1d`, `530872a`, `587c42c`, `da59b71`, `8629de6`, `0a1692d` (reverse order if several) |
| Whole deploy | `git checkout pre-audit-2026-11-12 && docker compose up -d --build` → returns to `d9075a5` |
| Behaviour only, no rebuild | set the knobs in `.env` (`BITGET_MANAGEMENT_MAX_SOURCE_AGE_SECONDS=300`, `BITGET_LATCH_TRANSIENT_STRIKES=1`, `BITGET_LATCH_TRANSIENT_AUTORELEASE=0`) and `docker compose up -d` |
| Database | **no rollback needed** — migration 38 is additive and nullable (`src/fatty_trader/storage/migrations.py:530-546`). Never drop it to "clean up" |
| Ops scripts | `scripts/` commits (`6704b77`, `5eb6f6c`, `61e918a`) add host tooling only and no container code path calls them; reverting them only removes the watchdog on the next build |

Rolling back the image while a position is open is safe for the position but reverts F2/F3/F5/F6 —
i.e. cancellation ownership, reservation release, native-plan reconciliation and the transient
latch tier. Prefer a targeted revert.

---

## 9. State-machine glossary (implemented states only)

### 9.1 Dispatch states — `src/fatty_trader/domain/enums.py:19-38`

`QUEUED → PREFLIGHT → SIZED → VALIDATED → SUBMITTING → ACKNOWLEDGED →
{PENDING_FILL, PARTIALLY_FILLED, FILLED} → {PROTECTING, CLOSING} → CLOSED`, with
`EXPIRED`, `REJECTED`, `RETRY_WAIT`, `UNKNOWN`, `MANUAL_REVIEW`, `FAILED` as exits
(legal edges: `src/fatty_trader/domain/state_machines.py:5-49`; illegal edges raise
`TransitionError`, `:52-55`).

Which are produced in practice [SRC]: `PROTECTING` and `ACTIVE` are declared but never assigned by
any runtime path; a filled bot trade therefore sits at `FILLED` (or `PARTIALLY_FILLED`) until it
closes, and `CLOSING`/`CLOSED` are written by recovery
(`src/fatty_trader/execution/bitget_dispatch_execution.py:138-155`). `EXPIRED` means the source
message aged out of the entry window; `REJECTED` means a gate refused it.

### 9.2 Intent states — `src/fatty_trader/storage/schema.py:181-185`

`staged, requested, acknowledged, submitted, partially_filled, filled, cancelled, rejected,
unknown, reconciled`.
Terminal set used everywhere for "still needs attention":
`NOT IN ('filled','rejected','cancelled','reconciled')`
(e.g. `src/fatty_trader/operator/health.py:389-391`,
`src/fatty_trader/storage/migrations.py:538-541`).
Roles: `ENTRY, SL, TP, CLOSE, EMERGENCY_CLOSE` (`src/fatty_trader/storage/schema.py:178-180`).
`unknown` + `last_error='provider-order-unresolved'` = a real divergence (§4.6).

### 9.3 Management update states — `src/fatty_trader/storage/schema.py:280-283`

`queued, claimed, reconciliation-pending, failed, reconciled, cancelled-flat, entries-cancelled`.
Honest terminal outcomes, per `docs/TRADING-RULES.md`:

- `cancelled-flat` — cancellation proved and a fresh read found no position; no close is claimed.
- `entries-cancelled` — entries proved cancelled while a position remains; no fill claimed.
- `reconciled` — the requested mutation completed its provider readback contract.
- `reconciliation-pending` — needs durable reconciliation; never success.
- `failed` — dead-lettered with `last_error` (§4.4). Migration 35 requires a `deadline_at` for
  every non-terminal row (`src/fatty_trader/storage/migrations.py:484-502`).

Actions: `TP1_BOOKED, TP_BOOKED, SL_TO_ENTRY, CLOSE` (`src/fatty_trader/storage/schema.py:279`);
later migrations keep that check in sync when a new action is added
(`src/fatty_trader/storage/migrations.py:382-387`).

### 9.4 Margin reservations — `src/fatty_trader/storage/schema.py:245`

`reserved, consumed, released, unknown`. A `reserved` row older than ten minutes is a leak (see
F3; the watchdog reports it as `reservations-stale`).

### 9.5 Protection capability — `src/fatty_trader/storage/migrations.py:213-234`

- `native_state`: `UNKNOWN, VERIFIED, UNSUPPORTED, FAILED`.
  `VERIFIED` is only admitted when `last_verified_at` is inside
  `BITGET_PROTECTION_VERIFICATION_STALE_SECONDS` (`src/fatty_trader/exchanges/bitget/protection_capability.py:104-118`).
- `stream_state`: `DISABLED, CONNECTING, HEALTHY, STALE, FAILED` (stream liveness, a 5-second
  horizon — deliberately not the verification clock).
- `fallback_allowed` + the admitting process's `BITGET_FALLBACK_MUTATIONS_ENABLED` must both hold
  before a fallback verdict is returned (`src/fatty_trader/service.py:243-249`).

### 9.6 Kill-switch reasons

- Transient (3 strikes, auto-release): `clock-skew-unavailable`, `provider-fills-read-failed`,
  `provider-orders-read-failed`, `provider-positions-read-failed`,
  `verified-close-evidence-unavailable`, and any `unknown-intent-unreconciled:*`
  (`src/fatty_trader/execution/bitget_monitor.py:60-70`, `:73-78`).
- Auto-releasable on a clean cycle (pre-existing): `clock-skew-unavailable`,
  `clock-skew-exceeded`, `post-fill-margin-or-leverage-mismatch` (`:47-52`).
- Durable, operator-only: everything else — notably `unexpected-order`, `unexpected-position`, and
  an unreleased post-fill mismatch.
- Healer-releasable, and only these three: `socket-not-connected`,
  `provider-position-read-failed`, `provider-position-invalid`
  (`src/fatty_trader/execution/latch_healer.py:21-27`).

### 9.7 Health vocabulary — `src/fatty_trader/web/health.py:65-93`

- `readiness.status`: `ready` only when `postgres`, `intake`, `analyzer`, `notification` each
  report a ready state **and** a progress age within 120 s (`:12-18`, `:58-65`).
- `status`: `ok` only when configuration and readiness are both good; `degraded` otherwise.
- `live_execution_enabled`: `LIVE`/`LIVE` plus `BITGET_EXECUTION_ENABLED=1` (`:66-72`).
- `orders_enabled` is `None` for the web process by design (`:79`) — web observes
  configuration, not dispatcher permission or kill switches (§O7).

---

## 10. Verifying the deployed build is still `9481197`

```bash
cd /home/valarion/apps/fatty-multi-exchange-trader

# 1. Revision identity. Host HEAD is 5eba213; the audited trading revision is 9481197.
git rev-parse --short HEAD                          # 5eba213
git rev-parse --short 9481197                       # the audited trading revision
git rev-parse --short pre-audit-2026-11-12^{commit} # d9075a5  (rollback target)
git status --porcelain                              # empty = no local drift

# 2. Prove only ops scripts differ from the audited trading revision.
git merge-base --is-ancestor 9481197 HEAD && echo "9481197 is an ancestor of HEAD"
git log --oneline 9481197..HEAD -- src docker-compose.yml Dockerfile
#    Expect NO output. Output means container behaviour differs from this audit.
git diff --stat 9481197..HEAD -- . ':(exclude)scripts'
#    Expect NO output. Output means something outside scripts/ changed.

# 3. Schema is at the audited migration.
q() { docker compose exec -T postgres psql -U fatty_app -d fatty_trader -At -c "$1"; }
q "SELECT max(version) FROM schema_migrations;"     # 38

# 4. Behavioural markers actually inside the running image.
docker compose exec -T dispatcher-bitget sh -lc \
  'grep -c "fallback-runtime-disabled" src/fatty_trader/service.py'          # 1
docker compose exec -T dispatcher-bitget sh -lc \
  'grep -c "last_error" src/fatty_trader/storage/live_intents.py'            # > 0
docker compose exec -T monitor-bitget sh -lc \
  'grep -c "transient_strikes" src/fatty_trader/execution/bitget_monitor.py' # > 0

# 5. Runtime lane health.
curl -s http://127.0.0.1:18081/health/telemetry \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["status"], d["readiness"]["status"], d["live_execution_enabled"])'
# ok ready True

# 6. Effective knobs inside the container (environment, not source defaults).
docker compose exec -T source-management sh -lc \
  'printenv BITGET_MANAGEMENT_MAX_SOURCE_AGE_SECONDS'    # 1800
docker compose exec -T monitor-bitget sh -lc \
  'printenv BITGET_LATCH_TRANSIENT_STRIKES BITGET_LATCH_TRANSIENT_AUTORELEASE'  # 3 and 1
docker compose exec -T dispatcher-bitget sh -lc \
  'printenv BITGET_PROTECTION_VERIFICATION_STALE_SECONDS BITGET_PROTECTION_CAPABILITY_GATE_ENABLED'  # 3600 and 0
```

Any mismatch in steps 2–6 is a finding, not a nuisance: either the host is not running this
revision, or someone changed behaviour without recording it. Re-read §2 before deciding which.
