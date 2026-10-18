# Master plan — execution integrity first, cleverness second

Status: **plan line** (`master-plan` branch). Live runs this line already; the rework
continues here behind the gated rollout. Session that produced this revision:
`01a14ef3-320f-7394-8558-a4d0c9f2c7e3`. Current state and runbooks:
`docs/HANDOVER-2026-10-18.md`.

This revision replaces an earlier plan that was organized by architecture. That plan's
thesis — *every durable record must be able to prove itself terminal* — survived the day
intact and stays. Its **ordering** did not survive: the day's losses came from timing on
the money path, from operator silence, and from missing forensics, none of which the
first plan ranked first. So the plan is now organized by **what retires risk**, and each
item must state the assumption it retires and the proof that retires it.

## The objective, stated honestly

The bot does not have an edge problem yet; it has an **integrity problem**. Today it
failed to convert two signals into held positions (WLD, NEAR): both entries filled, and
both were emergency-closed within ~3 s because protection verification could not
confirm the plans the venue had accepted ~1 s earlier. The account never carried
unprotected exposure, and each wash cost cents — but *every* signal is at risk of being
turned into a wash until the verification path is patient and evidence-driven. Until
that is fixed, work on entry quality, microstructure or sizing is decoration.

**Definition of integrity** (the P0 exit condition): a signal either becomes a held
position with venue-verified protection, or is refused/contained with a recorded,
alerted reason — and the operator can see which, and why, without opening a shell.

## What the day proved (evidence for this revision)

| Evidence | Consequence for the plan |
|---|---|
| WLD 09:16 and NEAR 12:54: entry filled → protection plans accepted → verification failed → containment within ~3 s → lifecycle unprovable → lane blocked | Containment timing is a first-class design surface, not an implementation detail |
| Both blocks needed an **approved baseline** to clear | Closed-lifecycle terminality is a P0 gap: a settled trade must settle itself |
| NEAR's re-admitted dispatch sat `QUEUED` 28 min: the claim's unqualified `FOR UPDATE` locked every joined table and the analyzer starved it (0/6 → 6/6 measured) | Contention on shared tables is a live failure mode; probes beat theory |
| A 2.5 h lane block produced only log lines; the operator learned at 18:47 | Observability is P0, not P2 |
| Deploys finished while their ssh wrappers hung; nothing announced | The operator loop is part of the system and needs the same no-silence rule |
| Manual forensics decided every incident; nothing produced those answers automatically | Forensics must become a scheduled, delivered artifact |
| ~19 fills total | Sizing/adaptivity work is struck until the scoreboard has real n |

## Ground rules (non-negotiable)

1. **Isolation.** Plan work happens on `master-plan` (worktree `~/apps/fatty-plan`).
   Tests use the disposable Postgres; the rollout is the only path to live.
2. **Measure before tuning.** No timeout, retry budget or threshold lands without the
   measurement that justifies it, recorded in the commit.
3. **Additive migrations (expand/contract).** Never drop or rename what the previous
   release still reads; contraction ships a release later. This is what makes the
   switch back real.
4. **Switchable revisions.** `scripts/rollout.py --branch <line>|--revision <sha>` with
   the same gate, window accounting and verification on both lines.
5. **Gate before stopping anything.** A rollout refuses a busy lane (in-flight
   dispatch, open intent/reservation, open entry window, recent message).
6. **Nothing silent, anywhere.** Signals that lapse are recorded and announced;
   blocked lanes alert once per issue set; rollouts announce start/refusal/completion/
   failure; alerts carry real identities.
7. **Proof per slice.** Unit + e2e on Linux, ruff, mypy strict, and the slice's own
   acceptance proof (a probe, a replay, or a production observation).
8. **One invariant may never be traded away:** never re-post an ambiguous order, and
   never remove a fence in favour of retry logic.

## Phase 0 — Execution integrity (do this first)

### W1. Protection-latency instrumentation *(riskiest assumption: venue plan readback confirms within seconds)*
Record, per protection attempt: POST timestamp, first readback that shows the plans,
attempt count, terminal outcome, symbol. Persist to a table; surface p50/p99 on the
card. **This is the measurement that sets W2's budget.**
*Done when:* ≥20 attempts recorded and a per-symbol latency distribution exists.

### W2. Escalation ladder with an evidence-based grace *(assumption: containment currently fires before the venue can confirm)*
Replace the single early readback with an explicit ladder: read → bounded retries with
jitter while the position is still open → records of each attempt → containment only
after the budget (from W1, default conservative) is exhausted → `protection-registration-failed`
alert carrying the attempt trail. The close intent is bound before POST (already fixed,
`97ddaa0`) so the lifecycle stays provable.
*Done when:* replay of the WLD and NEAR sequences keeps the position open with verified
protection, while a synthetic never-confirms case still contains and alerts.

### W3. Pre-entry protection feasibility gate *(assumption: any symbol we can trade we can protect)*
Before an entry POST, check the symbol's protection capability (fresh `VERIFIED`, or a
successful dry placement); refuse entry for `FAILED`/`UNSUPPORTED`/recently-failing
symbols. Cheaper to not enter than to enter and contain.
*Done when:* a symbol whose protection was rejected (e.g. 43011) cannot be entered, and
the refusal is alerted with the capability evidence.

### W4. Attached protection where the venue supports it *(assumption: two POSTs plus a race is acceptable)*
Prefer venue-attached protection (position TP/SL) over separate REST plans, so the
protection is atomic with the position; keep REST plans as fallback for venues/symbols
that need them. Reduces the race window to the venue's own semantics.
*Done when:* replay shows attached protection used for the normal path, fallback proven
still working, and W2's ladder only engaged on the fallback path.

### W5. Closed-lifecycle terminality *(assumption: an operator baseline is needed for a settled trade)*
A filled entry whose position is flat with close fills proven on the venue must settle
itself — reconcile, release, record — with an audit trail. Baselines remain for history,
not for yesterday's trade.
*Done when:* the WLD and NEAR fixtures settle without an approved baseline, and the
baseline path is only reachable for pre-existing history.

## Phase 1 — Visibility (P0/P1, cheap, unblocks everything)

### W6. Watchdog sweep
Invariant checks on a timer: parked rows past deadline, lane blocked >2 min, stalled
leases, canary exhaustion, stale fallback registrations, maintenance windows with
missed signals, notification delivery failures. Each check: positive and negative test,
one alert with identity + suggested action.
*Done when:* a synthetic block of each class pages within 2 minutes and clears.

### W7. Forensic signal accounting (nightly, delivered)
Per signal: received → parsed (or why not) → dispatched (or why not) → filled (price,
size) → protection verified (latency) → closed (why) → realised P&L, fees. Aggregates:
signals lost by cause, minutes of lane unavailability, protection-latency p50/p99,
wash-trade count, deploy cost (messages lapsed inside windows).
*Done when:* seven consecutive nightly reports with zero unexplained signals.

### W8. Operator surface (no shell)
Console actions for: re-admit a late signal, settle stranded protection intents, retire
a stale registration, take an approved baseline — each keeping today's evidence gates
and approval references. A single "needs operator" page listing exactly what to do.
*Done when:* the runbook's four shell commands have console equivalents and the defined
drills run end to end without a terminal.

## Phase 2 — Change process that survives contact (P1)

### W9. Replay harness *(the permanent form of today's probes)*
Fixtures from real incidents (WLD, NEAR, ENA, #Q, the claim-contention sequence) driven
through the full pipeline against a fake venue, asserting: no double order, no lost
signal, no unproven terminal state, and the specific behaviour each fix introduced.
*Done when:* it runs in CI and any trading-behaviour change must extend it.

### W10. Release cadence and a rehearsed rollback
One rollout per batch; every release states what behaviour changes, which invariant
could break, its rollback command and its proof; migrations additive; a canary flag for
new behaviour; and **one rehearsal** of plan → main → plan on a quiet lane with timings
recorded.
*Done when:* the rehearsal is logged and a rollback takes <5 minutes end to end.

## Phase 3 — Edge, only after integrity is boring

Ordered by expected value, all gated on W9 replay proof:

1. **Position intelligence** — automatic break-even at +1R, trailing after TP1,
   funding-aware holds, time stop, multi-target ladder. Highest expected value per line
   of code; measurable in replay.
2. **Execution quality** — LIMIT-first routing by spread/depth/window, depth-driven
   child sizing, expected-vs-realised slippage recorded per entry.
3. **Portfolio risk** — daily-loss/drawdown stop, rolling loss budget, correlation and
   inventory guard, realised-margin guard (planned margin already capped).
4. **Signal scoreboard**, then and only then **adaptive sizing within the fixed caps** —
   explicit gate: ≥100 scored trades. Until then sizing stays constant.

## Strikes (deliberately not doing)

* Redis as a state store (Postgres owns anything that can move money).
* ML price prediction; the edge is execution and risk, not prophecy.
* A second exchange before Bitget is excellent.
* Adaptive sizing before the scoreboard has n≥100.
* Any change that removes a fence in favour of retry logic.

## How to falsify this plan

Cheap probes that would force a re-order, so this document can be wrong quickly:

| Assumption | Probe | If falsified |
|---|---|---|
| Plan confirmation lags seconds | W1 instrumentation | Attached protection (W4) jumps ahead of W2 |
| Containment rarely needed | Count escalations over 30 days | The grace budget is too long; tighten it |
| Signals are lost mostly to lane blocks | W7 report | If parsing/vision dominates, signal intelligence moves up |
| Console actions remove operator risk | W8 drill timings | Keep shell paths first-class and harden them instead |

## Invariants that must survive the rework

Intent-before-POST with stable client order ids; never re-post an ambiguous order;
GET-only recovery; additive migrations; the deploy gate; rollout announcements; every
parked row carries an attempt budget and a deadline; alerts carry real identities.
