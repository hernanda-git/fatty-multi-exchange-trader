from __future__ import annotations

from fatty_trader.analyzer.codex_runner import CodexRunResult
from fatty_trader.analyzer.paper_pipeline import PaperPipeline, observed_messages


def test_observed_replay_has_two_actionable_and_three_non_actionable() -> None:
    pipeline = PaperPipeline(
        runner=lambda _: CodexRunResult(False, True, False, 1, "offline", "", "")
    )
    messages = observed_messages()
    results = [pipeline.process(message) for message in messages]
    assert sum(bool(result) for result in results) == 2
    assert pipeline.canonical_signal_count == 2
    assert [pipeline.state(message) for message in messages].count("ANALYZED") == 5
    assert sum(len(result) for result in results) == 4


def test_replay_is_idempotent_for_same_received_revision() -> None:
    calls = 0

    def failed_runner(_: str) -> CodexRunResult:
        nonlocal calls
        calls += 1
        return CodexRunResult(False, True, False, 1, "offline", "", "")

    pipeline = PaperPipeline(runner=failed_runner)
    message = observed_messages()[1]
    first = pipeline.process(message)
    second = pipeline.process(message)
    assert first == second
    assert len(first) == 2
    assert calls == 1
