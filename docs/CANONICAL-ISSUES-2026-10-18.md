# Canonical issues, findings, owner concerns, and the next-deployment contract

Compiled 2026-10-18 (session `01a14ef3-320f-7394-8558-a4d0c9f2c7e3`). This is the
register of record: every defect class, every incident with its evidence, everything the
owner raised, and the acceptance contract for the next deployment. The working plan is
`docs/MASTER-PLAN.md`; live state and runbooks are `docs/HANDOVER-2026-10-18.md`.

Status vocabulary: **FIXED** (deployed and verified in production) · **PARTIAL** (one
instance closed, class still open) · **OPEN** (plan item exists, not built) · **WATCH**
(fixed but unproven over time).

---

## Part 1 — Canonical defect classes

### C1 · Record that cannot prove itself terminal
A durable row records "I may have already acted at the venue" (a fence: reservation,
provider intent, close claim, plan). The system correctly refuses to re-act, but has no
terminal transition for the case where nothing happened or the effect is moot — so the
row parks forever, and readiness is often coupled to it.

| Instance | Evidence | Status |
|---|---|---|
| Two source-management updates re-claimed every 30 s for ~2.5 weeks | `reconciliation-pending`, no exit | FIXED (`671ddc2` retire-on-proof) |
| Orphan reservation blocked the live entry lane ~1 h (10:30→11:47) | `recovery-blocked: reservation-without-entry-intent` | FIXED (`e1915bf` auto-retire; baseline `…20261017` cleared the instance) |
| WLD lifecycle blocked the lane 09:16→12:0x | `owned-flat-close-fill-unproven` | PARTIAL (auto-settle of stranded protection `97ddaa0`; closed-lifecycle terminality = plan P0-6) |
| NEAR lifecycle blocked the lane 12:54→12:58 | same reason + `unresolved-protection` | PARTIAL (baseline `…20261018b` cleared the instance) |
| Stranded SL/TP intents made even the approved baseline refuse | non-entry intent not terminal | FIXED (`97ddaa0` auto-settle with evidence gates) |
| Attempt/deadline budget for parked source rows | none existed | FIXED (migration 35, `park()`, dead-letter alert) |

### C2 · Timing between "posted" and "proven"  ← **the class that cost two trades**
Protection is POSTed, verification reads back too early, containment fires, and the trade
becomes a wash with an unprovable lifecycle.

| Instance | Evidence | Status |
|---|---|---|
| WLD 09:16: filled 11, closed within 3 s | fills 09:16:17→09:16:20; emergency close filled 11 | OPEN (plan P0-2/P0-3) |
| NEAR 12:54: filled 4 @4.9165, closed 3 s later @4.9185 | realized −0.008 + 0.024 fees | OPEN (plan P0-2/P0-3) |
| Root path | `async_execution.py:843` containment; intent at `:864` | — |

**Owner decision for this class:** soft stop primary (websocket + reduce-only close at the
signal's level), wide venue backstop inside liquidation, both shipping in one release; no
observe-only stage. Reversal conditions are published in the plan.

### C3 · Silent failure
A bad state exists, nothing alerts, only logs. The operator learns hours later or by
asking.

| Instance | Evidence | Status |
|---|---|---|
| 2.5 h lane block, no alert | dispatcher logged `recovery-blocked` only | FIXED (`97ddaa0` one alert per issue set) |
| Rollouts finished with no signal to the owner | hanging ssh wrappers, no notification | FIXED (`0d75e4b` rollout start/blocked/complete/failed announcements) |
| Alerts rendered `Dispatch: (empty)` | outbox payload without identity | FIXED (`97ddaa0` identity rendering) |
| Signals can lapse with no record | window expiry silent | FIXED (maintenance windows + `missed_signals`, 0 missed) |
| Wider: stalled leases, canary exhaustion, stale registrations, delivery failures | not covered | OPEN (plan P1-1 sweep) |
| No automatic answer to "did we lose a signal today?" | manual queries decided every incident | OPEN (plan P1-2 nightly forensics) |

### C4 · Shared-table contention
An unqualified `FOR UPDATE` inside a join locks rows from **every** joined table, so
another worker's row locks starve the claim.

| Instance | Evidence | Status |
|---|---|---|
| NEAR dispatch sat `QUEUED` 28 min after re-admission | measured 0/6 matches as written, 6/6 with `FOR UPDATE OF d` | FIXED (`fcf7e92` + regression test) |

### C5 · Gates narrower than reality
Assumptions like "tickers are ≥2 characters" silently refused valid input.

| Instance | Evidence | Status |
|---|---|---|
| `#Q` signal unparseable | pair-token gates required ≥2 chars | FIXED (`34e23db`, `b039b3b`) |
| Vision timeouts made media signals depend on the text fallback | 22 s call vs 30 s cap | WATCH (fallback works; timeout margin thin) |

### C6 · Schema/model divergence
`dispatches.state` has no CHECK constraint; the database has held `RECONCILED`, a state the
Python enum does not define; terminal-set logic is duplicated as string lists. Status:
**OPEN** (kernel/constraints item).

### C7 · Deploy process risk
Stop-the-world rollouts can straddle a signal; wrappers hang; no rollback has ever been
rehearsed.

| Instance | Evidence | Status |
|---|---|---|
| Signals can lapse inside rollout windows | window accounting now records them (5 windows, 0 missed) | PARTIAL (gate + accounting done; rollback drill = P2-2) |
| The first rollout surfaced the orphan reservation block | deploy at 11:47 → `recovery-blocked` | FIXED for the class (auto-retire) |
| Deploy gate refused its own author mid-trade | `blocked_by: in_flight_dispatches` | FIXED (working as designed) |

### C8 · Protection-path friction
Venue stops must be placed, verified, and later cleaned; each step can fail.

| Instance | Evidence | Status |
|---|---|---|
| Stale fallback registrations kept a stream alive for a dead symbol | PUMPUSDT/PENGUUSDT `active` since September | FIXED (retired by hand on proof; auto-settle now covers the class) |
| Rejection path (`43011`) escalates without ordering/timing coordination | code comment + capability rows | OPEN (plan P0-4/P0-5) |
| Leftover plans after a close | observed, hand-cleaned | Addressed by the stop policy (local primary) |

---

## Part 2 — Owner concerns, as raised, with the answer

| Concern (as raised) | Answer / status |
|---|---|
| "make sure no more failure" | Watchdog + blocked-lane alerts + attempt budgets + no-silence rules (C3); invariant tests per fix |
| "why it still exists? why it keep failing" | C1: fences without terminal paths; the fix pattern is proof-based retirement, now applied to four lanes |
| "does all the development causing my live bitget malfunctioning?" | One rollout **surfaced** a latent orphan (it would have blocked any restart); the account never carried unprotected exposure; deploy risk now gated and accounted |
| "how to prevent it happening again? redis? TTL?" | Documented: **no Redis as state** (Postgres owns money state); **TTL = deadline + proof, never deletion**; the kernel implements exactly that |
| "why stream PUMPUSDT with no position?" | Stale September registrations; retired, and the class now auto-settles |
| "fix this health check" | Fixed (`a0f87f0`): `Lifecycle READY`, honest stored-evidence labels |
| "how the hell you stopped in silent?" | Operator-loop silence; rollouts now announce everything (`0d75e4b`) |
| "make autonomous execution more ease" | Late-signal re-admission through the bot's fences (`7f8493c`), claim starvation fixed (`fcf7e92`), console actions planned (P1-3) |
| "no need observe only … over 2 month without a real fixed" | Corpus-first evidence (P0-0); both stops ship in one release; "fixed" now means invariant test + corpus behaviour + nightly proof |
| "next deployment still condition live trade and enable trade execution but better (no other bugs)" | Part 4 contract below |

---

## Part 3 — Two months, four recurrences (why rounds failed)

The harness holds 101 sessions over exactly two months; the database holds 489 messages,
67 signals, 24 fills since 2026-09-04. Each round ended when the lane looked idle, on that
day's incident, with a local patch. The same four classes (C1–C4) returned under new
names. The correction: a class closes only when its invariant test exists, the corpus
shows it behaving, and the nightly report can prove it stayed closed.

---

## Part 4 — Next deployment contract (binding)

**Must keep (all live-trade conditions remain in force):** the LIVE/LIVE/execution-enabled
policy; recovery readiness; the 1.1 USDT planned-margin cap; leverage ceiling 20x (10x
where a stop would otherwise sit beyond liquidation); one position at a time; the canary
order cap; contract/step/min-notional validation; kill switches and latches; the deploy
gate; approval references for any operator action.

**Must enable (execution completes):** protection confirmed with an evidence-based,
bounded grace; the local soft stop primary with a wide venue backstop; no trade closed by
our own timing; a late signal re-admitted by the operator executes end to end.

**"No other bugs" — acceptance evidence required before promotion:**

1. unit + e2e on Linux, ruff, mypy strict — green;
2. WLD and NEAR corpus fixtures hold the position with protection verified;
3. a synthetic never-confirms case still contains and alerts with the attempt trail;
4. rehearsals: kill the process, drop the socket, replay a fast candle — each ends
   protected by the backstop;
5. 24 h soak: zero wash trades, zero lane blocks unreported for >2 minutes, zero signals
   lapsed without a `missed_signals` record;
6. the nightly forensic report explains every signal of that day;
7. rollback command documented and rehearsed; migrations additive only.

**Explicitly not in this deployment** (kept small on purpose): microstructure, position
intelligence beyond protection, portfolio drawdown logic, any sizing change, any new
exchange.

---

## Part 5 — Final plan (ordering)

The complete plan is `docs/MASTER-PLAN.md`; its order is binding:

- **P0-0** corpus + two-month ledger (the evidence engine)
- **P0-1** measure protection latency and stream freshness · **P0-2** escalation ladder
  with evidence-based grace · **P0-3** soft stop primary + wide venue backstop (one
  release) · **P0-4** pre-entry protection feasibility gate · **P0-5** attached backstop ·
  **P0-6** closed-lifecycle terminality
- **P1-1** watchdog sweep · **P1-2** nightly forensic accounting · **P1-3** operator
  surface without shell
- **P2-1** replay harness on the corpus · **P2-2** release cadence + rehearsed rollback
- **P3** edge: position intelligence → execution quality → portfolio risk → scoreboard →
  adaptive sizing only at n ≥ 100

Struck: local-only stops, Redis as state, ML prediction, a second exchange, sizing before
n ≥ 100.

---

## New findings — 2026-10-19 (measurement pass)

| id | finding | evidence | status |
| --- | --- | --- | --- |
| F-1 | Recovery re-transitions unresolved lifecycles on every tick with an unchanged `(to_state, reason)`: four dispatches carry **5 285** rows each (21 410 total). Pure audit noise and unbounded growth; it also buries real transitions in any timeline query. | `dispatch_transitions` counts per dispatch; collapsed timelines in `docs/PROTECTION-LATENCY-2026-10-18.md` | **open** — P0-1b: skip a no-op transition and store a repeat counter |
| F-2 | The rollout aborted at its own runtime verifier because `migrate` runs under `docker compose run --rm` and leaves no container record, while the verifier demanded one. | first rollout of `4d59465`; `service.py:1746` shows `init` is a no-op | **fixed** `35667c9` (verifier accepts a missing one-shot when the effect is proven by `schema_migrations`) |
| F-3 | A rollout that aborts after step 1 leaves `fatty-console-ops-worker` stopped, silently removing every allowlisted host operation from the console. | worker `inactive`/`ExecMainStatus=15` after the aborted rollout | **fixed** `35667c9` (`restore_ops_worker()` on every exit path; step 16 fails loudly if it is not active) |
| F-4 | Per-attempt protection readbacks are not durable: the grace writes attempts only to the capability error trail (`\|grace:N`) and a log line, so the 15 s budget cannot be tuned from data. | `async_execution.py:865-956`; `last_verified_at` is per-symbol local time | **open** — instrumentation slice: a per-attempt row (symbol, root, attempt, observed_at, outcome) |
| F-5 | `build_bitget_protection_stream` passed `stale_after` to the socket but not to the runtime, so runtime freshness silently used its 5 s default. | `service.py:299-304` before `116e542` | **fixed** `116e542` (one parse reaches socket, runtime and engine) |
| F-6 | Flat closes without a recorded close fill (PUMP class) remain operator decisions by design: an unaccounted close must stay visible rather than be inferred away. | `tests/e2e/test_service_owned_protection_recovery_db.py::test_real_service_inventory_verdict[flat]` | **by design** — still requires a baseline until the close fill is found |
| F-7 | The stream close engine existed and was tested but no service code constructed it; the builder raised when mutations were requested. The local stop was structurally unreachable. | `bitget_protection_stream.py:44-127` with no caller; `service.py:250-255` | **fixed** `116e542` (flag-gated wiring + integration test through `submit_fallback_close_async`) |
| F-8 | `scripts/rollout.py` step 16 called `out(..., check=False)`; the repository helper takes no such parameter (the legacy host copy does), so a *successful* release announced `rollout failed · TypeError`. The deployment itself completed and was verified after the fact. | outbox row `rollout:failed:…error=TypeError:out() got an unexpected keyword argument 'check'` | **fixed** 2026-10-19 (handler quotes `systemctl is-active` stdout; rollout still fails loudly if the worker is not active) |
| F-9 | Exit policy (owner decision 2026-10-19): a position whose exchange-side SL/TP cannot be confirmed is **never closed by us**. It is marked locally managed and the local monitors close it at the signal stop; with the local lane down, the system alerts with full position detail and still does not close. Emergency containment is now opt-in and off. | `_contain_or_local_management` + `-locally-managed` verdicts + `protection-alert` outbox rows; switch table in `docs/NEXT-STEPS-2026-10-18.md` | **landed** 2026-10-19 (`e5d1887`) |
| F-10 | A transient `clock-skew-unavailable` (one unreadable venue clock) latched the entry kill switch permanently: latched 2026-10-18 19:02, still active the next day while the venue read **-28 ms**. An absorbing state that silently blocks every entry after a network blip, and it also failed the runtime verifier. | `venue_kill_switches` row + `runtime_blocked=kill_switch_active` from the rollout verifier | **fixed** 2026-10-19 (transient clock reasons auto-release once a clean cycle proves the condition is over — `auto:clock-check-recovered`; durable reasons such as `provider-fills-invalid` are never auto-released) |
