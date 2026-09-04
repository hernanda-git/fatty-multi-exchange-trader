"""Connect Codex analysis to a fail-closed deterministic fallback."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from fatty_trader.analyzer.classifier import classifier_prompt, classify_json
from fatty_trader.analyzer.codex_runner import CodexRunResult
from fatty_trader.analyzer.deterministic_parser import parse_explicit_signal
from fatty_trader.domain.models import CanonicalSignal


class AnalysisStatus(StrEnum):
    CODEX_SUCCEEDED = "CODEX_SUCCEEDED"
    FALLBACK_ACCEPTED = "FALLBACK_ACCEPTED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


@dataclass(frozen=True)
class AnalysisResult:
    status: AnalysisStatus
    signal: CanonicalSignal | None
    failure_class: str | None = None


def analyze_with_fallback(
    *,
    text: str,
    message_id: int,
    codex_runner: Callable[[str], CodexRunResult],
) -> AnalysisResult:
    try:
        codex = codex_runner(classifier_prompt(text))
    except OSError:
        return _fallback(text, message_id, "codex unavailable")
    if codex.succeeded:
        classified = classify_json(text, codex.stdout, message_id=message_id)
        return AnalysisResult(AnalysisStatus.CODEX_SUCCEEDED, classified.signal)
    return _fallback(text, message_id, codex.failure_reason or "codex failed")


def _fallback(text: str, message_id: int, failure_class: str) -> AnalysisResult:
    signal = parse_explicit_signal(text, message_id=message_id)
    if signal is None:
        return AnalysisResult(AnalysisStatus.MANUAL_REVIEW, None, failure_class)
    return AnalysisResult(AnalysisStatus.FALLBACK_ACCEPTED, signal, failure_class)
