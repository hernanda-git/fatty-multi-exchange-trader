# Bitget protection hardening

Status: implemented in `feat/bitget-protection-ws-hardening`; runtime activation is
closed by default.

This document describes the safety contract implemented in the repository. It does
not authorize LIVE execution, a canary, or any provider mutation. Provider and
container claims must be established by the read-only deployment checks in
[`BITGET-PROTECTION-OPERATIONS.md`](BITGET-PROTECTION-OPERATIONS.md).

## 1. Scope and safety boundary

The protection path is designed as four cooperating layers:

```text
entry fill
  -> native Bitget position SL/TP (primary)
  -> Classic mark-price WebSocket (fast fallback trigger)
  -> private position/order/plan events (state confirmation)
  -> REST watchdog/reconciliation (stale-gap recovery)
  -> one atomic, reduce-only fallback-close intent
```

The implementation is additive and fail-closed:

- Existing dispatch, intents, fills, reservations, and history are preserved.
- New migrations only create tables/indexes or add constrained state.
- Missing protection on one symbol is a symbol-local admission failure; it does
  not latch a global kill switch.
- Provider/account failures, malformed provider rows, unknown order results,
  stale state, and failed reconciliation block the affected safety path.
- New protection capability admission, stream processing, fallback mutations,
  operator mutations, and execution are disabled unless explicitly enabled.
- No code in the stream reader sends an order or cancel request.

The code is not a claim that the current deployment is canary-ready. The actual
running image, environment, database schema, provider account, and service state
must be read back after deployment.

## 2. Components and ownership

| Component | Responsibility | Mutation boundary |
| --- | --- | --- |
| `protection_contract.py` | Bitget Classic V2 request/response normalization | Pure functions |
| `client.py` | Signed REST reads and explicitly-shaped REST mutations | Provider GET/POST through adapter |
| `async_execution.py` | Intent-first entry, fill read-back, native protection verification, containment | POST only after durable claim |
| `reconciliation_live.py` | Strict native position/plan read-back validation | Provider GET only |
| `protection_capability.py` | Pure symbol/environment admission decision | No I/O |
| `protection_capabilities.py` | Durable capability observations | Local PostgreSQL writes |
| `websocket.py` / `ws_models.py` | Classic login, subscription, mark/private event parsing, heartbeat, reconnect | Provider WebSocket only |
| `bitget_protection_stream.py` | Mark-event threshold engine and observe-only capability updates | Close callback is an explicit seam; service runtime is observe-only |
| `bitget_protection_watchdog.py` | Symbol-local freshness and provider-position checks | Provider GET + local capability write |
| `bitget_monitor.py` | Existing monitor plus provider-only exit reconciliation | Provider GET + local ledger write; no global latch for missing native plans |
| `live.py` / `live_intents.py` | Atomic durable intent/fill fences | Local memory/PostgreSQL |
| `migrations.py` | Additive schema versions 11 and 12 | Local PostgreSQL schema migration |

## 3. Native Bitget SL/TP contract

The adapter targets Bitget Classic V2 USDT futures position-level protection.
The request builder is in `protection_contract.py`; the REST transport is in
`client.py`.

### 3.1 Request invariants

- `symbol` is normalized and validated against the requested symbol.
- `holdSide` is the canonical Bitget side: `buy` or `sell`.
- Position-level plans do not add a generic `size` field. Partial plans use the
  provider-specific size fields (`stopLossSize` / `stopSurplusSize`).
- `planType=profit_loss` is explicit for the pending-plan read-back.
- Mark-price triggering is explicit (`triggerType=mark_price` in the internal
  contract representation).
- Market execution is explicit with `executePrice=0`.
- Stop-loss and take-profit client OIDs are deterministic and persisted before
  the provider request.
- Confirmed provider filled quantity, not requested quantity, is used for
  protection sizing.
- Generic or undocumented payload fields must not be added as a workaround.

### 3.2 Acknowledgement is not proof

A successful placement response is only an acknowledgement. Native protection is
`VERIFIED` only after a provider read-back proves all applicable fields:

1. exactly one open provider position exists for the symbol;
2. symbol and `holdSide` match the local intent;
3. observed quantity matches the confirmed filled quantity, within the applicable
   contract representation;
4. exactly one matching stop-loss and take-profit plan is found;
5. plan type, plan status, plan IDs, client OIDs, trigger prices, mark-price
   trigger type, and market execution mode match;
6. position-level plans do not unexpectedly carry a generic size;
7. partial plans carry the expected provider size;
8. malformed, empty, ambiguous, or stale responses are not interpreted as flat or
   protected.

If read-back fails, the execution adapter records a degraded/failed outcome and
uses the configured containment/reconciliation path. It never blind-retries an
ambiguous provider POST.

## 4. Symbol-local capability gate

`BitgetProtectionCapability` is keyed by `(exchange, environment, symbol)` and
stores:

- account position and margin mode;
- native state: `UNKNOWN`, `VERIFIED`, `UNSUPPORTED`, or `FAILED`;
- whether fallback is explicitly allowed;
- payload profile;
- last native verification/error;
- stream state: `DISABLED`, `CONNECTING`, `HEALTHY`, `STALE`, or `FAILED`;
- last stream event time.

Admission rules in `can_admit_symbol()`:

- `VERIFIED` native protection admits the symbol.
- A fallback admits only when `fallback_allowed` is true, stream state is
  `HEALTHY`, the event is fresh, and the environment matches exactly.
- `UNKNOWN`, `UNSUPPORTED`, stale, failed, malformed, or cross-environment
  records block only that symbol.
- The policy never changes global kill-switch state.

The dispatcher consults this policy only when
`BITGET_PROTECTION_CAPABILITY_GATE_ENABLED=1`. The default is `0`.

## 5. Mark-price WebSocket

The implementation uses the documented Classic endpoint:

```text
wss://ws.bitget.com/mix/v1/stream
```

The REST adapter uses V2 endpoints, while the Classic WebSocket protocol uses the
legacy `mc`/`UMCBL` subscription identifiers. This compatibility boundary is
intentional and remains a deployment risk until a read-only connection is proven
against the target account/environment.

Current subscription shape in `ws_models.py`:

- public `ticker` for each configured symbol with `instType=mc`;
- private `positions` with `instType=UMCBL`, `instId=default`;
- private `orders` with `instType=UMCBL`, `instId=default`;
- private `ordersAlgo` with `instType=UMCBL`, `instId=default`.

Order events can produce normalized fill events when the row contains a positive
fill size. The normalized event retains provider order ID, client OID, fill ID,
price, fee, and event time. There is no separate unsupported `fills` subscription
invented in this implementation.

Safety behavior:

- login uses Classic `/user/verify` signing with a seconds timestamp;
- invalid frames, symbols, prices, quantities, fees, or timestamps raise a
  protocol error;
- mark-price freshness is tracked per symbol, never globally;
- text `ping` is used for the Classic heartbeat;
- one silent heartbeat window marks the connection stale and sends `ping`;
- two consecutive silent heartbeat windows raise a connection error and force the
  reconnect state;
- reconnect performs a fresh login and resubscription with capped exponential
  backoff;
- cancellation closes the socket through the shared runtime shutdown path.

The stream runtime currently updates capability observations in observe-only mode.
The threshold engine and close callback are tested as separate seams, but the
production service does not enable fallback close mutation by default.

Official references:

- [Bitget Classic mark-price channel](https://www.bitget.com/api-doc/contract/websocket/public/Mark-Price-Channel)
- [Bitget Classic positions channel](https://www.bitget.com/api-doc/contract/websocket/private/Positions-Channel)
- [Bitget Classic order channel](https://www.bitget.com/api-doc/contract/websocket/private/Order-Channel)
- [Bitget Classic fill channel](https://www.bitget.com/api-doc/contract/websocket/private/Fill-Channel)
- [Bitget WebSocket overview](https://www.bitget.com/api-doc/contract/websocket)

## 6. REST watchdog and private-state reconciliation

`BitgetProtectionWatchdog.run_once()` checks every configured symbol independently:

1. inspect mark-price stream freshness for that symbol;
2. read the provider position through REST;
3. reject exceptions, malformed rows, and invalid envelopes;
4. preserve the last stream timestamp during REST reconciliation;
5. update that symbol's capability state;
6. allow a new entry only when the provider read succeeded and either native
   protection is verified or the fallback stream is fresh.

A successful empty provider list means flat only after a successful, valid read.
An exception or malformed response is not flat. A provider read failure blocks
that symbol even if an older native record was previously verified.

The monitor also reconciles unmatched provider exits. A fill with no local
`clientOid` is considered for synthetic reconciliation only when provider fields
identify an exit, including `enterPointSource=SYS`, `tradeSide=burst_*`, or an
explicit reduce-only marker. Existing local entries are not replayed.

## 7. Fallback close idempotency

Every close path uses a deterministic close identity and an atomic intent claim:

1. build the close client identity from the durable position/fallback identity;
2. claim `(exchange, client_order_id)` in PostgreSQL before any provider POST;
3. mark the close intent `closing` before sending the reduce-only market close;
4. if the claim already exists, do not send another POST; reconcile with GET;
5. clamp the requested quantity to the current provider quantity;
6. unknown POST results remain `closing`/unknown until provider state is read back;
7. provider-flat state is accepted only after a successful provider read;
8. malformed provider rows fail closed instead of causing a duplicate close.

This is an at-most-once provider-submit fence, not a guarantee that a provider
accepted an order. Provider order/fill read-back remains mandatory.

## 8. Liquidation buffer policy

Before admission, the policy checks the liquidation price against the intended
stop loss. It accounts for:

- direction: LONG requires liquidation below stop; SHORT requires liquidation
  above stop;
- relative minimum liquidation gap percentage;
- minimum tick-distance buffer;
- latency/slippage allowance relative to entry.

A stop that is geometrically valid but too close to liquidation is rejected. The
historical WLD liquidation must not be replayed or used as a new entry.

## 9. Provider-only/system liquidation reconciliation

Migration 12 adds `provider_reconciliation_events`, keyed uniquely by
`(exchange, provider_fill_id)`. The reconciliation helper preserves:

- provider order ID and fill ID;
- symbol and close side;
- `SYSTEM_LIQUIDATION`, `BOT_FALLBACK_CLOSE`, `NATIVE_SL`, or `PROVIDER_EXIT`;
- quantity, price, fee, realized PnL, observation time, and synthetic local OID.

The helper refuses fills that already have a bot client OID, missing identity,
invalid positive quantities/prices, invalid sides, or unsafe payloads. Replaying a
provider fill returns the existing durable identity instead of creating another
close/fill.

## 10. Additive migrations

Migration 11 creates `bitget_protection_capabilities`.

Migration 12 creates `provider_reconciliation_events` and its symbol/time index.
It also enforces provider-fill uniqueness and a foreign key to the synthetic/local
live intent. Both migrations are append-only and idempotent through the existing
migration runner. They do not delete or rewrite reservations, fills, orders,
intents, or historical rows.

Apply migrations only through the repository migration service after a verified
PostgreSQL backup. See the operations runbook for the exact rollout order.

## 11. Runtime flags and safe defaults

All values are supplied by Compose/environment; secrets are never committed.

| Variable | Default | Meaning |
| --- | --- | --- |
| `BITGET_MODE` | `DEMO` | Provider environment; must be `DEMO` or `LIVE`. |
| `BITGET_EXECUTION_ENABLED` | `0` | Opens the POST-capable dispatcher graph only when explicitly enabled. |
| `BITGET_PROTECTION_CAPABILITY_GATE_ENABLED` | `0` | Enables symbol-local protection admission. |
| `BITGET_PROTECTION_STALE_SECONDS` | `5` | Capability freshness bound. |
| `BITGET_FALLBACK_MUTATIONS_ENABLED` | `0` | Legacy monitor close mutation gate. |
| `BITGET_PROTECTION_STREAM_ENABLED` | `0` | Starts the Classic stream runtime. |
| `BITGET_PROTECTION_STREAM_MODE` | `observe` | Only `observe` is accepted by the current service builder. |
| `BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED` | `0` | Must remain `0` until close wiring is separately proven. |
| `BITGET_PROTECTION_STREAM_SYMBOLS` | empty | Required only when the stream is explicitly enabled. |
| `BITGET_PROTECTION_STREAM_STALE_SECONDS` | `5` | Mark event freshness bound. |
| `BITGET_PROTECTION_STREAM_HEARTBEAT_SECONDS` | `25` | Classic heartbeat receive/send window. |
| `BITGET_PROTECTION_REST_WATCHDOG_SECONDS` | `5` | Watchdog interval, bounded to 60 seconds. |
| `BITGET_OPERATOR_MUTATIONS_ENABLED` | `0` | Manual operator mutation gate. |
| `BITGET_CANARY_MAX_ORDERS` | `0` | Global entry cap; zero is closed. |
| `BITGET_APPROVAL_REFERENCE` | empty | Required by explicit LIVE cutover validation. |
| `BITGET_MAX_CLOCK_SKEW_MS` | `5000` in Compose | Maximum accepted provider clock skew. |

A running container may have environment values different from these defaults.
Read them from the container without printing credential values.

## 12. Verification evidence

The repository's offline verification battery is:

```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run python -m compileall -q src tests
git diff --check
docker compose config --quiet
```

Unit tests use fakes/fixtures and do not authorize provider mutation. They do not
prove a deployed image, live WebSocket contract, provider read-back, or canary.
Those tracks are intentionally separate and are covered by the operations runbook.
