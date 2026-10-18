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

**Evidence bar before the local stop becomes primary** (all five, or it stays
observe-only):

1. stream freshness and disconnect-duration distribution (percentiles, not anecdotes);
2. tick → close-order → fill latency, p50/p99, measured on real attempts;
3. gap behaviour: how much worse a local close fills than a venue trigger on a fast candle;
4. partial-fill correctness: the close is sized against the actual position, every time;
5. rehearsals — kill the process, drop the socket, replay a fast candle with a live
   position; each rehearsal must end with the position protected by the backstop.

**Reversal conditions.** If rehearsals show disconnects longer than the agreed budget,
or gap slippage worse than the agreed bound, the hard stop returns to the signal level
and the local stop returns to observe-only. This is a policy with a spine, not a mood.

Every policy change from here on carries the same shape: a decision record, an evidence
bar, and published reversal conditions.

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

**P0-1 · Measure before tuning.** *(assumption: protection confirmation is slow, and
nobody knows by how much)* Record per attempt: protection POST → first visible readback,
attempt count, outcome, symbol; per market: stream freshness, disconnect durations, tick
→ close → fill latency. Surface p50/p99 on the card.
*Done when:* ≥20 protection attempts and ≥7 days of stream/latency data exist, and the
stop-policy evidence bar can be judged from numbers.

**P0-2 · Escalation ladder with an evidence-based grace.** *(containment currently fires
before the venue can confirm)* Read → bounded jittered retries while the position is
open → record every attempt → contain only after the budget (set from P0-1) is spent →
alert `protection-registration-failed` with the attempt trail. The close intent is bound
before POST so the lifecycle stays provable.
*Done when:* replay of the WLD and NEAR sequences holds the position, and a synthetic
never-confirms case still contains and alerts.

**P0-3 · Soft stop primary, hard stop backstop** (policy above). Wire the local stop in
the existing fallback lane (behind its gate), place the wide venue backstop, prove both
paths in replay, then run the rehearsals in the evidence bar.
*Done when:* all five evidence-bar items are recorded and the rehearsals end protected;
until then the lane stays observe-only.

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

**P2-1 · Replay harness** with today's incidents as fixtures (WLD, NEAR, ENA, #Q, the
claim-contention sequence) driving the full pipeline against a fake venue, asserting: no
double order, no lost signal, no unproven terminal state, and the specific behaviour each
fix added. *Done when:* it runs in CI and any trading-behaviour change must extend it.

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
| Protection confirmation lags seconds | P0-1 instrumentation | Attached backstops (P0-5) jump ahead of P0-2 |
| The local stop degrades acceptably | P0-3 rehearsal: gap + disconnect | Hard stop returns to the signal level (reversal condition) |
| Containment rarely needed | Escalation count over 30 days | The grace budget is too long; tighten it |
| Signals are lost mostly to lane blocks | P1-2 report | Signal intelligence moves up |
| Console actions remove operator risk | P1-3 drill timings | Shell paths stay first-class and get hardened |

## Invariants that must survive the rework

Intent-before-POST with stable client order ids; never re-post an ambiguous order;
GET-only recovery; additive migrations; the deploy gate; rollout announcements; a bound
close intent before any containment; every parked row carries an attempt budget and a
deadline; alerts carry real identities.
