# Actual monitor-owned LIVE release readiness (v1)

## Integration API

```python
from fatty_trader.execution.bitget_release_readiness import (
    ReadinessUnavailable, read_monitor_readiness,
)
proof = read_monitor_readiness(
    "/run/fatty-health/monitor/release.json",
    socket_path="/run/fatty-health/monitor/owner.sock",
    expected_credential_binding=sha256_of_authenticated_collector_api_key,
    max_age_seconds=90,
    pong_max_age_seconds=65,
    max_clock_skew_ms=unchanged_safety_threshold,
)
```

The function returns a dictionary or raises `ReadinessUnavailable` with a fixed,
non-sensitive reason. Invoke AGAIN inside the existing Bitget admission advisory
lock, after all row-lock/provider waits, immediately before atomic baseline
activation/latch release. A planning-time call or cached dictionary is insufficient.
Never print the dictionary, credential binding, API key, account UID or raw frames.
A separate fresh authenticated flat-account proof and approved baseline are still
required. This helper does not validate historical ownership and cannot release
anything.

## Paths, permissions and one worker generation

- Default release path: `/run/fatty-health/service/release.json`, override with
  `BITGET_MONITOR_READINESS_PATH`; atomic replacement, mode **0600**.
- Attestor endpoint: `BITGET_MONITOR_READINESS_SOCKET_PATH`, else
  `WORKER_HEALTH_SOCKET_PATH`, else release path + `.sock`.
- `WORKER_HEALTH_PATH` receives generic progress (0644) only when the release
  snapshot is ready; same service/PID/start ticks/process UUID as the release file.
  Its progress time is the actual monitor cycle's pre-read monotonic observation,
  not the independently faster watchdog publication time.
- Monitor owns one `MonitorReadinessPublisher`, a `WorkerHealth` subclass. Do NOT
  additionally decorate monitor with `@owned_worker_health` or bind a second
  attestor to `owner.sock`. Dispatcher retains its independent publisher.
- Compose should mount monitor's dedicated metadata-only tmpfs RW at
  `/run/fatty-health/service`; operator mounts that same volume RO at
  `/run/fatty-health/monitor`, with the same worker UID for 0600 release reads.
  Dispatcher and monitor can use the same in-container mount target but MUST use
  different named volumes. No shared RW worker volume, provider secrets, host PID
  namespace or Docker socket are needed. Compose changes belong to the topology
  agent and are not included in this commit.
- A host operator may instead execute the reader inside the owning monitor
  container as its UID. Merely reading a file from the host is not proof.

Unix request is one newline-terminated JSON object containing `service`, `pid`,
`identity`, `owner_token`, and a fresh random `challenge`. Response is
`{valid: bool, challenge: echoed_challenge, proof: dictionary_or_null}`. The owner
checks its PID/start identity locally, request generation, current actual socket
readiness and connection generation against its published proof. The response
returns **in-memory worker evidence**, never an externally supplied JSON file.
The reader uses the file only to address the owning generation, checks the echoed
challenge and identity, and re-ages the response after the socket wait. No local
PID fallback exists. A live attestor thread cannot refresh stalled cycle times.
This is process identity/liveness under the dedicated volume/socket writer trust
boundary, not cryptographic protection against a malicious privileged operator.

## Exact dictionary contract

Schema name: `bitget-monitor-release-readiness/v1`. Top-level keys:

| Key | Type / meaning |
| --- | --- |
| `schema`, `service`, `environment` | Schema string, `monitor-bitget`, `LIVE` |
| `pid`, `identity`, `owner_token` | Actual PID, Linux process start ticks string, random process-lifetime UUID hex |
| `credential_binding_sha256` | SHA256(API key), INTERNAL; must equal authenticated collector binding; never log |
| `progress`, `observed_at` | Publication monotonic seconds, Unix seconds; not provider-cycle freshness by themselves |
| `ready`, `reasons` | Boolean; fixed safe reason strings, empty list only when all conditions pass |
| `monitor` | `{clean: bool, observed_at: Unix seconds/null, observed_monotonic: seconds/null}` |
| `watchdog` | Same independent report observation shape (null before first watchdog cycle) |
| `clock` | `{observed_at, observed_monotonic, lower_ms, upper_ms, bound_ms, clean}` or null |
| `private` | Socket dictionary below |

`private` keys:
- `ready`: current transport ready AND all configured watched tickers fresh.
- `account_stream_fresh`: authenticated current private account leg, all private
  ACKs, actual recent private pong, no reader failure; independent of ticker list.
- `connection_generation`: new UUID hex each connect attempt.
- `login_ack_at`: monotonic time of explicit successful private login ACK, or null.
- `subscription_acks`: sorted acknowledged channel names, exactly
  `['orders', 'orders-algo', 'positions']` for readiness. Only successful private
  `subscribe` ACKs with `instType=USDT-FUTURES`, `instId=default` count.
- `pong_at`: monotonic timestamp of actual bare-text **private** `pong`, or null.
  Connecting/login/public pong does not stamp this field.
- `pong_max_age_seconds`: transport's private heartbeat silence budget.
- `watched_symbols`: actual ticker list. `watched_symbols_healthy` is boolean for
  a nonempty list, **null** when empty: not watched, not vacuously watched healthy.

All monotonic observations are from the same host monotonic clock (normal Docker
namespaces; nonstandard time namespaces need explicit integration validation).
For a consumer needing UTC pong time, derive `observed_at - (progress - pong_at)`
from this one snapshot; never replace its observation with the time of DB insertion.
Monitor/watchdog cycle capture precedes provider reads and DB waits; each cycle
must start at or after the current socket's login ACK. Clock captures wall and
monotonic times around the server-time GET and keeps the full local-minus-server
interval. More than 5 seconds total latency, backward/non-finite samples, or a
wall-vs-monotonic elapsed discrepancy over 50ms are inconclusive. The entire
interval must be within the unchanged configured bound; reader also enforces its
own supplied safety bound.

`monitor.clean` means a completed recognized `ok` or `kill-switch-latched` report
with literal empty current reasons. Existing incident latches are deliberately not
cleared: a persisted latch can coexist with a clean current provider cycle.
`watchdog.clean` requires `HEALTHY` and empty reasons. Failed/unknown reports,
missing timestamps, stale pong/cycle/clock, a dead worker, reconnect, failed ACK,
private silence, symbol-source read failure, or an unexpected watchdog exception
cannot produce a usable proof. Fatal cycle invalidation is permanent for that
worker generation; shutdown removes evidence and prevents delayed resurrection.
Publication reads the actual observe-only stream even when provider fallback and
stream mutation flags are 0. It never creates a second provider socket or changes
LIVE/LIVE/1 lane/mutation flags.

## Parent-owned rollout and activation verification

1. Integrate component commits and run the full isolated real-PostgreSQL suite,
   Ruff, mypy, compileall, Compose validation and independent final review. Keep
   historical intent/fill/reservation rows UNVERIFIED and unchanged in meaning.
2. Keep provider fallback/stream mutation flags at 0, existing incident latches
   intact, and LIVE/LIVE/1 configuration preserved. Deploy only the reviewed
   integrated SHA; read back actual monitor source, effective gates, UID and volume
   access in its container. No deployment was performed by this implementation.
3. Wait for the actual deployed worker's login, **all** private subscription ACKs,
   actual private pong, clean monitor provider cycle, healthy watchdog, and bounded
   clock interval. Public empty ticker list is benign only with these independent
   account-stream/provider proofs. HEALTHY log lines or the legacy monitor
   heartbeat alone are insufficient.
4. Read via the helper from the approved namespace/UID and compare the internal
   credential binding to the authenticated GET-only provider collector. A new
   operator socket cannot bless an old monitor.
5. Gather the separate fresh flat positions/ordinary orders/all plan families
   proof. Under the admission lock, after all final waits, re-read monitor proof
   and re-age every provider report before atomic baseline activation and the two
   explicitly approved incident-latch releases. No baseline activation, kill
   release, historical retirement, provider POST/cancel/close or replay is done by
   this publisher.
6. In an approved isolated runtime (not by stopping production), verify refusal on
   reconnect, failed ACK, monitor/watchdog failure, private pong expiry, SIGSTOP
   and worker death. Re-read history/queue/current provider state after activation.

Offline focused tests use scripted transport/provider boundaries and a real local
subprocess for SIGSTOP/death/challenge proofs. An end-to-end test uses the actual
socket, stream, monitor, watchdog and owner-challenge reader; it proves that the
reader opens no provider socket and reconnect requires new completed cycles.
Release readiness rejects future watched-mark receipt times rather than treating
clamped convenience ages as freshness. These tests do not establish deployed
mount/LSM compatibility or actual LIVE provider readiness; those remain rollout
verification requirements. The existing quiet-socket heartbeat receive-loop fix
is retained: quiet legs continue to send bare-text pings while waiting for frames.
