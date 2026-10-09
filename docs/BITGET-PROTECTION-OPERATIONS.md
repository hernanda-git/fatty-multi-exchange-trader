# Bitget protection operations and controlled deployment

This runbook is for the existing Compose deployment at
`/home/valarion/apps/fatty-multi-exchange-trader`.

It is deliberately read-only with respect to Bitget orders/plans unless a separate
owner approval explicitly authorizes a canary. A code deployment is not permission
to place, cancel, close, or mutate a provider order.

A read-only Telegram snapshot is available through the authenticated private
operator bot command:

```text
/health
```

The command reports provider truth, DB ledger counts, provider-vs-DB drift,
active intents, fallback monitors, protection gates, kill-switch meaning, and
the latest source message. It does not place, cancel, close, or modify protection.

Interpretation rules:

- **Provider** is authoritative for current positions and pending orders.
- **DB drift** means local bookkeeping differs; it does not mean the provider is flat.
- **Execution ENABLED** means a dispatcher may submit; it does not prove a fill.
- **Kill switch ACTIVE** blocks new dispatches; it does not automatically close an
  existing position.
- **FALLBACK** means local protection is being relied on; verify stream freshness
  and the fallback mutation gate before assuming auto-close is active.

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

The permanent [production LIVE-only policy](PRODUCTION-LIVE-POLICY.md) supersedes
historical mutation-closed rollout instructions. Do not use DEMO or execution=0
shell overrides, or disable already-approved position protection during rollout.
Preserve independent safety gates and obtain a safe owner-approved maintenance
plan before a recreate. Validate rendered configuration before build/deployment:

```bash
bash scripts/check_production_live_policy.sh
```

Build/recreate only within that approved plan; this checker itself changes no
service, database or provider state.

Use `docker compose up -d --force-recreate`, not `docker compose restart`:
restart does not rebuild the image or re-read environment values. The migration
and init services are one-shot services and must exit `0`; they are not expected
to remain running.

Read the approval record and effective container flags first; production must
remain LIVE/LIVE/1. New protection mutation flags stay disabled until the
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
- no unexpected provider mutation; ordinary approved LIVE activity is not a fault;
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

### Protection-stream kill switch latched

The watchdog latches the `bitget-protection-stream` scope when the protection
socket is dead (or cannot report its state) for 3 consecutive cycles, or when a
provider protection read fails. This is a **dedicated** scope: it blocks new
entries only, and does not latch the venue-wide `bitget` scope, so existing
positions and fallback stops are not disabled.

- Confirm the latch reason and the scope in the watchdog report
  (`socket-not-connected` vs `protection-read-failed`).
- Read provider position/order/plan state through REST before acting.
- Do **not** clear the latch just because the socket has reconnected. The latch
  persists across restarts and must not auto-clear.
- Release only after the stream is genuinely healthy **and** a clean monitor
  report is obtained, with an explicit approval reference:

  ```bash
  # DEMO only — the script refuses to run with BITGET_MODE=LIVE.
  python scripts/recover_bitget_demo_kill_switch.py \
    --scope bitget-protection-stream \
    --approval-reference <approval-ticket>
  ```

- LIVE flat-baseline recovery is restricted to the exact two old reasons in
  section 12 below. Every other incident remains blocked; never directly clear
  database rows or use the DEMO recovery command on LIVE.

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

## 12. Approved LIVE flat-baseline latch recovery

`scripts/recover_bitget_live_latches.py` is an explicit operator-only release,
not an automatic recovery path. It releases only active `bitget` /
`clock-skew-exceeded` and `bitget-protection-stream` / `socket-not-connected`.
Any other active scope (including `global`) or reason refuses the entire operation.
Historical baselined trades remain unresolved history, not verified closures;
this procedure does not replay signals, change risk/environment settings, or issue
provider order, cancel, close, margin, or leverage mutations.

Before either proof or apply:

1. Obtain explicit recovery approval and the applied audited baseline receipt UUID
   for the expected authenticated Bitget account UID.
2. Stop **all account mutation consumers** (dispatch/execution workers, manual
   trading scripts, management/close/cancel consumers and external account writers).
   Keep them stopped through proof, review and apply. Database locks protect the
   local ledger only; the command cannot detect or prevent external writers.
   Observe-only monitor/notification consumers may remain running; a monitor
   changing a latch during proof causes refusal.
3. Run in the production environment with `TRADER_MODE=LIVE`, `BITGET_MODE=LIVE`,
   `BITGET_EXECUTION_ENABLED=1`, `BITGET_PROTECTION_STREAM_ENABLED=1`, stream mode
   `observe` and stream mutations disabled. Do not alter these settings to make
   the command pass. Normal credentials and `DATABASE_URL`/libpq `PG*` settings
   are reused. No secrets belong in the approval reference or command arguments.

```bash
# Default is dry-run, but explicit --confirm is required even for proof.
python scripts/recover_bitget_live_latches.py --dry-run --confirm \
  --approval-reference <recovery-approval> --account-id <authenticated-UID> \
  --baseline-id <applied-baseline-UUID>

# Review the proof, including exact latch rows, receipt/account binding,
# ledger_readiness_issues=[], all-products/all-families flat GET inventory,
# and authenticated CONNECTED V2 stream / both-leg pong / BTCUSDT mark proof.
# Use precisely the same approval, account and baseline values:
python scripts/recover_bitget_live_latches.py --apply --confirm \
  --approval-reference <recovery-approval> --account-id <authenticated-UID> \
  --baseline-id <applied-baseline-UUID> \
  --expected-digest <reviewed-recovery_digest>
```

The command performs fresh authenticated GET inventory for USDT, USDC and COIN
futures positions, ordinary orders and `normal_plan`, `profit_loss`, `track_plan`;
unknown responses or any exposure/pending orders refuse. It opens production V2
public/private sockets, authenticates privately, and requires fresh public BTCUSDT
marks and actual pongs on both legs (bounded initial proof: 15 seconds). The probe
uses a one-second diagnostic heartbeat only; it does not change service timing.

Under database write locks it compares every field of the observed active latch
rows, validates the baseline receipt binding, repeats existing ledger-readiness
checks and rejects all unbaselined unresolved commitments/open positions/leases.
It then repeats flat GET evidence with the stream still observed. Apply requires
the reviewed digest to match the exact latch snapshot/baseline/account/approval.
Both latch releases and per-scope audited `kill-switch-release` notification
outbox events commit together. Receipt, prior latch rows, raw provider inventory,
stream proof and approval are retained in those events. Dry-run rolls back all
database work; successful output has `applied=false`. Apply must report
`applied=true` and a `recovery_id`; refusals exit 2 without a release.

After successful apply, preserve the existing risk/stream flags and restart only
the approved consumers for **future fresh signals**. Confirm notifications and
current health using the normal operational process. If the monitor re-latches,
stop and investigate the new evidence; do not retry a release blindly. Any
refusal leaves execution blocked and requires addressing the stated cause, not
broadening the allowlist.
