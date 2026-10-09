import pytest

from fatty_trader.analyzer.trade_management import ManagementAction, parse_source_management


def test_tp1_booked_is_recognized_as_wld_partial_profit_management() -> None:
    update = parse_source_management("$WLD TP1 booked here at 2R")

    assert update is not None
    assert update.symbol == "WLDUSDT"
    assert update.action is ManagementAction.TP1_BOOKED


def test_sl_to_entry_is_recognized_as_stop_management() -> None:
    update = parse_source_management("$WLD SL to entry")

    assert update is not None
    assert update.symbol == "WLDUSDT"
    assert update.action is ManagementAction.SL_TO_ENTRY


def test_plain_comment_is_not_management_instruction() -> None:
    assert parse_source_management("WLD looks strong today") is None


@pytest.mark.parametrize(
    "text",
    [
        "$WLD TP booked",
        "book TP on $WLD",
        "$WLD TP2 booked",
        "taken TP 3 on WLDUSDT",
        "$WLD second TP hit",
        "$WLD take profit booked",
        "$WLD #TP2 booked",
        "$WLD TP2 taken #TP27USDT",
    ],
)
def test_generic_and_later_tp_booking_is_cancellation_only(text: str) -> None:
    update = parse_source_management(text)

    assert update is not None
    assert update.symbol == "WLDUSDT"
    assert update.action is ManagementAction.TP_BOOKED


@pytest.mark.parametrize(
    "text",
    [
        "$WLD do not book TP2",
        "$WLD TP2 not hit",
        "$WLD if TP2 hit",
        "$WLD waiting for TP booked",
        "$WLD $BTC TP2 booked",
        "TP2 booked",
        "$TP2 booked",
        "$WLD TP2 pending",
    ],
)
def test_generic_booking_requires_explicit_unambiguous_nonconditional_symbol(text: str) -> None:
    assert parse_source_management(text) is None


def test_tp1_keeps_existing_specific_management_precedence() -> None:
    update = parse_source_management("$WLD first TP booked")

    assert update is not None
    assert update.action is ManagementAction.TP1_BOOKED
