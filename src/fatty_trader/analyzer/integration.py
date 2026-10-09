"""Connect Codex analysis to a fail-closed deterministic fallback."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from fatty_trader.analyzer.classifier import classifier_prompt, classify_json
from fatty_trader.analyzer.codex_runner import CodexRunResult
from fatty_trader.analyzer.deterministic_parser import parse_explicit_signal
from fatty_trader.analyzer.market_price import public_last_price
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
    market_price_lookup: Callable[[str], Decimal | None] = public_last_price,
) -> AnalysisResult:
    """Classify with Codex, then fall back to the deterministic parser.

    ``market_price_lookup`` feeds market-entry formats, including stop-only scalp
    messages. The entry becomes the current observed market. The default is the
    public Bitget ticker; tests inject a fixed price. A missing price never permits
    an invented entry.
    """
    try:
        codex = codex_runner(classifier_prompt(text))
    except OSError:
        return _fallback(text, message_id, "codex unavailable", market_price_lookup)
    if codex.succeeded:
        classified = classify_json(text, codex.stdout, message_id=message_id)
        if classified.signal is not None:
            return AnalysisResult(AnalysisStatus.CODEX_SUCCEEDED, classified.signal)
        explicit_signal = parse_explicit_signal(
            text, message_id=message_id, market_price_lookup=market_price_lookup
        )
        if explicit_signal is None:
            return AnalysisResult(AnalysisStatus.CODEX_SUCCEEDED, None)
        return AnalysisResult(
            AnalysisStatus.FALLBACK_ACCEPTED,
            explicit_signal,
            classified.reason or "codex returned no signal",
        )
    return _fallback(text, message_id, codex.failure_reason or "codex failed", market_price_lookup)


def _fallback(
    text: str,
    message_id: int,
    failure_class: str,
    market_price_lookup: Callable[[str], Decimal | None],
) -> AnalysisResult:
    signal = parse_explicit_signal(
        text, message_id=message_id, market_price_lookup=market_price_lookup
    )
    if signal is None:
        return AnalysisResult(AnalysisStatus.MANUAL_REVIEW, None, failure_class)
    return AnalysisResult(AnalysisStatus.FALLBACK_ACCEPTED, signal, failure_class)
