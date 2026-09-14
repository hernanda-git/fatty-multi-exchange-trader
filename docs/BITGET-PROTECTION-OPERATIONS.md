# Bitget protection operations and controlled deployment

This runbook is for the existing Compose deployment at
`/home/valarion/apps/fatty-multi-exchange-trader`.

It is deliberately read-only with respect to Bitget orders/plans unless a separate
owner approval explicitly authorizes a canary. A code deployment is not permission
to place, cancel, close, or mutate a provider order.

## 1. Evidence tracks

Report these tracks separately:

- **LOCAL_TESTS**: pytest, Ruff, mypy, compileall, diff checks.
- **IMAGE_BUILD**: Docker build completed from the committed revision.
- **DEPLOYED_SCHEMA**: migration container exited successfully and the database
  reports the expected migration versions/tables.
- **SERVICE_RUNTIME**: running containers, health checks, source revision, and
  effective non-secret flags.
- **LIVE_ACCOUNT_READ**: authenticated Bitget account, contracts, positions,
  open orders, fills, and server time; GET-only.
- **BACKUP_ROLLBACK**: non-empty PostgreSQL custom-format backup and a documented
  restore command.
- **LIVE_ORDER_SMOKE**: intentionally absent unless a separate explicit canary
  authorization is recorded. A green local test is not an order smoke test.

Never upgrade a local test or a healthy container into live-protection proof.

## 2. Pre-commit checks

Run from the repository root:

```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run python -m compileall -q src tests
git diff --check
docker compose config --quiet
```

Inspect the diff and ensure:

- no credential, token, or `.env` value is staged;
- only intended source, test, fixture, Compose, lockfile, and documentation paths
  are staged;
- execution, fallback mutation, operator mutation, stream mutation, and canary
  defaults remain closed;
- no historical WLD signal or liquidation is replayed;
- no provider mutation was used as a test or verification shortcut.

## 3. Read-only pre-deploy snapshot

The snapshot must be captured immediately before replacing the image. Use the
repository's existing read-only script when available:

```bash
uv run python scripts/agentic_ops_snapshot.py
```

Record, without credentials:

- current git revision and branch;
- Compose service state;
- current `TRADER_MODE` and `BITGET_MODE` values;
- presence and values of safety flags, excluding secrets;
- Bitget server time/readiness result;
- available/equity balance;
- contract count;
- positions, open orders, and fills count;
- kill-switch state;
- unresolved local intents/reservations;
- current database migration versions.

A provider error, unknown position, unresolved intent, or non-empty unexpected
position is a rollout blocker. Do not “clean up” rows to make the snapshot green.

## 4. PostgreSQL backup

Run on the host, not inside the application container:

```bash
/bin/bash scripts/backup_postgres.sh
```

Require output shaped like:

```text
backup_created=backups/fatty_trader_<UTC>.dump bytes=<positive>
restore_command=CONFIRM_RESTORE=YES scripts/restore_postgres.sh backups/fatty_trader_<UTC>.dump
```

Verify the file is non-empty and keep its path with the deployment record. Do not
commit the dump. Restore is destructive and requires an explicit confirmation:

```bash
CONFIRM_RESTORE=YES scripts/restore_postgres.sh backups/fatty_trader_<UTC>.dump
```

Use restore only against the intended database after stopping writers and checking
the backup path. Never overwrite a production database from an unverified dump.

## 5. Commit and push

Stage specific paths; do not use `git add -A` because internal plans, runtime
artifacts, or credentials can be picked up accidentally.

```bash
git add README.md docs/ docker-compose.yml pyproject.toml uv.lock \
  src/ tests/
git diff --cached --check
git status --short
git commit -m "feat: harden Bitget position protection"
git push origin feat/bitget-protection-ws-hardening
git status --short --branch
git log -1 --oneline --decorate
```

After pushing, verify the remote branch points to the commit:

```bash
git ls-remote --heads origin feat/bitget-protection-ws-hardening
```

Do not push credentials, PostgreSQL dumps, runtime auth files, or generated
artifacts. The commit must contain the full protection documentation.

## 6. Controlled Compose deployment

The working directory is the deployment target for this existing Compose stack.
Do not change the database volume, project name, credentials, or target host.

During rebuild/recreate, explicitly close every provider mutation path. Shell
assignments override `.env` for this command without rewriting the secret file:

```bash
BITGET_EXECUTION_ENABLED=0 \
BITGET_FALLBACK_MUTATIONS_ENABLED=0 \
BITGET_OPERATOR_MUTATIONS_ENABLED=0 \
BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED=0 \
BITGET_PROTECTION_CAPABILITY_GATE_ENABLED=0 \
BITGET_PROTECTION_STREAM_ENABLED=0 \
docker compose build dispatcher-bitget monitor-bitget migrate init

BITGET_EXECUTION_ENABLED=0 \
BITGET_FALLBACK_MUTATIONS_ENABLED=0 \
BITGET_OPERATOR_MUTATIONS_ENABLED=0 \
BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED=0 \
BITGET_PROTECTION_CAPABILITY_GATE_ENABLED=0 \
BITGET_PROTECTION_STREAM_ENABLED=0 \
docker compose up -d --force-recreate migrate init dispatcher-bitget monitor-bitget
```

Use `docker compose up -d --force-recreate`, not `docker compose restart`:
restart does not rebuild the image or re-read environment values. The migration
and init services are one-shot services and must exit `0`; they are not expected
to remain running.

If the stack's existing environment deliberately has an already-approved legacy
execution gate, do not silently restore it. Read the approval record and effective
container flags first. New protection mutation flags stay disabled until the
fallback close path and provider WebSocket compatibility have an independent
approval.

## 7. Post-deploy read-only verification

First inspect service state and completed one-shot jobs:

```bash
docker compose ps

docker compose ps -a migrate init
```

Require:

- `postgres`, `dispatcher-bitget`, and `monitor-bitget` running/healthy;
- `migrate` and `init` exited with code `0`;
- no restart loop or traceback;
- no provider POST/cancel/close log line;
- image was rebuilt from the pushed commit.

Read the effective flags without printing secrets:

```bash
docker compose exec -T dispatcher-bitget sh -lc \
  'printf "execution=%s capability_gate=%s mode=%s canary_cap=%s approval=%s skew=%s\\n" \
  "$BITGET_EXECUTION_ENABLED" "$BITGET_PROTECTION_CAPABILITY_GATE_ENABLED" \
  "$BITGET_MODE" "$BITGET_CANARY_MAX_ORDERS" \
  "${BITGET_APPROVAL_REFERENCE:+set}" "$BITGET_MAX_CLOCK_SKEW_MS"'

docker compose exec -T monitor-bitget sh -lc \
  'printf "fallback=%s stream=%s stream_mode=%s stream_mutations=%s watchdog=%s\\n" \
  "$BITGET_FALLBACK_MUTATIONS_ENABLED" "$BITGET_PROTECTION_STREAM_ENABLED" \
  "$BITGET_PROTECTION_STREAM_MODE" "$BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED" \
  "$BITGET_PROTECTION_REST_WATCHDOG_SECONDS"'
```

Check the deployed schema:

```bash
docker compose exec -T postgres psql -U fatty_app -d fatty_trader -Atc \
  "SELECT string_agg(version::text, ',' ORDER BY version) FROM schema_migrations;"
docker compose exec -T postgres psql -U fatty_app -d fatty_trader -Atc \
  "SELECT to_regclass('public.bitget_protection_capabilities'), to_regclass('public.provider_reconciliation_events');"
```

Run the existing runtime verifier:

```bash
/bin/bash scripts/verify_bitget_runtime.sh
```

The verifier must be interpreted as read-only evidence only. It does not prove
that a native plan exists for every symbol, that the WebSocket endpoint is
compatible, or that an order can safely be placed.

Then read back provider state through the authenticated probe/helper, without
printing credentials:

```bash
docker compose exec -T dispatcher-bitget \
  /app/.venv/bin/python scripts/bitget_api_probe.py --json
```

Record account/position/order/fill results and compare them to the pre-deploy
snapshot. A discrepancy is a blocker, not a reason to release a gate.

## 8. Rollback

Rollback has two independent parts:

### 8.1 Application rollback

1. Keep all mutation gates closed.
2. Save the failing container logs and migration output.
3. Check out the last known-good pushed commit or deploy the prior image tag.
4. Rebuild/recreate only the affected services.
5. Re-run schema/runtime/provider GET verification.
6. Do not revert migrations by dropping tables. Migrations 11 and 12 are additive;
   retain their tables and restore code compatibility before considering data
   recovery.

### 8.2 Database recovery

Use the verified custom-format backup only when application rollback cannot restore
consistent state. Stop writers, confirm the target database and backup path, obtain
explicit destructive-restore confirmation, restore, then run migrations and read-only
verification again. Preserve the failed database/container evidence before restore.

## 9. Canary acceptance criteria

A LIVE canary is a separate, explicitly authorized operation. It is not part of
this deployment. Before it can be considered, all of the following must be true:

- a named approval reference is recorded;
- provider server time, account, contracts, positions, orders, and fills read
  cleanly;
- no unresolved local intent/reservation exists;
- the exact symbol is present in the target LIVE contract set;
- the symbol capability is environment-matched and `VERIFIED` native protection
  or an explicitly approved, fresh fallback lane;
- native placement request and response match the documented contract;
- provider read-back proves both plans, IDs/OIDs, mark trigger, execution mode,
  side, and confirmed quantity;
- global order cap is positive and bounded to the approved value;
- fallback and operator mutation gates have separate approvals;
- the canary is at most one intentionally selected entry and is never a replay of
  WLD or any historical liquidation;
- after the canary, provider and local lifecycle are read back to terminal state;
- any unknown provider result closes the execution gate and enters reconciliation.

If any criterion fails, keep execution closed. Do not blind-retry a POST.

## 10. Incident response

### Stale WebSocket

- Keep new entries blocked for the affected symbol.
- Confirm the process is reconnecting and not spinning.
- Compare per-symbol last mark event time with the watchdog report.
- Read provider position/order/plan state through REST.
- Do not call a close/cancel merely because the socket is stale.
- If provider state is unknown, preserve the intent as unknown/closing and reconcile.

### Missing native plan

- Treat provider read-back as failed; do not infer protection from the POST ack.
- Do not latch a global kill switch solely because one symbol lacks a native plan.
- Allow the symbol only if its fallback capability is explicitly allowed and fresh.
- Otherwise block that symbol and alert the operator.
- Never replay the entry to “fix” missing protection.

### Unknown provider order result

- Keep the durable intent and deterministic OID.
- Read order detail, matching fills, current position, and protection.
- Do not issue a second entry or close POST until provider state is known.
- If the position is open but unprotected, use the approved containment path once;
  retain the claim and reconcile the result.

### Provider/system liquidation

- Treat `SYS`/`burst_*` evidence as provider liquidation, not a new signal.
- Reconcile the exact provider fill once into `provider_reconciliation_events`.
- Preserve fee, realized PnL, order/fill IDs, and timestamps.
- Do not delete historical intent/order/fill rows and do not replay the signal.
- Compare the liquidation distance to the configured liquidation buffer and record
  the incident separately from code deployment.

## 11. No-mutation guarantee for this rollout

The following operations are intentionally absent from the deployment procedure:

- provider order placement;
- native SL/TP placement or replacement;
- provider close or cancel;
- LIVE canary;
- database deletion or destructive migration;
- replay of historical signals.

The only expected state-changing operations are local image/container replacement,
additive database migrations, capability/ledger reconciliation writes, and service
health telemetry. Any provider mutation in logs during this procedure is a rollout
failure and must stop the deployment.
