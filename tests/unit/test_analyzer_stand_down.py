from decimal import Decimal

import pytest

from fatty_trader.analyzer.deterministic_parser import parse_explicit_signal
from fatty_trader.analyzer.trade_management import parse_source_management


@pytest.mark.parametrize(
    "message",
    [
        "$BTC long wait for confirmation sl 90",
        "$BTC long cancelled sl 90",
        "$BTC long do not enter sl 90",
        "$BTC long don't enter sl 90",
        "$BTC long no entry sl 90",
    ],
)
def test_stand_down_never_looks_up_market_or_creates_entry(message):
    calls = []

    def lookup(pair):
        calls.append(pair)
        return Decimal("100")

    assert parse_explicit_signal(message, message_id=1, market_price_lookup=lookup) is None
    assert calls == []


@pytest.mark.parametrize(
    "message",
    [
        "$BTC TP1 not hit, do not book yet",
        "$BTC do not move sl to entry",
        "$BTC if TP1 hit book first tp",
    ],
)
def test_negated_or_conditional_management_abstains(message):
    assert parse_source_management(message) is None


def test_affirmative_stop_only_signal_is_preserved():
    signal = parse_explicit_signal(
        "$BTC long sl 90", message_id=1, market_price_lookup=lambda _: Decimal("100")
    )
    assert signal is not None


def test_affirmative_management_is_preserved():
    assert parse_source_management("$BTC TP1 hit booked") is not None


@pytest.mark.parametrize(
    "message",
    [
        "$NOT TP1 hit booked",
        "$NOT sl to entry",
        "$NOT close full position",
        "$WAIT TP1 hit booked",
        "$WAIT close full position",
    ],
)
def test_explicit_not_ticker_is_not_management_negation(message):
    assert parse_source_management(message) is not None


def test_explicit_wait_ticker_is_not_stand_down():
    assert (
        parse_explicit_signal(
            "$WAIT long sl 90", message_id=1, market_price_lookup=lambda _: Decimal("100")
        )
        is not None
    )
