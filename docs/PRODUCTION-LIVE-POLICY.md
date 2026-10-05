# Permanent production LIVE-only policy

This is the current owner directive for the production Compose deployment:
`TRADER_MODE=LIVE`, `BITGET_MODE=LIVE`, `BITGET_EXECUTION_ENABLED=1`.
No DEMO deployment and no execution-disabled rollout are authorized. Historical
DEMO/readiness reports and mutation-closed rollout examples are evidence, not
current operating instructions. This policy takes precedence over those examples.

## Admission, not automatic authorization

Compose pins `FATTY_PRODUCTION_LIVE_ONLY=1` for dispatcher, inherited monitor,
operator bot and source management. Their startup/health configuration rejects
non-LIVE, missing or disabled execution values before database/provider work.
Execution defaults to `1` in production Compose, but explicit conflicting shell
or `.env` overrides are **rejected**, never silently rewritten or auto-enabled.
The shared library's development fixtures remain isolated from production.

Before any approved deployment, run:

```bash
bash scripts/check_production_live_policy.sh
```

This read-only command checks every Bitget service in the **rendered** Compose
configuration, including service overrides/inheritance, without printing secrets.
Run it before build/recreate commands; `docker compose config --quiet` only checks
Compose syntax, not the policy. Direct Compose startup also enforces the policy
in application configuration. Do not remove or override the pinned marker.
After an approved deployment, `scripts/verify_bitget_runtime.sh` checks actual
container LIVE/LIVE/1 values before its DB/provider probes and rejects active
global/Bitget kill switches without releasing them. It also hashes the copied
Python source tree in each Bitget service and web against the host source tree;
stale or missing source fails instead of trusting the host Git SHA alone. Host configuration is
not evidence of existing container configuration. No restart/recreate is implied
by either checker.

Web receives the same LIVE gate configuration and reports it under
`configuration.execution_enabled` and `live_execution_enabled`. It cannot observe
all dispatcher admission gates: `orders_enabled` is null (unknown), not a false
claim of disabled execution or a true claim of order readiness. Fresh worker
progress/dependency evidence still determines readiness independently.

## Safety remains fail-closed

LIVE execution being enabled is necessary, not sufficient to submit an entry.
Keep approval reference, positive concurrency cap, clock-skew validation,
global/venue kill switches, final entry fence, source freshness, position/risk
limits, protection admission, degraded-state gates and idempotent intent fences.
Operator/fallback/stream mutation flags retain their independent defaults and
approval requirements. This policy never clears a latch or forces a trade.

Use established audited kill-switch/containment procedures for an incident; do
not replace them with DEMO or execution=0, and never clear safety gates merely to
satisfy deployment admission. Plan disruptive maintenance with the owner, protect
existing positions, and preserve monitor/close recovery. If safe maintenance
cannot preserve the directive, stop and obtain a scoped owner decision rather
than deploying a conflicting configuration.

## Regression source and limits of attribution

The owner-provided host snapshot reports `.env` execution=0, so the disabled
state is also persisted in host configuration; defaults alone are not the whole
cause. The previous Compose execution default was `0`; configuration accepted a healthy
closed dispatcher. The active fast-path rollout explicitly used a shell override
`BITGET_EXECUTION_ENABLED=0 docker compose up ...`, which takes precedence over
an execution=1 value in `.env`. Protection rollout and rollback docs repeated
that advice. That is a supported mechanism for the reported LIVE/LIVE/0 runtime,
but source inspection alone cannot identify which historical invocation created
those containers. The runtime verifier previously never checked this triplet.

This change does not edit `.env`, running containers, database or provider. An
already-running LIVE/LIVE/0 container remains unchanged until a separately
approved deployment corrects its effective environment. Tests are local policy
proof, not deployed health or live-order proof.
