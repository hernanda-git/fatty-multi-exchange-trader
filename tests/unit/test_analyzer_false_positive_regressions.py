import json

from fatty_trader.analyzer.classifier import classify_json
from fatty_trader.analyzer.image_analysis import signal_from_image_json
from fatty_trader.analyzer.trade_management import parse_source_management


def _classifier(text, **overrides):
    data = {
        "actionable": True,
        "pair": "BTC",
        "side": "LONG",
        "entry": 100,
        "stop_loss": 90,
        "take_profits": [],
        "confidence": 0.99,
        "reason": "valid geometry",
    }
    data.update(overrides)
    return classify_json(text, json.dumps(data), message_id=1)


def test_negated_close_is_not_parsed_as_management():
    assert parse_source_management("$BTC do not close the position") is None


def test_classifier_rejects_non_entry_wait_signal():
    result = _classifier("$BTC wait for confirmation", reason="WAIT")
    assert result.actionable is False
    assert result.signal is None


def test_classifier_rejects_unrelated_positive_geometry():
    result = _classifier("good morning everyone")
    assert result.actionable is False
    assert result.signal is None


def test_classifier_rejects_cancelled_signal_even_with_geometry():
    result = _classifier("$BTC long entry 100 stop 90 cancelled")
    assert result.actionable is False
    assert result.signal is None


def test_classifier_rejects_negated_entry_even_with_geometry():
    result = _classifier("$BTC long entry 100 stop 90; do not enter")
    assert result.actionable is False
    assert result.signal is None


def test_image_rejects_non_entry_action():
    signal = signal_from_image_json(
        {
            "setup": {
                "action": "WAIT",
                "asset": "BTC",
                "side": "LONG",
                "entry": 100,
                "stop_loss": 90,
            }
        },
        message_id=1,
        source_revision="a" * 64,
    )
    assert signal is None


def test_image_rejects_cancelled_caption_even_with_geometry():
    signal = signal_from_image_json(
        {
            "message": "$BTC long cancelled",
            "setup": {
                "action": "ENTER",
                "asset": "BTC",
                "side": "LONG",
                "entry": 100,
                "stop_loss": 90,
            },
        },
        message_id=1,
        source_revision="a" * 64,
    )
    assert signal is None


def test_image_rejects_negated_entry_caption_even_with_geometry():
    signal = signal_from_image_json(
        {
            "message": "$BTC do not enter",
            "setup": {
                "action": "ENTER",
                "asset": "BTC",
                "side": "LONG",
                "entry": 100,
                "stop_loss": 90,
            },
        },
        message_id=1,
        source_revision="a" * 64,
    )
    assert signal is None
