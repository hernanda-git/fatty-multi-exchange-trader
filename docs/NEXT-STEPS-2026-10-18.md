# Next steps — 2026-10-18, after `4d59465`

State at the start of this document: production runs `4d59465` (grace + closed-lifecycle
settlement), 10/10 services healthy, dispatcher LIVE/idle waiting for a natural signal,
account flat, 0 errors since rollout, `verify_bitget_runtime.sh` clean, host ops worker
active. **P0-2 (evidence-based grace) and P0-6 (closed-lifecycle terminality) are landed.**
The owner asked for the following three workstreams to be documented and then executed
step by step, in this order.

## Order and rationale

1. **WS-1 — measurement (P0-1).** Read-only. Replaces the *reasoned* 15 s grace budget
   with a *measured* distribution, and quantifies how fast a local stop must react.
2. **WS-2 — next-signal audit view.** Read-only. Makes the first live exercise of the
   grace fully auditable in one command. Runs alongside WS-1.
3. **WS-3 — stream-driven local stop (P0-3).** Mutating, flag-gated, rehearsed. Only
   after WS-1 says the local trigger can be *fast enough* that widening the venue stop to
   a backstop is not a risk downgrade.

Nothing in WS-1 or WS-2 mutates production state: both are SQL/report code. WS-3 is the
only workstream that changes live behaviour, and it ships behind flags with rehearsals.

---

## WS-1 · Measurement: protection latency, timing, stream freshness (P0-1)

**Goal.** Numbers for four questions that are currently answered by judgement:

| Question | Why it matters |
|---|---|
| How long after POST does native protection become provably visible? | sets `BITGET_PROTECTION_GRACE_SECONDS` (currently 15 s by reasoning) |
| How often does the first readback fail while the plan is fine? | the wash class we just fixed — how frequent is it really |
| How fast is the stream vs. REST, and how often is it stale? | decides whether a stream-driven local stop can be primary |
| For trades that closed, what was fill→close latency and outcome? | the baseline any new stop policy must beat |

**Deliverable.** `scripts/protection_latency_report.py` — read-only, Postgres only
(no venue calls in the default mode), prints a markdown table per trade plus aggregates
(median/p95), and `--json` for machine use. It opens every connection with
`-c default_transaction_read_only=on`, so it cannot write even by accident.

**Durable sources it uses** (from the writer inventory):

| Measure | Identifier |
|---|---|
| signal receipt | `dispatch_transitions.evidence->>'source_received_at'` |
| dispatch engaged | `dispatches.created_at` |
| entry fill | `fills.filled_at` (provider `cTime`, fallback `uTime`) |
| protection claim | SL/TP `live_order_intents.created_at` (`role IN ('SL','TP')`, `protection_root_oid = entry.client_order_id`) |
| protection verdict | `dispatches.terminal_reason` + `dispatch_transitions` (`stage`, `reason_code`, `observed_at`, `attempt`) |
| capability / stream state | `bitget_protection_capabilities.native_state`, `last_verified_at`, `stream_state`, `last_stream_at` |
| close | `live_order_intents` `role='EMERGENCY_CLOSE'` (`<entry>-emergency`) and its `fills.filled_at` |
| trade root | `live_order_intents.client_order_id` where `role='ENTRY'`; dispatch bridge is `bitget_margin_reservations.dispatch_id → client_order_id` (`live_order_intents.dispatch_id` is not reliably populated) |

**A measured finding already** (from the recon, not from the new tool): four dispatches
carry **5 285 rows each** in `dispatch_transitions` (21 410 rows total), because recovery
re-transitions the same unresolved lifecycle on every tick even when the state and reason
are unchanged. That is pure audit noise and unbounded growth. Follow-up **P0-1b**: skip a
transition when `(to_state, reason)` matches the last one for that dispatch, and record a
repeat counter instead — the new report collapses repeats so the noise is visible today.

**Acceptance.**
- runs against the production database read-only and completes in seconds;
- per trade reports: entry fill time, protection claim time, first recovery readback,
  verdict, close fill time, and transition churn;
- reports its own join coverage, so a broken bridge is visible instead of silently
  dropping trades;
- the WLD and NEAR wash cases appear with their pre-grace failure latency;
- output pasted into `docs/PROTECTION-LATENCY-2026-10-18.md` as the evidence artifact.

**Non-goals.** No venue calls, no writes, no tuning. Per-attempt protection readbacks are
listed under "still unmeasurable" — recording them is a separate, small instrumentation
slice (the grace currently writes attempts only to the capability error trail and a log
line).

---

## WS-2 · Next-signal audit view

**Goal.** One command that answers "what happened to this trade, in order, with proof"
for the next natural signal — the handover's stated next step.

**Deliverable.** `scripts/dispatch_trail.py [--latest] [--dispatch ID | --client-oid CID]`
— read-only, one screen: inbound message → canonical signal → admission verdict →
dispatch + every transition (reason, evidence keys, timestamps) → intents (entry, SL, TP,
close/emergency) with states, quantities, provider ids → fills → protection capability →
recovery/settlement verdict and readiness impact.

**Acceptance.**
- `--latest` on the newest dispatch prints a correctly ordered trail;
- run against a historical washed trade (NEAR/WLD) it shows the exact race: plans
  accepted, first readback failed, containment, then flat;
- no writes; exit codes: 0 found, 2 not found.

**Non-goals.** No new persistence; no console UI change in this slice (a console
deep-link is a follow-up only if the operator asks for it).

---

## WS-3 · Stream-driven local stop, venue backstop (P0-3)

**Goal.** The signal-level stop becomes a *local* decision driven by the live price
stream (sub-second), and the venue-side stop moves out to a wide backstop. Order matters:
the trigger must exist **before** the venue stop is widened, otherwise a fast move
overshoots from the 2.4 % signal stop to the ~4.5 % backstop — worse than today.

**What the code already has (seam map, verified):**

- The production stream runtime is `BitgetProtectionStreamRuntime`, owned by
  `monitor-bitget`. It consumes **only** fresh `mark_price` events and *drops* the
  position/order/fill/plan events it already normalizes
  (`execution/bitget_protection_stream.py:219-221`).
- `BitgetFallbackStreamEngine` — threshold decision, duplicate/out-of-order fencing, and
  an injected `CloseCallback` — **already exists and is tested**
  (`tests/unit/test_bitget_protection_stream.py`: one close request per event, never
  twice, out-of-order safe, non-mark ignored), but **no service code constructs it**.
- `build_bitget_protection_stream` **raises** when
  `BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED=1` (`service.py:250-255`): mutations are
  structurally unreachable today, not merely disabled.
- The audited close seam to reuse is
  `submit_fallback_close_async(client, entry, mark_price=…, reason=…, environment=…)`
  (`execution/bitget_fallback_protection.py:410-515`): environment check, provider
  position GET, exact ownership check, quantity clamp, atomic durable claim,
  `fallback_protection.state='closing'` before POST, exactly one `place_market_close`,
  no retry of an ambiguous POST, matched fill reconciliation.
- The only wired software-stop lane is the REST fallback poll at
  `BITGET_MONITOR_POLL_SECONDS` (default 30 s), gated by
  `BITGET_FALLBACK_MUTATIONS_ENABLED`; its decision predicate is `check_thresholds`
  (`bitget_fallback_protection.py:236-249`).
- Flag naming correction: socket freshness is `BITGET_PROTECTION_STREAM_STALE_SECONDS`;
  `BITGET_PROTECTION_STALE_SECONDS` is the capability-admission bound. A small wiring bug:
  `build_bitget_protection_stream` passes `stale_after` to the socket but omits it when
  constructing the runtime (`service.py:299-304`), so runtime freshness silently uses its
  5 s default.

**Slices (each shippable, each verified).**
- **3a — shadow mode (deliberately skipped):** the owner's P0-3 policy says no
  observe-only stage, so the lane lands armed-capable from the start and the off switch
  (`BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED=0`) is the safety position, not a phase.
  The `stale_after` wiring fix that this slice would have carried is included in 3b.
- **3b — arm it (landed):** `BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED=1` is now a valid
  mode; it builds `BitgetFallbackStreamEngine` inside `build_bitget_protection_stream` and
  binds its callback to `submit_fallback_close_async` (exact-ownership check, atomic
  durable claim, one POST, no retry). Flag `0` builds exactly the previous observe-only
  runtime — no engine, no callback. `1` without an execution client raises; any value
  other than `0`/`1` raises; `STREAM_MODE` still rejects anything but `observe`.
  `stale_after` now reaches socket, runtime, and engine from one parse. The fallback
  registry is read through a 2 s snapshot cache so the engine does not query the
  database on every mark (the close seam re-reads the position and re-checks ownership,
  so a stale stop payload cannot cause a wrong close).
  **Known bound:** evaluation is serial on the stream loop, so while a close is in
  flight (≈1–3 s) other marks are not evaluated. The REST lane remains a second trigger;
  a bounded work-queue is the follow-up if measurement shows second-order stalls.
  Tests: default path unchanged; engine receives only fresh marks; duplicate and
  out-of-order marks never produce two closes; a raising close callback or engine error
  cannot kill the stream or stop capability recording; `stale_after` propagation.
- **3c — rehearsals:** kill the process, drop the socket, replay a fast candle; each ends
  protected; zero wash trades; every local close auditable via the WS-2 trail.
- **3d — widen the venue stop and enable mutations in one release,** with rollback being:
  mutations back to `0` and the venue stop back to the signal level.

**Acceptance.** Rehearsals end protected; local closes are audited end-to-end; the corpus
from WS-1 shows no trade worse off than under the current venue-stop policy.

**Rollback.** Flag flip (`BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED=0`) plus restoring
the venue stop level; both are single-value changes, documented in the handover.

---

## Definition of done for the whole sequence

- WS-1 report exists, has been run on production, and its numbers are recorded in docs;
- WS-2 command works on live data for both a new and a historical trade;
- WS-3 either ships with rehearsals green, or is explicitly parked with the reason
  recorded (measurement says the local trigger cannot beat the venue stop in the current
  architecture);
- `docs/MASTER-PLAN.md` and `docs/HANDOVER-2026-10-18.md` reflect the new state.

---

## WS-3 · Exit policy (owner decision, 2026-10-19) and runbook

**The rule, as decided:**

1. A position whose exchange-side SL/TP cannot be confirmed — or is rejected outright —
   is **never closed by us** for that reason. There is no 15-second-then-close: the grace
   exists only to avoid a false "missing" verdict, and when it expires the position is
   kept.
2. Such a position is **marked locally managed** (durable `fallback_protection` row,
   capability `FAILED` with the readback reason) and the **local price monitors** close it
   at the signal's stop when the live price crosses it.
3. If the local lane is **also** unavailable (stream stale, or the lane disabled), the
   system **alerts only** — a detailed notification with symbol, direction, quantity,
   entry, stop, take-profits, the venue readback reason, and the local-lane state — and
   still does not close. The owner (or a future autonomous agent acting on the exchange
   side) handles that position; the running bot is not to be disturbed by it.

**Landing state of the switches:**

| switch | value | meaning |
| --- | --- | --- |
| `BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED` (monitor) | **1** | the sub-second stream trigger may close a locally-managed position |
| `BITGET_FALLBACK_MUTATIONS_ENABLED` (monitor) | **1** | the 30 s REST lane is the second local trigger |
| `BITGET_EMERGENCY_CONTAINMENT_ENABLED` | **0** | the old "close because protection is unconfirmed" path is opt-in and off |

**Verify after a rollout:**
1. `docker compose exec -T monitor-bitget printenv | grep -E 'MUTATIONS|STREAM_ENABLED'`
   shows the values above;
2. `docker compose logs monitor-bitget | grep -i stream` shows the engine constructed and
   the runtime still recording `stream_state=HEALTHY`;
3. a locally-managed position closes through exactly one `place_market_close` POST, the
   `fallback_protection` row goes `closing → closed`, and `scripts/dispatch_trail.py
   --latest` shows the close; the venue stop stays as the backstop, and the duplicate
   fence prevents a second close;
4. with the triggers off, a locally-managed position produces the detailed alert and no
   close.

**Rollback (seconds):** set either trigger back to `0` and recreate `monitor-bitget`;
re-enable containment with `BITGET_EMERGENCY_CONTAINMENT_ENABLED=1` if the old
close-on-unconfirmed behaviour is ever wanted again.

**Still deliberately sequenced:** the wide venue backstop. Stop distance feeds sizing and
liquidation headroom, so widening it needs the replay corpus (P2-1); sequence is
local management (now) → observe real exits → widen.
