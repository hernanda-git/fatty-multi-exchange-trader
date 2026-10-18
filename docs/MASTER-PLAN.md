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
# status: which revision is the live tree on, and is the lane quiet?
cd ~/apps/fatty-multi-exchange-trader
git log --oneline -1
sudo -u valarion python3 - <<'PY'
# gate only; prints allowed/blocked_by
import subprocess, sys; sys.path.insert(0, "src")
from fatty_trader.ops.deploy_safety import evaluate_deploy_gate
def q(sql):
    out = subprocess.run(["docker","compose","exec","-T","postgres","psql","-U","fatty_app",
                          "-d","fatty_trader","-At","-F","\t","-c",sql],
                         capture_output=True, text=True, check=True).stdout.strip()
    return tuple(out.split("\t")) if out else None
print(evaluate_deploy_gate(q))
PY

# promote the plan line (gate + window + staged rollout + verifier + accounting)
python3 scripts/rollout.py --branch master-plan

# switch back to the stable line
python3 scripts/rollout.py --branch main

# roll back to an exact previous revision (e.g. after a bad promote)
python3 scripts/rollout.py --revision <sha>

# only when the gate is legitimately blocking (records the reason in the log)
python3 scripts/rollout.py --branch master-plan --force-reason "owner-approved mid-session switch"
```

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
* Unit tests for the gate and the sweep (`tests/unit/test_deploy_safety.py`).

Nothing else in the plan is implemented yet; no trading path reads any of it.

## Explicit non-goals

* No Redis as a state store (Postgres owns anything that can move money); Redis only
  for caches/rate limiting if ever needed.
* No ML price prediction; expectancy comes from execution quality and risk control.
* No second exchange before Bitget is excellent.
* No removal of a fence in favour of retry logic.
