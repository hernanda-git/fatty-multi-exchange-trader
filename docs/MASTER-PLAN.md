# Master plan — from faithful executor to a clever, self-proving bot

Status: **plan line** (`master-plan` branch). The live service runs `main`; nothing
in this branch is deployed until an operator promotes it (see *Switch runbook*).

Goal: keep every safety property the live bot already has (intent-before-POST,
GET-only recovery, canary cap, liquidation-aware sizing, approval-gated baselines)
and add the four things it lacks — **perception**, **portfolio thinking**,
**learning**, and **self-proving memory**. Nothing here may weaken a fence; each
phase must be provable before it can trade.

## Ground rules (non-negotiable)

1. **Isolation.** Plan work happens on `master-plan` (worktree, e.g.
   `~/apps/fatty-plan`). It never points at the live database or venue: tests use
   `FATTY_TEST_POSTGRES_DSN` against the local test container, and the replay
   harness uses recorded fixtures. Only `scripts/rollout.py` touches live.
2. **Additive migrations (expand/contract).** A release may add columns/tables and
   backfill, but never drop or rename what the previous release still reads.
   Contraction ships one release later, after the new code is proven. This is what
   makes "switch back whenever I want" real.
3. **Switchable revisions.** `scripts/rollout.py --branch <name>` or
   `--revision <sha>` deploys any verified revision on top of the same data with
   the same gates. There is no separate environment to drift.
4. **Gate before stopping anything.** A rollout refuses to start while the lane is
   busy — in-flight dispatch, open entry intent/reservation, signal inside its
   entry window, or a message received inside `--quiet-minutes` (default 5).
   `--force-reason` overrides and is recorded.
5. **No signal may vanish silently.** Each rollout opens a maintenance window,
   closes it at the end, and records every channel message that lapsed inside it
   (`missed_signals`) with one notification. Crashed rollouts are settled on the
   next run.
6. **Every phase ships with its proof**: unit + e2e on Linux, the replay
   acceptance for the phase, `ruff check`, `ruff format --check`, `mypy src`.

## Workstreams

| # | Workstream | What lands | Acceptance proof | Est. |
|---|---|---|---|---|
| 1 | **Reconciliation kernel** (spine) | `deadline_at`/`attempts`/`last_error`/`evidence` on every durable lane; `CHECK (parked ⇒ deadline_at NOT NULL)`; one sweeper that proves terminality via GET-only readback, retires on proof, dead-letters with an alert | "no absorbing state" property test; chaos drill: kill each worker mid-flight, row resolves or alerts within deadline | 6–8 d |
| 2 | **Watchdog + alerts** | invariant checks (parked rows, `recovery-blocked`, canary exhaustion, stale leases, stale fallback registrations, missed signals) → `notifications_outbox` → Telegram | every check has a positive/negative unit test; one synthetic incident end-to-end | 3–4 d |
| 3 | **Market microstructure** | L2 top-of-book, spread, depth-at-price, funding rate/next funding, cached | journal records expected vs realized slippage per entry | 3–4 d |
| 4 | **Execution quality** | routing policy (LIMIT-first vs MARKET by window/ spread/ depth), depth-driven child sizing, latency + slippage metrics | replay shows ≥X bps improvement at equal fill rate | 4–5 d |
| 5 | **Position intelligence** | automatic break-even at +1R, trailing after TP1, funding-aware holds, time stop, multi-target ladder | replay expectancy uplift; every policy step writes a journal row | 5–7 d |
| 6 | **Portfolio risk** | daily-loss/drawdown stop, rolling loss budget, correlation/inventory guard, realized-margin guard | injected bad-day sim fires the stop at the configured budget | 3–4 d |
| 7 | **Signal intelligence** | vision-first intake, `MANUAL_REVIEW` requeue, maintenance-window policy, per-source scoreboard | 60-day replay: no missing or unattributed signal; scoreboard with win rate/expectancy | 4–5 d |
| 8 | **Replay + simulation harness** | recorded venue/channel fixtures, full pipeline offline, invariant assertions (no double order, no lost signal, no unproven terminal state) | runs in CI; every later phase depends on it | 5–6 d |
| 9 | **Adaptive sizing within caps** | size scales with scoreboard confidence inside the fixed 1.1 USDT / 20x / 1-position budget | replay + explicit cap tests; caps immutable in tests | 3–4 d |

Ordering: 1 → 2 → 8 → 3/4 → 5/6 → 7 → 9. Workstreams 1 and 2 are the only ones
that can be promoted without the replay harness.

## Switch runbook

```bash
# the tool lives on the plan line; refresh it first so you run the current one
cd ~/apps/fatty-plan && git pull --ff-only

# ask the gate before anything else (read-only; runs under the host's python3)
python3 scripts/deploy_gate_check.py

# promote the plan line (gate + window + staged rollout + verifier + accounting)
python3 scripts/rollout.py --branch master-plan

# switch back to the stable line
python3 scripts/rollout.py --branch main

# roll back to an exact previous revision (e.g. after a bad promote)
python3 scripts/rollout.py --revision <sha>

# only when the gate is legitimately blocking (records the reason in the log)
python3 scripts/rollout.py --branch master-plan --force-reason "owner-approved mid-session switch"
```

`scripts/rollout.py` runs under the host's system interpreter (verified on 3.9), so
keep the ops module free of 3.10-only runtime calls (no `zip(strict=)` — the library
targets 3.11, the rollout interpreter is older).

Every rollout is announced: `rollout starting`, `rollout blocked` (with the exact gate
reason), `rollout complete` and `rollout failed` land in `notifications_outbox` and reach
Telegram, so a detached deploy can never look like silence again. The wrapper's ssh
session may still hang after the script exits — that is cosmetic; the notification and
the rollout log at `/tmp/deploy-*.log` are the source of truth.

Rollback rules: switching back is safe **as long as the newer release's migrations
were additive (rule 2)**; the old code ignores the new columns. If a release ever
contracts the schema, restore the pre-promote snapshot first
(`systemctl --user start fatty-backup` before promoting anything that contracts).

## What this branch already contains

* `maintenance_windows` + `missed_signals` (migration 34) and
  `fatty_trader/ops/deploy_safety.py`: the deploy gate and the missed-signal sweep.
* `scripts/rollout.py`: the previously ad-hoc rollout script, versioned, gated,
  window-accounting, and revision-addressable (this is what makes switching safe
  and reversible).
* `scripts/deploy_gate_check.py`: read-only "may I deploy right now?" helper.
* Unit tests for the gate and the sweep (`tests/unit/test_deploy_safety.py`).

Verified on the live lane without touching it: the gate reads the live database
read-only from the plan workspace and reports `allowed: true` with all five counts
zero; the live tree stays on `main` at `a0f87f0`, the schema stays 33 and neither new
table exists there yet.

Nothing else in the plan is implemented yet; no trading path reads any of it.

## Incident 2026-10-18 09:16 UTC — WLD wedge, and the four fixes it forced

A WLD entry filled 11 at 09:16:17, SL/TP registration was never *verified*, the
protection watchdog emergency-closed the position at 09:16:19 (correct: it never held
an unprotected position), and recovery then refused readiness with
`recovery-missing-protection:owned-flat-close-fill-unproven` — every later signal was
blocked until 11:52 UTC, including NEAR #16326, whose 5-minute window closed while the
lane was down. No exposure was ever open; the cost was availability and one lost signal.

Three defects met, all fixed here with tests:

1. **Close-binding race** (the wedge): `bind_requested_close` required the reservation
   to be `consumed`, so a protective close that raced the fill produced no binding, the
   close's fills were never provable, and recovery blocked forever. The bind (and the
   reconcile candidate query, which reads the same reservations) now accept
   `consumed|reserved`; the entry intent's `filled_qty>0` already proves the fill.
2. **Stranded protection intents** (what made even the approved baseline refuse): an
   `unknown` SL/TP intent whose plan the venue no longer holds can never reconcile, and
   an unresolved non-entry intent blocks both recovery and the baseline. The GET-only
   recovery reader now settles such intents as `cancelled` — only when the symbol is
   flat, the venue lists no plan and no pending order, and the intent filled nothing —
   with one alert. `scripts/reconcile_unknown_intents.py` exposes the same evidence gate
   to an operator.
3. **Silence** (why nobody knew for 2.5 hours): a blocked lane produced only a log line.
   The dispatcher now emits one `execution-alert` per distinct recovery issue set
   (dedup-keyed, so 30-second cycles cannot spam), and execution alerts render the
   identities they actually have instead of `Dispatch: (empty)` placeholders.

Open follow-up found in the same incident: protection verification was given ~2 s before
the watchdog escalated to an emergency close, which is likely too tight for provider
readback. Add a bounded verification grace (readback with retry) before escalating, and
alert on protection-registration failures themselves.


## Explicit non-goals

* No Redis as a state store (Postgres owns anything that can move money); Redis only
  for caches/rate limiting if ever needed.
* No ML price prediction; expectancy comes from execution quality and risk control.
* No second exchange before Bitget is excellent.
* No removal of a fence in favour of retry logic.
