# Master plan — execution integrity first, cleverness second

Status: **plan line** (`master-plan`). Live already runs this line, behind the gated
rollout. Session that produced this revision: `01a14ef3-320f-7394-8558-a4d0c9f2c7e3`.
Live state and runbooks: `docs/HANDOVER-2026-10-18.md`.

Added 2026-10-18 by owner decision: the **stop policy** below changes how positions are
protected. Everything else is re-ordered so the losses stop before the cleverness starts.

## Stop policy — soft stop primary, hard stop backstop (owner decision, 2026-10-18)

**Decision.** The bot's stop at the *signal's* level is managed locally: the websocket
feed watches the mark price and the bot sends a reduce-only market close when the level
is crossed. The venue holds only a **wider backstop stop**, clearly inside the
liquidation distance (e.g. −4% to −4.5% when the signal stop is −2.4%), so an outage, a
socket loss, or a gap can never leave the position uncovered.

**Why.** Both losses on 2026-10-18 (WLD, NEAR) came from the venue-stop path, not from
the market: the exchange accepted the stop, the bot failed to *verify* it in time,
containment fired ~3 s after entry, and the trade became a wash with an unprovable
lifecycle. A locally managed stop has nothing to place, nothing to verify, nothing to
reject, and nothing left behind after a close. It also makes break-even and trailing
stops free and instant instead of another venue call that can fail.

**Why not local-only.** The exchange is 24/7; this stack is not — it stops the world for
rollouts, has been recovery-blocked for hours, and has lost its websocket before. A
local-only stop turns *our* outage into an uncovered position at 20x, and a gap into a
liquidation. The backstop exists precisely for the case we cannot handle.

**Evidence comes from replay and rehearsals, not from a waiting period** (owner
decision: no observe-only rollout). The corpus is built from the source channel's own
history plus venue candles, so thousands of signals can be judged offline in hours
instead of waiting weeks for live samples. The five items are sourced as follows:

1. stream freshness and disconnect handling — historical capability rows and monitor
   evidence for the shape of the failure, plus an explicit socket-drop rehearsal;
2. tick → close → fill latency — replay for the expected distribution, a live probe in
   the first week as confirmation (recorded, not gating);
3. gap behaviour — replay across the whole corpus: local close versus venue trigger on
   the same candles, reported in basis points;
4. partial-fill correctness — unit and e2e tests plus corpus cases with partial fills;
5. rehearsals — kill the process, drop the socket, replay a fast candle with a live
   position; each must end protected by the backstop.

**Both stops ship in the same release.** The local stop becomes primary only together
with the wide venue backstop, the bound close intent, the containment ladder and the
alerts — never alone, never in two stages.

**Reversal conditions.** If rehearsals show disconnects longer than the agreed budget,
or gap slippage worse than the agreed bound, the hard stop returns to the signal level
and the local stop returns to observe-only. This is a policy with a spine, not a mood.

Every policy change from here on carries the same shape: a decision record, an evidence
bar, and published reversal conditions.

## Why two months of fixes did not fix it

The harness on this machine holds **101 agent sessions spanning exactly two months**
(2026-08-18 → today) and the database holds **489 channel messages, 67 signals, 24
fills** since 2026-09-04. Both are evidence that the work happened; neither was ever
turned into a proof that a *class* of defect was gone. Each round ended when the lane
looked idle, on the day's incident, with a local patch — and the same classes came back
under new names:

| Class | First seen | Recurrence |
|---|---|---|
| Venue-side record that cannot prove itself terminal | September (source updates) | reservation orphan, WLD, NEAR |
| Timing between "posted" and "proven" | September (close intents) | WLD and NEAR protection verification |
| Silent failure (log-only, no alert) | throughout | 2.5 h block, hanging deploys, `(empty)` alerts |
| Shared-table contention | October | claim starvation (NEAR sat 28 min) |

**The correction in this plan** is that "fixed" becomes falsifiable: a defect class is
closed only when (a) the invariant test that would have caught it exists, (b) the replay
corpus shows the class behaving, and (c) the nightly forensic report can prove it stayed
closed. The corpus and the two-month ledger are built first (P0-0), because they are the
instrument that makes every later claim checkable.

## The objective, stated honestly

The bot does not have an edge problem yet; it has an **integrity problem**: it must
convert a signal into a *held, protected* position, or refuse it with a recorded,
alerted reason — and the operator must see which, and why, without a shell.

## What the day proved (evidence for this revision)

| Evidence | Consequence for the plan |
|---|---|
| WLD 09:16 and NEAR 12:54: entry filled → plans accepted → verification too slow → containment within ~3 s → lifecycle unprovable → lane blocked for hours | Protection timing is a first-class design surface; and it is why the stop policy above exists |
| Both blocks needed an **approved baseline** to clear | A settled trade must settle itself; baselines are for history, not yesterday |
| NEAR's re-admitted dispatch sat `QUEUED` 28 min: unqualified `FOR UPDATE` locked every joined table and the analyzer starved it (0/6 → 6/6 measured) | Contention on shared tables is a live failure mode; probes beat theory |
| A 2.5 h block produced only log lines; the operator learned hours later | Observability is P0 |
| Rollouts finished while their ssh wrappers hung and nothing announced | The operator loop needs the same no-silence rule as the bot |
| Manual forensics decided every incident | Forensics must be scheduled and delivered |
| ~19 fills total | Sizing/adaptive work is struck until a real sample exists |

## Ground rules (non-negotiable)

1. **Isolation.** Plan work happens on `master-plan` (`~/apps/fatty-plan`). Tests use the
   disposable Postgres; `scripts/rollout.py` is the only path to live.
2. **Measure before tuning.** No timeout, retry budget or threshold lands without the
   measurement that justifies it, recorded in the commit.
3. **Additive migrations (expand/contract).** Never drop or rename what the previous
   release still reads; contraction ships a release later.
4. **Switchable revisions.** `--branch <line>` or `--revision <sha>`, same gate, same
   verification, same window accounting on both lines.
5. **Gate before stopping anything.** A rollout refuses a busy lane.
6. **Nothing silent.** Lapsed signals recorded and announced; blocked lanes alert once
   per issue set; rollouts announce start/refusal/completion/failure; alerts carry real
   identities.
7. **Proof per slice.** Unit + e2e on Linux, ruff, mypy strict, plus the slice's own
   acceptance proof (probe, replay, or production observation).
8. **Never traded away:** never re-post an ambiguous order; never remove a fence in
   favour of retry logic.

## Phase 0 — execution integrity

**P0-0 · Corpus and the two-month ledger (do this first).** *(assumption: there is not
enough live data to decide anything)* Build the evidence engine:
(a) backfill the source channel's own history through the existing intake (years of
messages, not just the 489 in the database), so signals and price candles exist for
every historical entry;
(b) fetch venue candles around each signal and store the corpus;
(c) mine the 101 harness sessions (hermes, omp, opencode history) into
`docs/FORENSICS-2-MONTHS.md`: every attempted fix, what regressed, what was reverted,
what silently never landed — so no round repeats a previous one;
(d) write `docs/REPLAY-CORPUS.md`: coverage, sample size, known gaps.
*Done when:* the corpus is queryable, the ledger exists, and a single command replays the
whole corpus through the pipeline offline.

**P0-1 · Measure before tuning.** *(protection confirmation is slow; nobody knows by how
much)* Record per attempt: protection POST → first visible readback, attempts, outcome,
symbol; per market: stream freshness, disconnects, tick → close → fill latency. Replay
supplies the distribution; the live probe confirms it in the first week.
*Done when:* the corpus report answers the stop-policy questions with numbers, and the
first week of live probes matches the replay within a stated tolerance.

**P0-2 · Escalation ladder with an evidence-based grace.** *(containment currently fires
before the venue can confirm)* Read → bounded jittered retries while the position is
open → record every attempt → contain only after the budget (set from P0-1) is spent →
alert `protection-registration-failed` with the attempt trail. The close intent is bound
before POST so the lifecycle stays provable.
*Done when:* replay of the WLD and NEAR sequences holds the position, and a synthetic
never-confirms case still contains and alerts.
**Landed 2026-10-18** (`_await_native_protection`, `BITGET_PROTECTION_GRACE_SECONDS=15`,
3 s jittered readbacks, zero default for library/test callers): reads only, stops early
on a flat position, records `native-protection-verified-late` / `-grace-flat`, and appends
the attempt count to the capability error trail. Coverage: confirmed-late never closes,
never-confirmed still contains once, zero-budget callers unchanged.

**P0-3 · Soft stop primary, hard stop backstop** (policy above). Wire the local stop in
the existing fallback lane, place the wide venue backstop, and ship both in one release;
prove both paths in replay against the corpus, then run the rehearsals in the evidence
list. No observe-only stage: the lane's off switch remains, but the design is committed.
*Done when:* the corpus gap study is recorded, the rehearsals end protected, and live
entries are being closed by the local stop with the venue backstop verified in place.
**Sequencing correction 2026-10-18:** the only local trigger today is a **30 s REST
poll**. Widening the venue stop before a *fast* local trigger exists would be a risk
downgrade: a fast move would overshoot to the ~4.5 % backstop instead of the 2.4 % signal
stop. Order is therefore: grace (done) → stream-driven close callback → then widen the
venue stop in the same release.
**Stream trigger landed (2026-10-19, `116e542`):** `build_bitget_protection_stream` now
wires `BitgetFallbackStreamEngine` when `BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED=1`,
with its callback bound to `submit_fallback_close_async` (the audited seam: ownership
check, atomic claim, one POST, no retry). The engine existed and was tested but was never
constructed; the builder used to raise when mutations were requested, so the local stop
was structurally unreachable. Default remains `0` — arming is one value and the owner's
call, because it changes how live positions exit. The registry is read through a 2 s
snapshot cache so the engine does not query the database per mark. Known bound:
evaluation is serial with the stream loop, so a close in flight (~1–3 s) delays other
marks; the REST lane stays a second trigger.
**Deliberate deviation:** the wide venue backstop is *not* in this release. Stop distance
feeds sizing/liquidation headroom, so widening it needs the corpus numbers (P2-1).
Sequence is now: arm → observe real exits → widen. Recorded rather than silently dropped.

**P0-4 · Pre-entry protection feasibility gate.** *(assumption: anything we can trade we
can protect)* Before an entry POST, require a fresh backstop capability (or a successful
dry placement) for the symbol; refuse `FAILED`/`UNSUPPORTED`/recently-failing symbols and
say why in one alert line.
*Done when:* a symbol whose stop the venue rejects cannot be entered.

**P0-5 · Backstop placement robustness.** Prefer a venue-attached stop (position TP/SL)
over separate plan orders — atomic with the position, fewer rejection modes; keep REST
plans as fallback. Wide levels in normal operation (they are the backstop, not the stop).
*Done when:* replay shows attached backstops in the normal path, fallback proven, and
rejection-driven containment has not occurred in a week.

**P0-6 · Closed-lifecycle terminality.** A filled entry whose position is flat with close
fills proven on the venue settles itself: reconciled, released, recorded, alerted.
*Done when:* WLD and NEAR fixtures settle without an approved baseline.
**Landed 2026-10-18** (`_closed_lifecycle_reason`, `GetOnlyProtectionRecovery.
position_is_flat`): a flat venue read plus a close-side fill for the whole position
settles as `recovery-closed-close-fill-verified` (reconciled, released, two audited
transitions). Flat alone never settles — an unaccounted close stays visible and keeps the
missing-protection verdict — and a failed read settles nothing. Settled dispatches leave
the candidate set (`d.state NOT IN ('CLOSED','RECONCILED')`). Live impact: the six
`owned-flat-close-fill-unproven` dispatches whose close fills *are* recorded (NEAR, WLD,
PENGU, HBAR, STONK) settle on the next recovery pass instead of needing another baseline;
PUMP entries without a recorded close remain an operator decision by design.

## Phase 1 — visibility (cheap, unblocks everything)

**P1-1 · Watchdog sweep.** Parked rows past deadline, lane blocked >2 minutes, stalled
leases, canary exhaustion, stale fallback registrations, missed signals in windows,
notification delivery failures. Positive and negative test each; alert with identity and
a suggested action. *Done when:* a synthetic block of each class pages within 2 minutes.

**P1-2 · Forensic signal accounting, nightly and delivered.** Per signal: received →
parsed (or why not) → dispatched (or why not) → filled (price, size) → protection verified
(latency) → closed (why) → realised P&L and fees. Aggregates: signals lost by cause,
minutes of lane unavailability, protection-latency and stream-freshness percentiles,
wash-trade count, deploy cost. *Done when:* seven consecutive reports with zero
unexplained signals.

**P1-3 · Operator surface, no shell.** Console actions for: re-admit a late signal, settle
stranded protection intents, retire a stale registration, take an approved baseline — same
evidence gates and approval references as today's scripts. One "needs operator" page that
says exactly what to do. *Done when:* the four runbook shell commands have console
equivalents and the drills pass without a terminal.

## Phase 2 — a change process that survives contact

**P2-1 · Replay harness** on the P0-0 corpus, with today's incidents as named fixtures
(WLD, NEAR, ENA, #Q, the claim-contention sequence) driving the full pipeline against a
fake venue, asserting: no double order, no lost signal, no unproven terminal state, and
the specific behaviour each fix added. Every future corpus entry becomes a regression
case for free.
*Done when:* it runs in CI and any trading-behaviour change must extend it.

**P2-2 · Release cadence and a rehearsed rollback.** One rollout per batch; every release
states what behaviour changes, which invariant could break, its rollback command and its
proof; a canary flag for new behaviour; and one rehearsal of plan → main → plan on a quiet
lane with timings. *Done when:* the rehearsal is logged and a rollback takes <5 minutes.

## Phase 3 — edge, only after integrity is boring (all replay-gated)

1. **Position intelligence** — automatic break-even at +1R, trailing after TP1,
   funding-aware holds, time stop, multi-target ladder. The soft stop makes these local
   and free; highest expected value per line of code.
2. **Execution quality** — LIMIT-first routing by spread/depth/window, depth-driven child
   sizing, expected vs realised slippage recorded per entry.
3. **Portfolio risk** — daily-loss/drawdown stop, rolling loss budget, correlation and
   inventory guard, realised-margin guard.
4. **Signal scoreboard → adaptive sizing within the fixed caps** — explicit gate: ≥100
   scored trades. Until then sizing stays constant.

## Strikes (deliberately not doing)

* Local-only stops (policy above explains why).
* Redis as a state store; Postgres owns anything that can move money.
* ML price prediction; the edge is execution and risk.
* A second exchange before Bitget is excellent.
* Adaptive sizing before n≥100.

## How to falsify this plan

| Assumption | Probe | If falsified |
|---|---|---|
| The corpus is representative of live | Replay outcomes vs the first 7 days of live probes | Extend the backfill; treat replay conclusions as provisional for that market |
| Protection confirmation lags seconds | P0-0/P0-1 corpus report | Attached backstops (P0-5) jump ahead of P0-2 |
| The local stop degrades acceptably | Corpus gap study + P0-3 rehearsal | Hard stop returns to the signal level (reversal condition) |
| Containment rarely needed | Escalation count over 30 days | The grace budget is too long; tighten it |
| Signals are lost mostly to lane blocks | P1-2 report | Signal intelligence moves up |
| Console actions remove operator risk | P1-3 drill timings | Shell paths stay first-class and get hardened |

## Invariants that must survive the rework

Intent-before-POST with stable client order ids; never re-post an ambiguous order;
GET-only recovery; additive migrations; the deploy gate; rollout announcements; a bound
close intent before any containment; every parked row carries an attempt budget and a
deadline; alerts carry real identities.
