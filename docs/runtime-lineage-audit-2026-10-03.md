# Read-only runtime / operational verifier audit — 2026-10-03

Scope: isolated worktree `audit/runtime-lineage`, based on `b326fbc`.
No production files, gates, database rows, containers or credentials were changed.
Only Docker metadata, selected source hashes, sanitized cycle timestamps/progress,
and the existing local HTTP health response were read. No account/provider probe
or complete operational snapshot was executed.

## Reproduced verifier defect and isolated fix

The snapshot's source detector searched only 1500 characters after the constant
name and required the exact alias `reserved_dispatch`. The deployed reservation
SQL uses another alias and includes guards outside that fragile window. This
reported two false negatives, not missing runtime guards.

The regression test runs the exact Python source probe against the repository
source in a temporary directory, mocking out Docker, DB and provider boundaries.
It failed before the fix and passes afterwards. The fix statically extracts the
complete `_RESERVE_CANARY_ENTRY_SQL` literal using AST, discovers the dispatch
alias, and checks the associated NOT IN list for all four terminal states.
Additional tests cover aliases, long literals, missing join/filter/state, wrong
alias, and unrelated decoy constants. This remains source-pattern evidence, not
an execution/concurrency proof or a general SQL semantic verifier.

At 16:52:36 UTC, executing only the source-inspection snippets inside the running
dispatcher returned:

- Baseline: `joins_dispatches=false`, `filters_terminal_states=false`.
- Fixed probe: `joins_dispatches=true`, `filters_terminal_states=true`.

No deployed script was edited to obtain this result.

## Runtime observations

- Dispatcher, monitor, intake, analyzer, operator-bot, notification-sender,
  PostgreSQL and web Docker checks were healthy. Source-management had no Docker
  healthcheck; paper-kaka was not running.
- Latest completed-cycle log ages at the sampled timestamp were dispatcher
  27.1 seconds (`idle`), source-management 0.5 seconds (`idle`), and monitor
  3.7 seconds (`HEALTHY`). These are point-in-time liveness evidence, not promises
  about future progress or provider health.
- Worker-owned progress ages were intake 9.2 seconds, analyzer 3.8 seconds,
  operator-bot 2.1 seconds and notification-sender 0.6 seconds.
- Web reported runtime readiness `ready` for postgres/intake/analyzer/notification
  and HTTP liveness `alive`. Its overall status was `degraded` because its static
  configuration component map was empty. HTTP 200 alone therefore does not imply
  overall readiness. Its Docker check tests HTTP liveness only.
- Dispatcher Docker `--check` validates configuration rather than completed-cycle
  progress (`service.py:1527-1535`). It can report healthy without proving work.
  Source-management publishes no worker-owned progress and has no Docker check.
  These are coverage gaps, but the live cycles were fresh: no service stall was
  demonstrated and no service implementation/Compose changes were made.
- Monitor has a 90-second heartbeat check; intake/analyzer/operator-bot use
  worker-owned progress; notification uses its own progress check. Web's shared
  readiness contract remains the one documented in remediation-verification.md.

## Source / image lineage

Selected SHA-256 hashes of `service.py`, `worker_health.py` and
`execution/bitget_dispatch_repository.py` matched the worktree in dispatcher and
source-management. In web, `service.py`, `worker_health.py` and `web/health.py`
matched. This establishes those files only, not the entire image or installed
package. Inspected application containers had no `/app/src` bind mount.

Application image revision labels returned `unknown`, so image IDs alone do not
provide an authenticated Git revision. Dispatcher image ID:
`sha256:ccb94d8587ba264f518e53648e213d5b5a993d9bef45c254bf0fa4008ef7549f`.
Source-management image ID:
`sha256:d86c87e7a5d66a1c4d3d83762a021a4933005b57fd0fb6570e9d592270784773`.
Web container image ID:
`sha256:a49785beeeb503b8b590580923e3abb8ff34a882a390c97c45c8de5cb4f52fd7`.
Docker image inspection of that web ID returned `No such image`, although the
container was running and its selected source files were readable. This limits
historical build metadata; it does not establish a running-source mismatch.

## Verification and limits

- Source/health targeted tests: **54 passed**.
- Entire unit directory: **1357 passed, 2 skipped**. The skipped tests require
  `FATTY_TEST_POSTGRES_DSN`; no database configuration was supplied.
- Ruff check and format for the changed Python files passed; diff whitespace check
  passed.
- No full integration/database/provider proof was attempted, and no production
  readiness or new-entry approval is implied by this audit.
