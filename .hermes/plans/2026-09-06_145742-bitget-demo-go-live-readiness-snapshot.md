# Bitget Demo Readiness and Controlled Go-Live Plan

> **For Hermes:** Execute this plan only after the user fills the demo credential template and explicitly asks to continue. Do not enable live execution or clear safety controls automatically.

**Goal:** Restore the previous Codex text+image analysis path, establish a correctly isolated Bitget demo environment, prove the complete demo execution lifecycle and operational controls, and prepare—but do not perform—the eventual bounded live cutover.

**Architecture:** Keep `fspmi-hostinger` and its existing PostgreSQL/Compose deployment as the production system of record. Add or restore demo configuration as an explicitly isolated provider environment, not by repurposing mainnet credentials. Keep the global topology PAPER and keep `BITGET_EXECUTION_ENABLED=0` until demo evidence, safety recovery, explicit approval, and the user's final cutover instruction are complete.

**Tech stack:** Python 3.11/uv, Docker Compose, PostgreSQL 16, `httpx`, Bitget V2 REST, Telethon, Telegram Bot API HTML notifications, Codex CLI/authentication, and the existing durable dispatch/intent/protection/reconciliation components.

---

## User decisions captured verbatim

1. **Point 1:** Skip Telegram-token rotation for now; update documentation and mark it solved/waived for this readiness track. Do not re-open it unless the user asks.
2. **Point 2:** Resolve the Bitget demo error. Prepare a Windows credential template for the user to fill. Do not guess the demo API mechanism or silently mix demo credentials with mainnet credentials.
3. **Point 3:** After the completed demo credential template is supplied, run the full demo lifecycle, including dynamic-pair testing and all safety/read-back checks.
4. **Point 4:** Make the kill-switch/recovery work complete, but only through evidence-backed reconciliation and an authorized recovery path. Never clear it merely to make a test pass.
5. **Point 5:** Codex subscription/Codex execution is mandatory for random, unstructured, text+image Telegram signals. The deterministic parser is not the approved production analysis path for this requirement. Restore the previous `fspmi-hostinger` Codex wiring instead of replacing it with a weaker fallback.
6. **Point 6:** Dynamic-pair coverage belongs inside Point 3's demo verification.
7. **Point 7:** Do not use a PAPER soak as the primary readiness gate. Use the actual Bitget demo account/environment once it is correctly wired.
8. **Point 8:** Include Telegram outage/retry/lease/failure recovery in the Point 3 testing plan.
9. **Point 9:** Prepare the rollback procedure and evidence package before any cutover.
10. **Point 10:** Live cutover remains explicitly held for the user's later instruction. No live enablement is part of this plan execution.

---

## Verified current context and constraints

Evidence captured from the running deployment before this plan was written:

```text
Host:                         fspmi-hostinger
Application:                  /home/valarion/apps/fatty-multi-exchange-trader
Production source SHA:        e8838977c988ee5609572be41d2dd4fa4cf307c1
Compose services:             8/8 running and healthy
TRADER_MODE:                  PAPER
BITGET_MODE:                  LIVE
BITGET_EXECUTION_ENABLED:     0
TELEGRAM_HEARTBEAT_SECONDS:   21600
Schema migrations:            1,2,3,4,5
telegram_messages:            4
canonical_signals:             1
dispatches:                   2 (1 QUEUED, 1 REJECTED)
live_order_intents:            0
fills:                         0
positions:                     0
notifications_outbox:          16 total, 16 sent, 0 failed
kill switch:                   active, provider-orders-invalid
analyzer:                      codex_cli=unavailable, codex_account=UNCONFIGURED
```

The current source code confirms:

- `BITGET_MODE` is currently accepted as `PAPER` or `LIVE`; there is no confirmed `DEMO` mode in `service_config`.
- `BITGET_EXECUTION_ENABLED` defaults to `0` in Compose.
- The Bitget REST client currently uses `https://api.bitget.com` as its default base URL and does not, from the inspected source alone, prove a separate demo-environment header or endpoint.
- The analyzer image mounts `/app/runtime/codex-home` from `./data/codex-home` and `/app/runtime/media` from `./data/media`, but the running analyzer reports no `codex` executable and an unconfigured account.
- The `notification-sender` uses Telegram HTML mode and a durable PostgreSQL outbox.

These facts mean the demo template must not invent unsupported variables such as `BITGET_DEMO_*`, `BITGET_ENVIRONMENT`, or a demo URL until the provider implementation and the previous host wiring are inspected and selected deliberately.

---

## Phase 0 — documentation status update

**Purpose:** Record Point 1 as waived for this track and update the canonical full report without performing unrelated work.

### Planned files

- Modify: `docs/FULL-REPORT-fatty-multi-exchange-trader-20260906.md`
- Optionally modify: `docs/BITGET-LIVE-OPERATIONS.md` if the demo procedure is clarified there.

### Required documentation changes

- Mark Telegram-token rotation as **waived by explicit user decision for this readiness track**, not as technically safe or permanently resolved.
- State that no token rotation will be performed in the demo-readiness sequence.
- Preserve the warning that the token was historically exposed and that rotation remains a separate security recommendation.
- Change the readiness list so Point 1 is not counted as a blocker for the user's requested demo track.
- Keep the production SHA separate from later docs-only commits.

### Verification

- No token value, chat ID, API key, passphrase, or session material appears in the document.
- `git diff --check` passes.
- The documentation change is committed and pushed separately.
- No deployment is performed for a docs-only change unless explicitly requested.

---

## Phase 1 — inspect and define the real Bitget demo contract

**Purpose:** Resolve the `40099: exchange environment is incorrect` error without guessing.

### Read-only investigation first

1. Inspect the complete current Bitget client, probe, metadata, execution, and Compose configuration.
2. Inspect the previous deployment history and host files for the earlier demo wiring, without printing secrets.
3. Determine whether the intended Bitget demo path uses:
   - the same `https://api.bitget.com` base with a demo-trading header;
   - a separate base URL;
   - a provider-specific product/account flag;
   - a separate API-key class;
   - or a code path that has not yet been implemented.
4. Compare the exact environment expected by the provider with the exact request headers and endpoints produced by `BitgetRestClient`.
5. Preserve the current mainnet read-only configuration untouched while testing demo configuration.

### Evidence required before writing the template

- Exact provider environment mechanism.
- Exact credential fields required.
- Exact source/service that consumes those fields.
- Whether the current code can consume them without a code change.
- Whether demo read-only probes can run without constructing the POST-capable mainnet graph.
- A safe migration/rollback strategy if code changes are required.

### Stop condition

If the current code cannot represent an isolated demo environment, stop and implement the smallest explicit configuration boundary first. Do not put a misleading `.env` file in the user's hands that the application cannot consume.

---

## Phase 2 — prepare the Windows demo credential template

**Purpose:** Give the user a safe file to fill, containing only confirmed variable names and no retained secret values.

### Intended Windows path

```text
C:\Workspace\bots\fatty-bitget-live\fatty-bitget-demo.env
```

The file must be created only after Phase 1 confirms the variable names and demo mechanism. It must not be committed, pushed, copied to the production host, or loaded into the mainnet Compose project.

### Template requirements

- Include comments explaining that the file is for Bitget demo only.
- Include placeholders for the exact confirmed demo API key, secret, and passphrase fields.
- Include the exact confirmed demo environment selector/header/base URL fields, if required.
- Include no mainnet credentials.
- Include no Telegram credentials.
- Include no PostgreSQL password.
- Include no Codex credential.
- Use a mode-600 equivalent where possible; on Windows, restrict the file ACL to the current user.
- Add the file to the repository's ignore rules if it is not already ignored.
- Validate that its contents are not staged by `git status`, `git diff`, or `git add`.

### User handoff

After creation, provide exactly this path and wait for the user to fill it:

```text
C:\Workspace\bots\fatty-bitget-live\fatty-bitget-demo.env
```

Do not read the completed secret values into chat. Do not ask the user to paste them into the conversation. The next execution phase should read the file only through a controlled secret-injection path and print presence/shape checks, never values.

---

## Phase 3 — restore Codex text+image analysis before demo lifecycle testing

**Purpose:** Make the analyzer use the previously working Codex subscription path for random/unstructured text and image inputs.

### Source and host investigation

1. Inspect the previous `fspmi-hostinger` deployment files, image history, startup commands, mounted directories, and service logs to find how Codex previously ran.
2. Inspect host-side Codex executable location and authentication presence without printing or copying credentials into the report.
3. Inspect `src/fatty_trader/analyzer/codex_runner.py`, `integration.py`, `postgres_worker.py`, `Dockerfile`, and Compose analyzer environment.
4. Determine whether the previous wiring used:
   - a host-mounted Codex binary;
   - a host-mounted `CODEX_HOME`/auth directory;
   - a gateway/proxy endpoint;
   - a wrapper command;
   - or a separate authenticated service.
5. Preserve the prior working topology where it is safe; do not replace it with deterministic parsing as the primary path.

### Required implementation outcome

Inside the running analyzer container:

```text
codex executable: discoverable and executable
Codex authentication: available through the approved isolated mechanism
text analysis: real Codex request succeeds with a harmless probe
image analysis: real Codex request succeeds with a controlled fixture
fallback: explicit error-only fallback, not silent success
telemetry: reports actual Codex status, model/context, failure class, and source ID
```

### Security boundary

- Do not copy host OAuth files into Git.
- Do not print auth JSON, bearer tokens, cookies, or subscription credentials.
- Prefer a read-only mount or controlled proxy with least privilege.
- Keep Codex credentials separate from Bitget and Telegram credentials.
- Verify the analyzer container, not only the host, before claiming Codex works.

### Tests

- Unit tests for text invocation, image/media path handling, timeout, non-zero exit, empty output, and redaction.
- Integration test with a controlled text fixture.
- Integration test with a controlled image fixture under the mounted media path.
- Production-container read-only probe proving the actual executable and auth context.
- Source-message regression test using an unstructured multi-pair message where deterministic parsing alone is insufficient.

### Stop condition

If Codex cannot be restored without unsafe credential copying or an unverified proxy, stop and report the exact infrastructure blocker. Do not downgrade the production requirement to fallback parsing without the user's explicit approval.

---

## Phase 4 — demo read-only probes using the user-filled template

**Trigger:** Only after the user fills `C:\Workspace\bots\fatty-bitget-live\fatty-bitget-demo.env` and explicitly asks to continue.

### Isolation rules

- Keep the current production `.env` unchanged.
- Load the demo file only into a separate one-shot probe or explicitly isolated Compose project/environment.
- Never overwrite mainnet credentials.
- Never run a provider POST during the first probe stage.
- Never clear the current Bitget kill switch during probe stage.

### Read-only probe sequence

1. Validate the file has every required field and no unexpected secret class.
2. Verify API credential presence without logging values.
3. Verify provider environment selection.
4. Read server time and calculate clock skew.
5. Read account identity/balance.
6. Read `USDT-FUTURES` contracts.
7. Read metadata for representative symbols.
8. Read positions, pending orders, plan orders, fills, and account state.
9. Capture exact provider error code/message if any, with secrets redacted.
10. Confirm the old `40099` is gone before proceeding.

### Acceptance

- All read-only requests return provider success.
- Correct demo account is identified.
- No production account mutation occurs.
- No provider POST occurs.
- A sanitized probe report is written with timestamp, environment classification, symbols tested, and response status only.

---

## Phase 5 — full actual-demo lifecycle, including Point 6 and Point 8

**Trigger:** Only after Phase 4 passes and the user explicitly authorizes demo mutations.

### Symbol and sizing matrix

Use actual demo metadata for multiple representative pairs, not only one hardcoded pair:

- high-price contract;
- mid-price contract;
- low-price contract;
- sub-cent contract;
- unusual contract multiplier / `1000`-style symbol if the demo catalogue contains one;
- at least one real symbol emitted by `@fattyfatclub`.

For each symbol, verify:

- symbol normalization;
- metadata lookup by actual symbol;
- tick/quantity step;
- minimum quantity;
- minimum notional;
- contract value/multiplier;
- leverage maximum;
- isolated margin availability;
- price and quantity rounding;
- post-rounding notional;
- rejection before POST for invalid geometry.

### Controlled lifecycle

Run one small permitted demo trade at a time:

1. Account and margin-mode read-back.
2. Metadata and price read-back.
3. Intent persistence before POST.
4. Small market entry.
5. Provider order ID read-back.
6. Fill quantity, average price, fee, and fill ID read-back.
7. Native SL/TP placement using confirmed fill quantity.
8. Native SL/TP read-back.
9. Position and liquidation-price read-back.
10. Partial-fill scenario where the demo environment supports it.
11. Restart/recovery while an intent is pending or provider state is ambiguous.
12. Verify no duplicate POST after restart or unknown result.
13. Pending limit order, cancel, and zero pending-order read-back.
14. Reduce-only close.
15. Zero-position and zero-protection residual read-back.
16. Reconciliation state and database rows read-back.

### Telegram telemetry requirements during every step

Every lifecycle action must produce a durable, sent operator event containing:

- correlation/dispatch/intent identifier;
- exchange and symbol;
- state transition;
- provider status classification;
- fill/protection result;
- safe rejection reason;
- no credential or raw signed request data.

### Alert failure drills

Run in the demo environment:

- Telegram transport timeout;
- Telegram HTTP 429/retryable failure;
- sender restart after claim;
- lease expiry and recovery;
- terminal non-retryable failure;
- notification outbox read-back;
- recovery delivery after the transport returns.

Acceptance: no business event is silently lost; outbox state distinguishes pending, retry, sent, and terminal failure; no duplicate business action is created by notification retry.

### Kill-switch test

Trigger controlled safety conditions in demo or a protocol fake first:

- stale provider state;
- wrong margin mode;
- missing SL/TP;
- unexpected position;
- clock skew;
- provider error.

Acceptance:

- latch is active;
- no new entry mutation occurs;
- containment behavior is at-most-once;
- operator receives a safe alert;
- recovery requires explicit authorization;
- read-back proves the final account state.

---

## Phase 6 — make Point 4 complete, safely

Point 4 is not “clear the kill switch now.” It is complete only when the root cause and recovery are evidenced.

### Required sequence

1. Read the current `venue_kill_switches` row and recent reconciliation state.
2. Re-run authenticated read-only account, order, fill, position, contract, and server-time probes.
3. Explain precisely why `provider-orders-invalid` was latched.
4. Verify no unexpected live position/order exists.
5. Run the demo safety tests from Phase 5.
6. Prepare an explicit recovery record with operator, timestamp, reason, evidence, and rollback path.
7. Clear or replace the latch only through the existing authorized operator/recovery mechanism.
8. Read back the latch state and Telegram event.
9. Keep `BITGET_EXECUTION_ENABLED=0` until the user separately authorizes cutover.

If the existing code does not have a safe authorized recovery path, implement that path first with tests. Do not update the database directly as a shortcut.

---

## Phase 7 — rollback preparation, Point 9

Prepare this before any demo mutation rollout and repeat immediately before any live cutover.

### Rollback package

Record:

- current production SHA;
- candidate SHA;
- previous known-good SHA;
- Compose configuration checksum/diff;
- database backup path and byte size;
- migration ledger;
- current kill-switch state;
- current effective execution gate;
- exact service restart command;
- exact source rollback command;
- database restore command, clearly marked destructive;
- operator and UTC timestamp.

### Rollback drill

- Perform source-only rollback rehearsal first.
- Verify the stack returns healthy without touching the PostgreSQL volume.
- Verify the execution gate remains closed after rollback.
- Verify Telegram telemetry reports the rollback/restart.
- Test backup readability or restore against a non-production target.
- Never restore over production during a rehearsal.

---

## Phase 8 — live cutover held for Point 10

No implementation or deployment step in this plan enables live execution.

When the user later authorizes Point 10, the final cutover plan must separately confirm:

```text
BITGET_EXECUTION_ENABLED=1
positive bounded canary maximum
validated canary symbol / dynamic-pair policy
approval reference
positive clock-skew limit
fresh backup
kill switch recovered by authorized procedure
Codex text+image path verified in the running analyzer
demo lifecycle green
rollback package ready
operator watching telemetry
```

The user must explicitly approve the exact cutover. This plan does not treat filling the demo env file, passing demo tests, or clearing the kill switch as permission to submit mainnet orders.

---

## Files likely to change during execution

These are candidates, not claims that changes are already required:

- `C:\Workspace\bots\fatty-bitget-live\docs\FULL-REPORT-fatty-multi-exchange-trader-20260906.md`
- `C:\Workspace\bots\fatty-bitget-live\docs\BITGET-LIVE-OPERATIONS.md`
- `C:\Workspace\bots\fatty-bitget-live\docker-compose.yml`
- `C:\Workspace\bots\fatty-bitget-live\Dockerfile`
- `C:\Workspace\bots\fatty-bitget-live\src\fatty_trader\config\bitget.py`
- `C:\Workspace\bots\fatty-bitget-live\src\fatty_trader\exchanges\bitget\client.py`
- `C:\Workspace\bots\fatty-bitget-live\src\fatty_trader\exchanges\bitget\probe.py`
- `C:\Workspace\bots\fatty-bitget-live\src\fatty_trader\analyzer\codex_runner.py`
- `C:\Workspace\bots\fatty-bitget-live\src\fatty_trader\analyzer\integration.py`
- `C:\Workspace\bots\fatty-bitget-live\src\fatty_trader\analyzer\postgres_worker.py`
- `C:\Workspace\bots\fatty-bitget-live\src\fatty_trader\service.py`
- `C:\Workspace\bots\fatty-bitget-live\tests\`
- `C:\Workspace\bots\fatty-bitget-live\fatty-bitget-demo.env` (untracked local template only; never commit)

No file in this list should be changed until the corresponding evidence step identifies a real need.

---

## Verification and commit discipline

For every implementation phase:

1. Write/update focused tests before the code change where applicable.
2. Run the smallest focused test.
3. Run the full suite and quality gates:

```bash
uv run pytest -q
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run mypy src
git diff --check
docker compose config --quiet
```

4. Re-check `git status --short`, changed-file scope, and secret patterns.
5. Commit only claimed files with a conventional message.
6. Push the specific branch.
7. For production changes: backup first, deploy in place, read back service/schema/runtime state, and record the deployed SHA.
8. For docs-only changes: do not claim production deployment.

---

## Explicit stop points

Stop and return an evidence-based blocker instead of guessing if any of the following occurs:

- the demo provider mechanism cannot be identified;
- the supplied demo credentials fail read-only authentication;
- provider returns `40099` or another environment mismatch;
- previous Codex wiring cannot be found;
- Codex auth would require exposing credentials;
- image parsing cannot be proven inside the analyzer container;
- provider read-back disagrees with local state;
- a notification event is not durably sent;
- the kill switch reason cannot be explained;
- a rollback backup is missing or empty;
- any operation would require enabling live execution before Point 10.

**This plan ends after preparation and user handoff. No demo lifecycle, kill-switch recovery, Codex restoration, deployment, or live cutover is executed by the planning turn.**
