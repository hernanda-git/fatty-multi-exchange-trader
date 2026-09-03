# Layer 1 Codex CLI status

The plan requires a literal `codex exec` subprocess. On this implementation host, Phase 0 is currently **BLOCKED**: `codex` is not installed (`command -v codex` returned no executable on 2026-09-03).

The repository therefore ships a fail-closed deterministic parser for only rigid text fixtures; it never infers image-only or ambiguous content and does not submit exchange orders. `scripts/probe_codex.py` must be run on the deployment host before enabling the analyzer service. It records `codex --version` and `codex exec --help`, bounds execution, and never forwards input through a shell.

Required proof before analyzer activation:

1. Supported schema/JSON output flags and exit behavior.
2. Image-path support or documented preprocessing alternative.
3. OAuth state mount behavior in a dedicated empty git work directory.
4. Timeout graceful terminate then forced kill.
5. Bounded, redacted stdout/stderr.
