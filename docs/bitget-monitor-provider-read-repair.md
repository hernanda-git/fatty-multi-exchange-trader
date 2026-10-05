# Bitget monitor provider-read repair

## Scope and retained evidence

Candidate based on `b326fbc78e1f652d6234398640adfcf3b2cf5389`, developed only in
`/home/valarion/workspace/dev/bitgetmonitorrepair`. No deployment, environment
change, provider operation, gate release, or runtime/database mutation was made.
Runtime access was limited to Docker logs and a SELECT of the Bitget latch.

Retained logs include `2026-10-05T07:21:07.315450185Z`:
`service=monitor-bitget state=kill-switch-latched reasons=provider-fills-invalid`.
Later clean cycles print the same latched state with `reasons=none`.
The read-only database result showed `active=true`, `reason=provider-fills-invalid`,
`latched_at=NULL`, `updated_at=2026-10-05 07:21:07.306262+00`.
A later watchdog log at `09:21:54.732840640Z` reports
`provider-position-read-failed`. Historical logs do not retain the underlying
exception or shape. A successful later GET (reported by the supervising
investigation) cannot distinguish the original failure; this repair did not
perform a new authenticated provider probe.

## Root causes and evidence limits

1. The fills reader catches every exception and labels it `provider-fills-invalid`.
   Thus a timeout, HTTP error, authentication rejection and an invalid list shape
   are observationally indistinguishable. Position/order monitor readers have the
   same defect; the watchdog distinguishes reasons but drops exception details.
2. GET retries cover transport failures and HTTP 5xx, not HTTP 429. A deterministic
   real-client/monitor MockTransport reproduction proves a transient single 429
   latches the baseline even when the next read would succeed. **This proves a
   source fault, not that the historical LIVE latch was caused by HTTP 429.**
3. A clean cycle checks the persisted switch but discards its stored reason.
   `reasons=none` therefore describes current checks, not an absent durable latch.
4. The latch UPSERT never updates `latched_at` on conflict. An existing inactive
   row with NULL time remains NULL on activation; a released row's old epoch also
   survives a new activation.
5. A missing `fillList` key defaults to an empty list. That malformed envelope
   silently looks clean; missing/null/malformed fills must remain fail-closed.

## Implemented behavior

- Read exceptions become `provider-fills-read-failed`,
  `provider-positions-read-failed`, or `provider-orders-read-failed`.
  Invalid shapes retain the corresponding `*-invalid` reason.
- Exception diagnostics print exception class and a numeric provider code;
  fills diagnostics also print separately captured HTTP status. No arbitrary
  exception message, response body, request headers or credentials are logged.
  Shape diagnostics print only the value's type. The watchdog now records the
  position-read exception class/code while retaining its existing failure gates.
- Only HTTP 429 GETs gain retry/backoff: the existing `max_get_retries` budget is
  shared with transport/5xx retries; default two retries means at most three GET
  attempts, waiting 1s then 2s for successive 429s. Each attempt is freshly signed.
  POST remains one attempt, including HTTP 429. Permanent non-429 errors and
  successful-but-invalid data are not automatically retried. Exhaustion still
  fails closed and latches; no automatic release is added.
- The report exposes `latched_reason` separately from current `reasons`, and the
  production loop prints both. This preserves consumers that require clean
  current evidence (`reasons == ()`) without concealing the persisted latch.
- The UPSERT sets a timestamp for a new activation or a missing active timestamp,
  preserves non-NULL timestamps across repeated active latches, and starts a new
  epoch after an approved release. A historical NULL timestamp cannot be
  reconstructed: the next latch records its current observation time, not an
  invented historical first-latch time. Merely reading a persisted latch never
  backfills or clears it.

HTTP 429 is documented as the REST access-frequency-limit status by Bitget:
<https://www.bitget.com/docs/uta/rest-api#access-restriction>.
The GET-only policy, retry budget and 1s/2s backoff are this client's policy, not a
claim that Bitget prescribes those particular delays.

## RED / GREEN receipts

Every changed behavior was exercised RED before its implementation:

| Regression | RED receipt | GREEN coverage |
|---|---|---|
| Fills exception vs shape | expected read-failed, got fills-invalid | monitor suite |
| Missing/null/malformed fills diagnostics | four failures; missing key wrongly clean | monitor suite |
| GET 429 / fresh signatures | first 429 raises instead of retrying | REST suite |
| Persisted latch report | missing `latched_reason` attribute | monitor suite |
| Runtime latch log | expected latched_reason absent from stdout | runtime suite |
| Watchdog read diagnostic | exception class absent from logs | watchdog suite |
| Latch timestamp | NULL/new epoch cases fail; existing active epoch already passes | timestamp suite |
| Position/order diagnosis | four reason/log failures | monitor suite |
| HTTP status metadata/log | missing status attribute, then missing status log | REST/monitor suites |

Additionally, the final real REST-client/monitor 429 integration regression was
run against a separate `git archive b326fbc src` baseline. The loaded module path
was verified in the temporary source archive: **2 failed** (transient 429 falsely
latches; exhausted 429 misdiagnosed). The candidate returned **2 passed**.

Verification commands (run in the private clone):

```sh
uv run ruff check src tests
uv run mypy src
env -u FATTY_TEST_POSTGRES_DSN -u DATABASE_URL uv run pytest tests -q -r f
```

Final candidate verification: **1383 passed, 265 skipped**; Ruff passed; mypy
passed for 101 source files; `git diff --check` passed.

PostgreSQL-dependent tests are intentionally skipped rather than pointed at
runtime DBs. Timestamp semantics are exercised against the production UPSERT in
an in-memory SQLite engine supporting the CASE/COALESCE/ON CONFLICT subset; only
placeholder syntax/test clock are translated and JSONB outbox SQL is captured.
This is **not** a claim of PostgreSQL migration/concurrency/integration proof.
The parent release verification should run authorized disposable-PostgreSQL
proofs before deployment. Existing entry, exposure, protection, and approved
release gates are unchanged.

## Verdict

Source diagnosis/retry/timestamp defects reproduced and corrected. No claim that
the historical LIVE exception was identified, no claim of deployed recovery,
and no justification to clear the existing latch from a successful isolated GET.
