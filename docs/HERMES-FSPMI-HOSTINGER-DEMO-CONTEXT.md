# Hermes Context: Fatty Bitget DEMO on `fspmi-hostinger`

**Purpose:** canonical handoff for Hermes sessions running on the deployment host.
This document contains operational context and evidence, never credentials.

**Last verified:** 2026-09-06
**Remote host:** `fspmi-hostinger`
**Remote application:** `/home/valarion/apps/fatty-multi-exchange-trader`
**Remote demo env:** `/home/valarion/apps/fatty-multi-exchange-trader/.env.bitget-demo`
**Local source worktree:** `C:\Workspace\bots\fatty-bitget-live`

## Non-negotiable safety rules

- Use `.env.bitget-demo` only for Bitget DEMO checks. Never merge it into `.env`.
- Never print, copy into documentation, commit, or log API keys, secrets, passphrases, Telegram tokens, database passwords, or Codex auth.
- `BITGET_EXECUTION_ENABLED=0` remains mandatory. Do not enable live execution.
- Do not clear the Bitget kill switch without reconciliation evidence and explicit authorization.
- Do not submit an order until the user explicitly authorizes the bounded DEMO lifecycle.
- Preserve the existing Compose project, PostgreSQL volume, data, and rollback path.
- Take a PostgreSQL backup before any deployment/rebuild/restart that changes the running stack.
- Treat the running remote image and the local worktree as separate evidence tracks.

## Current remote credential evidence

The user authorized copying the filled local DEMO env to the remote host. It was copied to:

```text
/home/valarion/apps/fatty-multi-exchange-trader/.env.bitget-demo
```

Verified remotely:

```text
BITGET_MODE=DEMO
BITGET_API_KEY       present, value redacted
BITGET_API_SECRET    present, value redacted
BITGET_API_PASSPHRASE present, value redacted
file mode: 0600
```

Never read these values into chat or reports.

## Remote DEMO account evidence

A transient read-only container was run with `.env` for infrastructure interpolation and `.env.bitget-demo` for the demo credentials. The official Bitget `paptrading: 1` header was applied to the authenticated request.

Verified response:

```text
available=100
usdtEquity=100
crossedMaxAvailable=100
isolatedMaxAvailable=100
marginMode=crossed
posMode=hedge_mode
```

No order was submitted. No position was created. The `100 USDT` balance is visible on the correct DEMO account.

## Current blockers before DEMO mutation

The account is funded but its account modes are still incompatible with the bot contract:

```text
required marginMode: isolated
actual marginMode:   crossed
required posMode:    one_way
actual posMode:      hedge_mode
```

The user must set the DEMO USDT-M futures account to **Isolated** margin and **One-way** position mode, or Hermes must use an explicitly reviewed, tested, authorized account-mode mutation path. Do not infer that `isolatedMaxAvailable=100` means the account is already isolated; the authoritative `marginMode` is still `crossed`.

## Remote image/source mismatch

The current running/deployable remote image predates the local DEMO mode implementation. Evidence:

- The remote `BitgetRestClient` rejects `mode=DEMO` as an unknown constructor argument.
- The old image therefore cannot be used as proof of the new `DEMO`/`LIVE` mode contract.
- A transient monkey-patched read with the official DEMO header succeeded after the demo env was copied, proving the credentials/environment now match the DEMO account.
- The local source contains the proper mode boundary and public/private header behavior, but those changes have not yet been deployed.

Do not claim the remote production stack has the new mode contract until a backup-first rollout and post-deploy read-back are complete.

## Local implementation state

The local worktree contains the mode migration and related fixes:

- Accepted runtime modes are `DEMO` and `LIVE`; `PAPER` is removed from active source/configuration.
- Compose defaults use `TRADER_MODE=DEMO` and `BITGET_MODE=DEMO`.
- DEMO private requests use `paptrading: 1`; public server-time requests omit it.
- Bitget metadata accepts current V2 fields `maxOrderQty`/`maxMarketOrderQty`, with a non-empty `maxPositionNum` fallback.
- The ignored local template is `C:\Workspace\bots\fatty-bitget-live\fatty-bitget-demo.env`.
- The remote deployment has not yet received these source changes.

Local verification already completed:

```text
pytest: 275 passed
ruff check: passed
ruff format --check: passed
mypy src/fatty_trader --ignore-missing-imports: no issues in 60 files
git diff --check: passed
```

## Validated local DEMO read-only probe

Using the filled local DEMO env without printing values:

```json
{
  "account": "PASS",
  "contracts": "PASS",
  "fills": "PASS",
  "open_orders": "PASS",
  "positions": "PASS, count=0",
  "server_time": "PASS",
  "ok": true
}
```

Representative local metadata was resolved for `BTCUSDT`, `ETHUSDT`, and `XRPUSDT`.

## Required next sequence

### A. Re-read account mode after the user changes it

Run from the remote application directory with the demo env layered after the production env only for Compose interpolation:

```bash
cd /home/valarion/apps/fatty-multi-exchange-trader

docker compose --env-file .env --env-file .env.bitget-demo \
  run --rm --no-deps \
  --entrypoint /app/.venv/bin/python dispatcher-bitget \
  /app/scripts/bitget_api_probe.py --json
```

The shipped remote probe is stale until the new source is deployed. Before deployment, do not treat it as proof of DEMO mode. For a temporary account read only, use the current image with the official `paptrading: 1` header; do not use this workaround for orders.

Acceptance before mutation:

```text
available > 0
usdtEquity > 0
marginMode = isolated
posMode = one_way
positions = 0
open orders = 0
```

### B. Prepare and deploy the local source safely

1. Re-derive `git status`, branch, diff, and remote ancestry in the local worktree.
2. Keep the ignored local demo env out of Git.
3. Run full local tests and quality checks again.
4. Commit only claimed source/docs/tests/scripts; never stage `.env` or `.hermes/` accidentally.
5. Push the source branch.
6. On `fspmi-hostinger`, make a fresh PostgreSQL backup with the existing backup script and verify non-zero size.
7. Fast-forward/deploy the existing Compose project in place; do not recreate volumes or replace the project.
8. Keep `.env` (production) unchanged. Keep `.env.bitget-demo` separate.
9. Read back image SHA, service health, migrations, execution gate, kill switch, and telemetry.
10. Run the new DEMO read-only probe inside the deployed image.

### C. Bounded DEMO lifecycle, only after explicit authorization

The lifecycle must be one bounded order at a time and must include:

1. account/mode read-back;
2. dynamic symbol metadata and price validation;
3. risk sizing and minimum notional validation;
4. durable intent before POST;
5. smallest permitted DEMO entry;
6. provider order/fill read-back;
7. native SL/TP placement and read-back;
8. position and protection reconciliation;
9. restart/unknown-submit recovery without duplicate POST;
10. pending limit/cancel read-back;
11. reduce-only close;
12. zero-position and zero-order verification;
13. Telegram outbox delivery/read-back and retry/lease drill;
14. kill-switch safety tests and evidence-based recovery.

No LIVE order or LIVE cutover is part of this sequence.

## Production invariants to re-check after any rollout

```text
BITGET_EXECUTION_ENABLED=0
BITGET_CANARY_MAX_ORDERS=0
live order intents=0 before DEMO mutation
production .env unchanged
PostgreSQL volume/data preserved
kill switch remains latched until authorized recovery
Telegram heartbeat remains 21600 seconds
Codex analyzer status reported separately from host Codex status
```

## Documentation source map

The `docs/hermes-context/` directory on the remote host contains snapshots of:

- this handoff;
- the full project readiness report;
- Bitget operations;
- exchange contracts;
- go-live and operations docs;
- implementation status;
- the Bitget DEMO readiness plan.

The plan is historical planning context. This handoff is the current operational source for the funded remote DEMO state.

## Related Hermes skills installed for this workspace

The remote Hermes skill set should include:

- `fatty-bitget-live` — project-specific Bitget deployment, safety, telemetry, and lifecycle workflow;
- `crypto-auto-trader-reliability` — exchange filters, sizing, idempotency, protection, reconciliation;
- `trading-bot-deploy-ops` — safe deployment and rollback practices;
- `agentic-trading-bot-stewardship` — human-in-the-loop and no-autonomous-live rules;
- `deployed-app-debugging` — remote logs, auth probes, and evidence discipline;
- `project-documentation` — evidence-based documentation and secret hygiene;
- `hermes-agent` — Hermes remote skill/configuration conventions.

Hermes must load the relevant skill before modifying the remote deployment or execution path.
