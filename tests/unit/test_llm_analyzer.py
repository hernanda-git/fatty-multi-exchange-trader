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


def test_parser_accepts_exact_not_signal_without_symbol_corruption() -> None:
    signal = parse_explicit_signal(
        "#NOT $NOT LONG TRADE\n\nENTRY: 0.0004715\n\nTARGET: 0.00058\n\nSTOPLOSS: 0.000458",
        message_id=16100,
    )

    assert signal is not None
    assert signal.pair_token == "NOT"
    assert signal.direction is Direction.LONG
    assert signal.entry_price == Decimal("0.0004715")
    assert signal.stop_loss == Decimal("0.000458")
    assert signal.take_profits == (Decimal("0.00058"),)


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


def test_explicit_parser_recovers_signal_when_codex_misses_it() -> None:
    result = analyze_with_fallback(
        text="#GIGGLE $GIGGLE LONG TRADE ENTRY: 36.85 TARGET: 43 STOPLOSS: 35.45",
        message_id=16081,
        codex_runner=lambda _: run_result(
            {"actionable": False, "reason": "model says this is not a signal"}
        ),
    )
    assert result.status is AnalysisStatus.FALLBACK_ACCEPTED
    assert result.signal is not None
    assert result.signal.pair_token == "GIGGLE"
    assert result.failure_class == "model says this is not a signal"


def test_non_actionable_ambiguous_text_remains_codex_succeeded() -> None:
    result = analyze_with_fallback(
        text="$ETH 6% up",
        message_id=16082,
        codex_runner=lambda _: run_result({"actionable": False, "reason": "movement only"}),
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


# --- stop-only scalp format ("$ENA longed scalp here / Stoploss below: 0.27845") --------
#
# The source stopped publishing an entry price. Entry becomes the market price at
# decision time, which is only acceptable because the signal model refuses a long whose
# stop sits at or above that entry: a scalp that already ran through its stop produces no
# signal at all, instead of a market order chasing a dead setup.

_SCALP_ENA = "$ENA longed scalp here\n\nStoploss below: 0.27845"


def test_scalp_stop_only_message_becomes_a_market_entry_signal() -> None:
    signal = parse_explicit_signal(
        _SCALP_ENA,
        message_id=16219,
        market_price_lookup=lambda _pair: Decimal("0.286"),
    )

    assert signal is not None
    assert signal.pair_token == "ENA"
    assert signal.direction is Direction.LONG
    assert signal.entry_price == Decimal("0.286")
    assert signal.stop_loss == Decimal("0.27845")
    assert signal.take_profits == ()


def test_scalp_is_refused_when_the_market_already_crossed_the_stop() -> None:
    # The real ENA case (2026-09-28): market 0.27217 against a stop of 0.27845.
    signal = parse_explicit_signal(
        _SCALP_ENA,
        message_id=16219,
        market_price_lookup=lambda _pair: Decimal("0.27217"),
    )

    assert signal is None


def test_scalp_is_refused_without_a_price_source() -> None:
    assert parse_explicit_signal(_SCALP_ENA, message_id=16219) is None
    assert (
        parse_explicit_signal(_SCALP_ENA, message_id=16219, market_price_lookup=lambda _pair: None)
        is None
    )
    assert (
        parse_explicit_signal(
            _SCALP_ENA, message_id=16219, market_price_lookup=lambda _pair: Decimal("0")
        )
        is None
    )


def test_scalp_short_needs_its_stop_above_the_market() -> None:
    text = "$SOL shorted scalp here\n\nStoploss above: 200"

    accepted = parse_explicit_signal(
        text, message_id=1, market_price_lookup=lambda _pair: Decimal("190")
    )
    refused = parse_explicit_signal(
        text, message_id=1, market_price_lookup=lambda _pair: Decimal("210")
    )

    assert accepted is not None
    assert accepted.direction is Direction.SHORT
    assert accepted.entry_price == Decimal("190")
    assert refused is None


def test_stated_entry_never_consults_the_market_price() -> None:
    consulted: list[str] = []

    signal = parse_explicit_signal(
        "#ENA $ENA LONG TRADE\n\nENTRY: 0.30\n\nTARGET: 0.33\n\nSTOPLOSS: 0.27845",
        message_id=16219,
        market_price_lookup=lambda pair: consulted.append(pair) or Decimal("0.27217"),
    )

    assert signal is not None
    assert signal.entry_price == Decimal("0.30")
    assert consulted == []


def test_scalp_chatter_is_never_a_signal() -> None:
    chatter = (
        "close ASTER in small profit",
        "$QNT sl hit, not looking to trade anything today",
        "$RARE more than 1R down, tp1 booked",
        "Goodmorning ❤️\n\nI think I held up to my end of the deal",
    )
    for text in chatter:
        assert (
            parse_explicit_signal(
                text, message_id=1, market_price_lookup=lambda _pair: Decimal("1")
            )
            is None
        )


def test_scalp_reaches_the_dispatch_path_through_the_fallback_seam() -> None:
    result = analyze_with_fallback(
        text=_SCALP_ENA,
        message_id=16219,
        codex_runner=lambda _: run_result(
            {"actionable": False, "reason": "no entry price provided"}
        ),
        market_price_lookup=lambda _pair: Decimal("0.286"),
    )

    assert result.status is AnalysisStatus.FALLBACK_ACCEPTED
    assert result.signal is not None
    assert result.signal.pair_token == "ENA"
    assert result.failure_class == "no entry price provided"


def test_dead_scalp_yields_no_signal_even_through_the_fallback_seam() -> None:
    result = analyze_with_fallback(
        text=_SCALP_ENA,
        message_id=16219,
        codex_runner=lambda _: run_result(
            {"actionable": False, "reason": "no entry price provided"}
        ),
        market_price_lookup=lambda _pair: Decimal("0.27217"),
    )

    assert result.signal is None
