# Layer 1 Codex CLI runner

`fatty_trader.analyzer.codex_runner.CodexRunner` is a fail-closed boundary around the
literal argv invocation:

```text
["codex", "exec", prompt]
```

It calls `subprocess.Popen` with `shell=False`; prompts are never interpolated into a shell
command. The runner has no execution, exchange, dispatch, or order-creation dependency. A
successful Codex process only returns bounded, redacted terminal output for a later, separate
paper-only analysis step.

## Failure behavior

Every terminal-level failure produces `CodexRunResult(terminal_failure=True)` rather than a
signal, dispatch, or order:

- missing/unstartable executable;
- non-zero Codex exit status;
- timeout; and
- timeout that requires forced kill after the configurable terminate grace period.

`CodexRunnerConfig` supplies the executable name, timeout, terminate grace period, and a per-stream
stdout/stderr byte limit. The runner drains both pipes concurrently while retaining only the bounded
prefix. Returned output redacts common token, secret, API-key, authorization/Bearer, and `sk-...`
patterns. If capture or redaction would exceed the configured limit, the output ends in
`[truncated]`.

## Deployment proof still required

On this implementation host, `codex` is unavailable. Unit tests inject a fake `Popen` process; they
do not install Codex, use credentials, or submit orders. Before enabling an analyzer service on a
paper deployment host, prove:

1. the installed `codex exec` flags and exit semantics;
2. its supported JSON/schema and image-input contract;
3. OAuth state mount behavior in a dedicated empty git work directory;
4. graceful terminate followed by forced kill; and
5. bounded, redacted stdout/stderr with representative failure output.

Until that proof is recorded, callers must treat all Codex outcomes as analysis-only and retain the
existing deterministic-parser fallback for deliberately rigid text.
