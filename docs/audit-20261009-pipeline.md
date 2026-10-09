# Pipeline, persistence, operator and operational audit — 2026-10-09

## Scope and evidence boundary

This slice reviewed the actual Telegram intake/backfill/catchup path, source eligibility SQL, text/image analyzer and subprocess boundary, analyzer persistence/fan-out, core schema/migration and source-management claim contracts, operator/dashboard authorization, notification delivery/outbox, shared health rendering, Compose healthchecks, runtime verifier and systemd report/backup wiring. The assignment's baseline is production commit `1e4720d`; this document does not assert that production is currently running the repaired local tree.

All findings below are code-path evidence, not observations of current remote database rows, provider orders or Telegram delivery. No build, test, lint, formatter, database connection, production mutation, order placement or signal replay was run by this slice. Official documentation was read; targeted offline regressions were added for the main agent's integrated verification. Exchange execution, sizing/risk and protection changes belong to other slices. `service.py` and `execution/bitget_dispatch_execution.py` were not edited here.

## Confirmed defects and local repairs

### P1 — Market syntax invented an entry price

`analyzer/deterministic_parser.py::parse_explicit_signal` accepted `BTCUSDT LONG MARKET SL 64000 TP 64630` without market data and synthesized the stop/first-target midpoint as entry. Its own missing-price contract said never guess an entry. That manufactured value controlled canonical geometry and downstream dispatch sizing even if the actual market had already crossed the stop or target.

Market syntax now requires the existing injected/public market-price lookup and uses its observed, finite positive quote. Canonical geometry still rejects an invalid current setup. Explicit numeric entries continue to win without price I/O. The stop-only path also rejects nonfinite quotes without raising an unclassified comparison exception. Pipeline and intake fixtures now inject quotes rather than encoding midpoint behavior. Added regressions cover the observed quote, absent/zero/NaN/infinite quotes, and market below the long stop.

### P2 — Text model output was not tied to source values

`analyzer/classifier.py::_from_mapping` required only a trade-action word plus valid model-supplied geometry. Consequently `BTC long, use the attached chart` with model JSON containing invented entry/stop values could produce a canonical text signal; the prompt's explicit-source/no-invention instruction was not enforced in code. A substituted asset, opposite side or invented target could also pass.

A text classification now additionally requires the source asset token, compatible directional wording, and all returned price values to be numeric values present in the original source text. Grouped thousands and decimal/scientific numeric text are recognized without inventing prices. This guard applies to text classification, not OCR/chart extraction. Tests cover hallucinated geometry, invented targets, substituted asset/side, and legitimate grouped decimal prices. Existing invalid canonical geometry remains a separate refusal.

This does not prove the semantic role of every number in arbitrary prose; it removes the confirmed absent/substituted-value acceptance. Image interpretation still requires independent model-quality verification.

### P3 — Public quote reads could accept wrong, failed or stale evidence

`analyzer/market_price.py::public_last_price` previously extracted the first `lastPr` without checking API success, requested symbol, finite value or provider observation time. It could cache a positive price from another symbol or an error-shaped response carrying data, and could accept arbitrarily old data as current market entry evidence.

The read now requires `code=00000`, one ticker row, the exact requested symbol, a finite positive `lastPr`, and the documented millisecond `ts` within 30 seconds of the local wall clock (with the existing source policy's 30-second future allowance). Cache age includes nonnegative provider age; the existing ten-second cache lifetime and five-minute source-entry expiry are otherwise unchanged. Tests cover wrong symbols, provider error codes, missing/stale/future timestamps and nonfinite values.

Official Bitget V2 ticker documentation identifies `GET /api/v2/mix/market/ticker`, mandatory `symbol`/`productType`, `USDT-FUTURES`, `lastPr` and millisecond current-data `ts`: [canonical documentation](https://www.bitget.com/legacy-docs/classic/contract/market/Get-Ticker), [accessible Bitget-hosted copy read during audit](https://www.bitget.work/legacy-docs/classic/contract/market/Get-Ticker). The previous `/api-doc/contract/market/Get-Ticker` URL was found by search but reader requests to the `.com` site failed certificate validation; no claim relies on an unread response from that URL.

### P4 — Backfill silently omitted image evidence

`intake/backfill.py::backfill_latest` used synchronous `TelegramIntake.ingest`, retaining `has_media=True` but never downloading a fresh image. The baseline message could then be analyzed without its image; catchup deliberately polls strictly after the baseline ID, so that message was not automatically repaired by history polling.

Backfill now uses the production bounded `ingest_async` media path and configured media root. A successful fresh attachment retains its path/hash/metadata; a failed download retains an explicit rejection rather than pretending image evidence exists. Old, edited, unsupported and oversized messages keep their existing audit-only guards. Tests exercise an image-only baseline, successful bytes, failure classification and client disconnect.

### P5 — Eligibility could expire while waiting inside an analyzer batch

`analyzer/postgres_worker.py::process_received_batch` locks a bounded batch before per-message model calls. It formerly rechecked database-clock eligibility only after analyzer/model/market I/O. A later row could be expired or permanently vetoed by already-retained edit evidence when its turn arrived, yet still consume an analysis call and delay subsequent fresh work.

The initial selection now uses the complete shared `SOURCE_ELIGIBLE_SQL`, including finite timestamps, accepted origins and edit vetoes, instead of a second partial predicate. The same predicate is checked before per-message I/O and again before persistence/fan-out. This also excludes infinite timestamps before PostgreSQL driver decoding. Expired rows retire within their savepoint. Retirement preserves an existing rejection reason rather than overwriting edited-source veto evidence. The regression verifies no model call, canonical insert or dispatch insert when the pre-I/O database guard refuses the row.

### P6 — Operator HTTP exception chains exposed the bot credential

`operator/telegram_polling.py::TelegramBotApi._post` embeds the token in the required Bot API URL, then chained token-bearing `httpx` exceptions into a generic `RuntimeError`. Parent `run_operator_bot` calls `set_my_commands` before its polling error handler; a startup HTTP failure could therefore print the secret URL through a default traceback.

HTTP/JSON failures still raise the sanitized failure, but without a token-bearing exception chain. Regression cases format real mocked HTTP-status, transport and invalid-JSON tracebacks and require absence of the token and Bot API credential URL. This is a credential-disclosure repair, not a change to operator authorization or mutation gates. Notification transport now applies the same exception-chain hygiene.

### P7 — A failed durable receipt claim still advanced the operator offset

`TelegramCommandPoller.run_once` advanced the in-memory Telegram offset before `receipt_store.claim`. A temporary database failure could acknowledge an update on the next long poll even though no durable claim or command execution occurred.

The durable claim now completes before the in-memory offset advances. A failed claim leaves the update available for retry without invoking the command service. Duplicate durable claims still advance without re-execution. A regression fails the first database claim and verifies the same fetch offset is retried and the command executes only once after a successful claim. The poller documentation now accurately describes at-most-once claims rather than exactly-once execution.

### P8 — Notification bounds could create rejected HTML or oversized messages

`notifications.py::format_notification_html` had raw `[:4000]` slices after HTML escaping, and `_format_kaka_digest_html` had no cap/redaction. Such slices could break entities/closing tags; Python code-point counts allowed non-BMP text to exceed the existing Telegram UTF-16 budget. An oversized Kaka digest or malformed HTML became a terminal HTTP 400 delivery failure. The shared scheduled/operator health formatter had the same final raw slice. The manual shell report used `%b`, allowing source backslash escapes such as `\c` to alter or truncate the report.

`telegram_html.py::bounded_html` is now the single pure limiter for internally generated escaped `b/i/code/pre` cards. It counts UTF-16 conservatively including markup, keeps entities and tags atomic, reserves closing tags and closes retained markup on truncation. Notifications, shared health cards and the manual shell report use it. Kaka digest text receives the existing secret redaction. The shell report prints source text literally with `%s` instead of interpreting escapes.

Regression cases cover oversized Kaka/source text, 3,500 emoji, ampersand-heavy text, nested closing-tag integrity, Kaka secret redaction, oversized scheduled-health values and literal backslashes in the actual extracted manual-report budgeting command.

### P9 — Outbox transport/claim failures escaped durable classification

`TelegramBotSender.send` called `.get` on an unvalidated JSON response. An HTTP 200 list/string/bool raised outside `NotificationDeliveryError`, leaving the row leased until process restart/expiry. `PostgresNotificationOutbox.claim` committed a malformed non-object JSONB claim and then raised without retiring it, allowing oldest-row crash loops. Current in-tree producers serialize objects; the malformed-row defect is a latent integrity/compatibility fault, not evidence of an existing bad producer.

Transport now requires a mapping with exact boolean `ok=True`; malformed success bodies follow the existing retryable-delivery contract. A non-object outbox payload is atomically terminal-failed in its claim transaction and raises a distinct sanitized `NotificationPayloadError`; the worker reports `failed`, never idle or successful delivery, and can continue with later rows. No general programming/database exception is swallowed. All outbox/enqueue connections close on success and failure.

Tests cover malformed success bodies, malformed durable payload retirement with committed `failed_at`, explicit failed worker outcome and connection closure. Object-shape schema migration was not added: valid producers remain unchanged, and existing malformed evidence is retained rather than rewritten/deleted.

### P10 — Short leases and stale acknowledgements gave false delivery success

Outbox token predicates already prevented one claimant from updating another claimant's row, but mark methods ignored affected-row counts. `NotificationWorker.run_once` could return `sent` after a stale owner's update changed zero rows. Arbitrarily short configured leases were also allowed while transport could remain in flight.

Mark methods now return whether one owned row changed; lost ownership returns `claim-lost`, not sent/retry/failed success. Worker delivery has a ten-second total asynchronous deadline, distinct from HTTP per-operation timeouts, and rejects leases below fifteen seconds before claiming/sending. Default lease remains thirty seconds. Regressions cover rejected short leases, bounded slow send, stale ownership and durable retry classification.

This removes avoidable short-lease overlap and false acknowledgements, not the unavoidable cross-system at-least-once window. A crash after Telegram acceptance but before PostgreSQL acknowledgement, or a sufficiently long scheduler/host pause, can still cause duplicate delivery. No exactly-once claim is made.

### P11 — Web/report/runtime health could be false or permanently degraded

`web/health.py::build_health_report` required nonempty static `SERVICE_COMPONENTS` even for web, while the actual Compose web environment deliberately supplies none. Thus fresh runtime evidence could never produce overall `ok`. Web also reported trader mode instead of independently configured `BITGET_MODE`. Compose checked only HTTP 200, which the liveness endpoint intentionally returns even for degraded/unknown readiness.

Web configuration no longer requires invented static component flags; valid mode configuration remains distinct from required fresh PostgreSQL/intake/analyzer/notification runtime evidence. Web reports the configured venue mode independently and still reports `orders_enabled=None`. Compose parses the JSON verdict and fails the container check unless overall status is `ok`. Tests exercise actual rendered Compose environment with fresh runtime, mismatched trader/venue mode, and HTTP-success/degraded-body probe failure.

The scheduled report's service loader and shared renderer previously permitted running containers without healthy evidence. They now treat missing health as unhealthy/degraded. `scripts/verify_bitget_runtime.sh` previously omitted intake, analyzer and notification-sender, checked only a running subset and checked source lineage for another subset. Its single operational service set now covers PostgreSQL, intake, analyzer, dispatcher, monitor, operator, notification-sender, source-management and web, requires `running:healthy`, and checks copied source lineage for every Python service. Existing policy/latch/admission gates remain ahead of provider probing. Fake-compose regressions cover omitted/stopped/unhealthy services and missing healthchecks without real provider requests.

**Parent-owned integration requirement:** source-management previously had no owner-controlled progress/check path in `service.py`. This slice adds its Compose healthcheck using `fatty_trader.service --service source-management --check` and communicated the matching parent-owned change: `owned_worker_health("source-management")`, progress after completed executor cycles, and inclusion in the existing `check_worker_health` branch. The main agent owns that implementation and its integrated verification; this slice does not claim it was implemented or exercised here.

## Audited boundaries intentionally unchanged

- **Source identity/freshness:** raw revisions are unique by channel/message/revision; catchup uses an independent watermark, reverse history and durable handling before advancing. Realtime duplicates do not advance coverage. Original timestamps, five-minute expiry and permanent edited-message vetoes still govern entry eligibility. No historical signals were replayed.
- **Analyzer atomicity:** row locks/`SKIP LOCKED`, per-message savepoints, deterministic canonical identity, canonical ID readback, per-exchange unique dispatch identity and outbox deduplication remain in the same analyzer transaction. One bad message rolls back its partial signal/fan-out before being marked failed. No source-forward duplicate notification was restored; analysis still owns the single operator-facing interpretation event.
- **Model capability boundary:** Codex uses explicit auth only, an isolated temporary cwd/environment, bounded output/time and Linux parent credential hardening. Its read-only sandbox is not filesystem read confinement; dedicated auth/media mounts remain a deployment prerequisite. The image model's semantic/OCR accuracy was not independently established.
- **Database fencing:** live intent/provider fill IDs and provider-intent uniqueness were read; migration savepoints distinguish duplicate DDL from data uniqueness violations. No migration was changed and no production financial evidence was rewritten. Exchange admission/fill/protection ledger correctness is covered by the entry/protection slices, not asserted here.
- **Source-management claim semantics:** its store can immediately reclaim `claimed` work after the claim transaction ends. Stable provider-intent IDs prevent repeated POSTs for the same action, including an ambiguous/restarted claimant; this is not an exclusive in-flight processing lease. Multi-worker ownership/state-race behavior remains a limitation requiring coordinated design if that service is scaled. This slice did not change its trading/protection gateway or mutation policy.
- **Operator authorization:** exact sender identity is required before command parsing; private/unforwarded messages, independent mutation gates and one-use parameter-bound expiring confirmation tokens remain enforced. Durable receipts intentionally favor at-most-once mutation handling: a crash after committed receipt but before command execution can lose that command, and a failed reply is not permission to replay a mutation.
- **Dashboard boundary:** dashboard routes have no application authentication. Checked-in deployment publishes loopback only, and reviewed responses contain sanitized health/configuration summaries, not account positions, orders or secret values. No externally exposed proxy was verified. Do not expose this port through an unauthenticated public proxy.
- **Recovery webhook:** exact Bearer authorization precedes inventory/disabled recovery responses. Inventory is read-only; full recovery/kill-switch release remains disabled. No active deployment/caller beyond tests was found during this slice.
- **Notification retry policy:** transport/429/5xx retryability, terminal other HTTP errors, capped attempts, durable retry scheduling, unique claim tokens and producer dedup keys remain. Missing bot/target configuration remains inert/unhealthy, not a healthy no-op. Fresh sender liveness does not prove all historical notifications were delivered; terminal failed rows remain audit evidence.
- **Deployment/report scope:** the systemd scheduled report runs the provider-first Python script every six hours; backup timers remain persistent/daily. Operations documentation was corrected from the stale hourly/60-minute description. Periodic reports still send directly and are not business-event outbox messages; transient direct-send failure can lose that interval's card. No retry/outbox architecture or extra telemetry was added.

## Main-agent verification scenarios

Run only after all slices and the source-management parent integration have landed:

1. Targeted unit suites: `test_pipeline`, `test_telegram_intake`, `test_telegram_backfill`, `test_llm_analyzer`, `test_analyzer_false_positive_regressions`, `test_analyzer_worker`, `test_market_price`, `test_operator_polling`, `test_notification_outbox`, `test_web_runtime_health`, `test_compose_runtime_health`, `test_report_execution_health`, `test_health_report_contract`, `test_health_report_parity` and `test_production_live_policy`.
2. Existing PostgreSQL source-freshness/savepoint, canonical/fan-out idempotence and notification concurrent-claim scenarios; verify source expiry during a long model call still prevents persistence and that the new pre-I/O check preserves edit vetoes.
3. Notification transaction scenarios: two claimants, stale token affecting zero rows, malformed JSONB followed by a valid pending row, DB failure/rollback/close, send timeout and crash after provider acceptance. Treat the last case as documented possible duplicate delivery, never entry replay.
4. Render Compose without changing secrets/risk flags; verify all nine operational services have valid health contracts. Check source-management's actual owner progress/check behavior through the parent-owned loop. HTTP 200 with degraded/unknown readiness must fail the web container probe.
5. Run the fake runtime-verifier regressions before any authenticated runtime/provider verification. Stopped intake/analyzer/sender and running-unhealthy monitor/source-management must block before the provider probe. A current host SHA alone must not bypass copied-source lineage checks.
6. Evaluate chart-only and captioned historical source fixtures in a strictly non-LIVE/offline lane. Successful OCR/model process output is not provider execution/fill/protection evidence.

No result from these checks is claimed by this document until the main agent records the exercised integrated verification.
