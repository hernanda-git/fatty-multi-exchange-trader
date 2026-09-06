from __future__ import annotations

import json
from decimal import Decimal

from fatty_trader.analyzer.classifier import classify_json
from fatty_trader.analyzer.codex_runner import CodexRunResult
from fatty_trader.analyzer.deterministic_parser import parse_explicit_signal
from fatty_trader.analyzer.integration import AnalysisStatus, analyze_with_fallback
from fatty_trader.domain.enums import Direction


def run_result(payload: dict) -> CodexRunResult:
    return CodexRunResult(True, False, False, 0, None, json.dumps(payload), "")


def test_parser_accepts_plural_targets_and_preserves_all_targets() -> None:
    signal = parse_explicit_signal(
        "#PUMP $PUMP LONG TRADE ENTRY: 0.00427 TARGETS: 0.004438 - 0.004915 STOPLOSS: 0.00416",
        message_id=16090,
    )

    assert signal is not None
    assert signal.pair_token == "PUMP"
    assert signal.direction is Direction.LONG
    assert signal.entry_price == Decimal("0.00427")
    assert signal.stop_loss == Decimal("0.00416")
    assert signal.take_profits == (Decimal("0.004438"), Decimal("0.004915"))


def test_classifier_preserves_only_values_present_in_json() -> None:
    result = classify_json(
        "#PYTH $PYTH LONG TRADE ENTRY: 0.0568 TARGET: 0.0708 STOPLOSS: 0.05462",
        json.dumps(
            {
                "actionable": True,
                "pair": "PYTH",
                "side": "LONG",
                "entry": 0.0568,
                "stop_loss": 0.05462,
                "take_profits": [0.0708],
                "confidence": 0.91,
                "reason": "explicit entry, stop, and target",
            }
        ),
        message_id=16083,
    )
    assert result.actionable is True
    assert result.signal is not None
    assert result.signal.direction is Direction.LONG
    assert result.signal.take_profits == (Decimal("0.0708"),)


def test_classifier_does_not_invent_missing_trade_geometry() -> None:
    result = classify_json(
        "$ETH 6% up",
        json.dumps({"actionable": False, "reason": "movement only"}),
        message_id=16084,
    )
    assert result.actionable is False
    assert result.signal is None
    assert result.pair is None
    assert result.entry is None
    assert result.stop_loss is None
    assert result.take_profits == ()


def test_classifier_rejects_invalid_canonical_geometry() -> None:
    result = classify_json(
        "BTC LONG",
        json.dumps(
            {
                "actionable": True,
                "pair": "BTC",
                "side": "LONG",
                "entry": 100,
                "stop_loss": 101,
                "take_profits": [110],
                "confidence": 0.8,
                "reason": "bad",
            }
        ),
        message_id=3,
    )
    assert result.actionable is False
    assert result.signal is None
    assert "geometry" in result.reason.lower()


def test_llm_result_is_primary_and_parser_is_not_used_on_success() -> None:
    result = analyze_with_fallback(
        text="#GIGGLE $GIGGLE LONG TRADE ENTRY: 36.85 TARGET: 43 STOPLOSS: 35.45",
        message_id=16081,
        codex_runner=lambda _: run_result(
            {"actionable": False, "reason": "model says this is not a signal"}
        ),
    )
    assert result.status is AnalysisStatus.CODEX_SUCCEEDED
    assert result.signal is None
    assert result.failure_class is None


def test_parser_is_used_only_after_codex_failure() -> None:
    result = analyze_with_fallback(
        text="#GIGGLE $GIGGLE LONG TRADE ENTRY: 36.85 TARGET: 43 STOPLOSS: 35.45",
        message_id=16081,
        codex_runner=lambda _: CodexRunResult(False, True, False, 7, "failed", "", ""),
    )
    assert result.status is AnalysisStatus.FALLBACK_ACCEPTED
    assert result.signal is not None
    assert result.signal.pair_token == "GIGGLE"
