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
- **3a — shadow mode (no mutations):** construct `BitgetFallbackStreamEngine` in
  `monitor-bitget` with a callback that only records the decision (symbol, mark, stop,
  decision latency, would-have-closed) — never posts; fix the `stale_after` wiring. This
  gives real tick→decision latency and a dry-run population of would-be closes.
- **3b — arm it:** make `BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED=1` a *valid* mode
  only when the engine is wired, with the callback bound to
  `submit_fallback_close_async`; default stays `0`. Tests: healthy protection never
  closes; a crossed signal stop closes exactly once; duplicate/out-of-order events fence;
  a failed close leaves the venue backstop as the remaining protection.
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
