# Fresh Hermes Session Prompt — Fatty Bitget DEMO on fspmi-hostinger

You are continuing work on the existing `fatty-multi-exchange-trader` deployment. This is a safety-critical trading system. Use the repository source, live remote evidence, and the context documents below as the source of truth. Do not guess and do not repeat historical work that is already verified.

## Mission

Continue the controlled Bitget DEMO readiness track on `fspmi-hostinger`.

The immediate goal is **not LIVE trading**. The goal is to:

1. verify the funded Bitget DEMO account state;
2. ensure the account uses the bot-required modes;
3. safely deploy the local `DEMO`/`LIVE` mode implementation to the existing Compose deployment after a PostgreSQL backup;
4. run a sanitized DEMO read-only probe inside the deployed image;
5. only after explicit user authorization, execute the bounded DEMO lifecycle with complete read-back, protection, reconciliation, telemetry, and rollback evidence.

Never enable LIVE execution in this session.

## Mandatory first reads

Working directory:

```text
/home/valarion/apps/fatty-multi-exchange-trader
```

Read these files first:

```text
./docs/hermes-context/HERMES-CONTEXT-BUNDLE-README.md
./docs/hermes-context/HERMES-FSPMI-HOSTINGER-DEMO-CONTEXT.md
./docs/hermes-context/BITGET-LIVE-OPERATIONS.md
./docs/hermes-context/EXCHANGE-CONTRACTS.md
./docs/hermes-context/GO-LIVE.md
./docs/hermes-context/OPERATIONS.md
./docs/hermes-context/IMPLEMENTATION-STATUS.md
./.hermes/plans/2026-09-06_145742-bitget-demo-go-live-readiness-snapshot.md
```

Then inspect the actual source and runtime state. Code and live read-back outrank stale documentation.

## Mandatory Hermes skills

Load these skills before taking action:

```text
fatty-bitget-live
crypto-auto-trader-reliability
trading-bot-deploy-ops
agentic-trading-bot-stewardship
deployed-app-debugging
project-documentation
hermes-agent
```

## Safety contract

- Never print or store `BITGET_API_KEY`, `BITGET_API_SECRET`, `BITGET_API_PASSPHRASE`, Telegram tokens, PostgreSQL passwords, Codex auth, cookies, signatures, or authorization headers.
- Never put secret values in Markdown, logs, commits, shell history, chat, or reports.
- The DEMO credential file is:

  ```text
  /home/valarion/apps/fatty-multi-exchange-trader/.env.bitget-demo
  ```

  It is separate from production `.env` and must remain mode `0600`.
- Production `.env` must not be overwritten, merged, or repurposed.
- `BITGET_EXECUTION_ENABLED=0` must remain enforced.
- Do not enable LIVE execution or perform a LIVE cutover.
- Do not clear the Bitget kill switch without evidence, authorization, and read-back.
- Do not submit even a DEMO order until the user explicitly authorizes the bounded DEMO lifecycle in the current session.
- Preserve the existing Compose project, PostgreSQL volume, database data, and rollback path.
- Before any deployment/rebuild/restart that changes the running stack, create and verify a non-empty PostgreSQL backup.
- Use specific Git paths; never stage `.env`, `.env.*`, credentials, `.hermes` internal files, or unrelated WIP.

## Verified current evidence

The user authorized copying the filled local DEMO env to the host. The remote file is present at mode `0600`.

A transient remote authenticated read using the official Bitget DEMO header `paptrading: 1` returned:

```text
available=100
usdtEquity=100
crossedMaxAvailable=100
isolatedMaxAvailable=100
marginMode=crossed
posMode=hedge_mode
```

No order or position was created.

The funded DEMO account therefore exists and the credentials/environment match DEMO, but account modes are not yet compatible with the bot contract.

Required account state:

```text
marginMode=isolated
posMode=one_way
```

Current account state:

```text
marginMode=crossed
posMode=hedge_mode
```

Do not infer that `isolatedMaxAvailable=100` means the account is isolated. Use the authoritative `marginMode` field.

The current remote image is older than the local source implementation:

- the old remote `BitgetRestClient` does not accept `mode=DEMO`;
- a remote probe that does not apply `paptrading: 1` is not valid DEMO evidence;
- the local source contains the new explicit `DEMO`/`LIVE` mode contract, public/private header handling, and current Bitget metadata compatibility fixes;
- those local source changes have not yet been deployed to the remote Compose stack.

## Local implementation already completed

The local worktree at `C:\Workspace\bots\fatty-bitget-live` contains:

- accepted runtime modes restricted to `DEMO` and `LIVE`;
- removal of active `PAPER` mode references from source/configuration/tests/docs touched by the change;
- Compose defaults changed to DEMO;
- DEMO private requests using `paptrading: 1`;
- public server-time requests omitting the DEMO header;
- current Bitget V2 metadata compatibility for `maxOrderQty`, `maxMarketOrderQty`, and non-empty `maxPositionNum` fallback;
- sanitized DEMO probe support;
- updated tests and documentation.

Local verification:

```text
pytest: 275 passed
ruff check: passed
ruff format --check: passed
mypy src/fatty_trader --ignore-missing-imports: no issues in 60 files
git diff --check: passed
```

Re-check Git state before committing. Do not assume the current working tree is clean.

## Required next action: account-mode read-back

First ask the user to set the Bitget DEMO USDT-M futures account to:

```text
Margin: Isolated
Position mode: One-way
```

If the user says it is done, read back the account from the remote host using the demo environment. Do not print secrets.

The accepted pre-mutation state is:

```text
available > 0
usdtEquity > 0
marginMode=isolated
posMode=one_way
positions=0
open_orders=0
```

If either mode is wrong, stop and report the exact blocker. Do not silently mutate the account mode unless the user explicitly authorizes that operation and the code path is verified and safe.

## Safe remote probe pattern

Run from the remote application directory. Use `.env` only for Compose interpolation and `.env.bitget-demo` for DEMO values. Never print env values.

After the updated image is deployed, use the image's probe:

```bash
cd /home/valarion/apps/fatty-multi-exchange-trader

docker compose --env-file .env --env-file .env.bitget-demo \
  run --rm --no-deps \
  --entrypoint /app/.venv/bin/python dispatcher-bitget \
  /app/scripts/bitget_api_probe.py --json
```

The probe must prove:

- server time;
- authenticated account;
- USDT-FUTURES contracts;
- representative symbol metadata;
- positions;
- open orders;
- fills;
- no secret leakage;
- provider environment is DEMO;
- no POST/order mutation occurred.

Do not treat the old remote probe as valid proof of DEMO mode until the updated image is deployed.

## Deployment plan, only after source review and user continuation

Do not deploy blindly. First:

1. inspect `git status --short`, branch, remote ancestry, and diff;
2. review changed-file scope and secret scan;
3. run the full local test and quality gates;
4. commit only the intended source/docs/tests/scripts with a conventional message;
5. push the branch or approved fast-forward path;
6. on `fspmi-hostinger`, run the existing PostgreSQL backup script;
7. verify the backup exists and is non-empty;
8. deploy in place using the existing Compose project;
9. do not recreate or delete PostgreSQL volumes;
10. leave production `.env` unchanged;
11. keep `BITGET_EXECUTION_ENABLED=0`;
12. read back image/source SHA, service health, migrations, execution gate, kill switch, and notification state;
13. run the new DEMO read-only probe inside the deployed image.

Separate these evidence tracks:

```text
LOCAL_TESTS
SOURCE_COMMIT
REMOTE_DEPLOYMENT
DEPLOYED_IMAGE
DEMO_AUTH_READ
DEMO_METADATA
DEMO_MUTATION
TELEGRAM_DELIVERY
CODEX_ANALYZER
ROLLBACK_BACKUP
LIVE_CUTOVER
```

Never upgrade local PASS into deployed PASS without remote read-back.

## DEMO lifecycle gate

Only after:

- account balance is positive;
- margin mode is isolated;
- position mode is one-way;
- updated image is deployed and read-only probe is green;
- database backup and rollback evidence exist;
- kill switch state is understood;
- user explicitly authorizes DEMO mutation;

run one bounded DEMO lifecycle at a time:

1. account and mode read-back;
2. dynamic symbol contract lookup;
3. price and metadata read-back;
4. deterministic quantity/price/min-notional validation;
5. durable intent persistence before POST;
6. smallest permitted DEMO market entry;
7. provider order ID read-back;
8. fill quantity, average price, fee, and fill ID read-back;
9. native SL/TP placement;
10. native SL/TP read-back;
11. position and liquidation-price read-back;
12. restart/unknown-submit reconciliation without duplicate POST;
13. pending limit order and cancel read-back;
14. reduce-only close;
15. zero position, zero order, and zero protection residual verification;
16. database intent/fill/position read-back;
17. Telegram durable event/outbox delivery verification;
18. retry/lease failure drill;
19. safety/kill-switch tests;
20. evidence-based recovery only if separately authorized.

Use real dynamic symbols from the Bitget catalogue, not only hardcoded BTCUSDT. At minimum cover high-price, mid-price, low-price, and unusual contract shapes where available. Every symbol must pass actual metadata, precision, min quantity, min notional, contract value, leverage, sizing, and risk gates before any provider POST.

## Codex requirement

Codex text+image analysis is mandatory for random/unstructured Telegram messages in the intended production path.

Do not silently replace it with deterministic fallback.

Before claiming Codex readiness, prove inside the running analyzer container:

```text
codex executable discoverable and executable
auth available through approved isolated mechanism
controlled text probe succeeds
controlled image probe succeeds
failure and fallback telemetry is explicit
```

Host-side Codex status does not prove analyzer-container Codex status.
Never copy OAuth/auth files into Git or documentation.

## Telegram and operational telemetry

Maintain the durable event chain:

```text
telegram_messages
  -> canonical_signals
  -> dispatches
  -> live_order_intents
  -> notifications_outbox
```

Verify event delivery by database read-back and sender state. Do not treat a log line as proof Telegram delivery. Preserve HTML-safe formatting, literal newlines, redaction, correlation IDs, dispatch/intent identifiers, provider status, and state transitions. Heartbeat cadence remains 21600 seconds (6 hours), not one minute.

## Rollback and stop conditions

Stop and report an evidence-based blocker if:

- DEMO account mode is wrong;
- demo provider returns `40099`;
- remote image is stale or source lineage is unclear;
- backup is missing/empty;
- provider read-back disagrees with local state;
- Codex cannot be verified inside analyzer;
- notification delivery is not durable;
- kill-switch reason is unexplained;
- any step would require enabling LIVE;
- any operation would expose or copy a secret unsafely.

Never fabricate a green status. Report exactly what passed, what is blocked, and the next user action.

## Expected first response from this fresh session

After loading the required skills and reading the context bundle:

1. report the current remote account mode read-back;
2. state whether `marginMode=isolated` and `posMode=one_way` are satisfied;
3. if not satisfied, stop and ask the user to correct those two DEMO account settings;
4. do not deploy or submit orders before that gate is green.
