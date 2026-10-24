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
        "Full tp on $BONK 🔥",
        "#PEPE TP2 DONE ✅",
        "#INJ TP3 DONE ✅",
        "$BONK full tped",
    ],
)
def test_generic_and_later_tp_booking_is_cancellation_only(text: str) -> None:
    update = parse_source_management(text)

    assert update is not None
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
        "$BONK look for long after 4h hold and close above 0.00484",
        "daily close above support on $BTC",
    ],
)
def test_generic_booking_requires_explicit_unambiguous_nonconditional_symbol(text: str) -> None:
    assert parse_source_management(text) is None


def test_tp1_keeps_existing_specific_management_precedence() -> None:
    update = parse_source_management("$WLD first TP booked")

    assert update is not None
    assert update.action is ManagementAction.TP1_BOOKED


@pytest.mark.parametrize(
    ("text", "expected_symbol"),
    [
        ("tp1 on $ENA", "ENAUSDT"),
        ("tp 1 on $ENA", "ENAUSDT"),
        ("$ENA tp1", "ENAUSDT"),
        ("#PEPE TP1 DONE ❤️", "PEPEUSDT"),
        ("#WIF TP1 SMASHED🔥", "WIFUSDT"),
        ("#FET TP1 SMASHED🔥", "FETUSDT"),
        ("$WLD tp1 here❤️", "WLDUSDT"),
        ("booked my first tp on $PENGU", "PENGUUSDT"),
        ("$CLO 5% up and tp1 taken❤️", "CLOUSDT"),
        ("$INJ tp1 done", "INJUSDT"),
        ("Book tp1 here on $KAITO", "KAITOUSDT"),
    ],
)
def test_channel_tp1_variations_are_recognized(text: str, expected_symbol: str) -> None:
    update = parse_source_management(text)

    assert update is not None
    assert update.symbol == expected_symbol
    assert update.action is ManagementAction.TP1_BOOKED


@pytest.mark.parametrize(
    ("text", "expected_symbol"),
    [
        ("move sl to entry on $ENA 1R up already", "ENAUSDT"),
        ("my sl moved to entry $FLOW", "FLOWUSDT"),
        ("moving my $MAGIC sl to entry", "MAGICUSDT"),
        ("Stoploss to entry on $APEX ❤️", "APEXUSDT"),
    ],
)
def test_channel_sl_to_entry_variations_are_recognized(text: str, expected_symbol: str) -> None:
    update = parse_source_management(text)

    assert update is not None
    assert update.symbol == expected_symbol
    assert update.action is ManagementAction.SL_TO_ENTRY


@pytest.mark.parametrize(
    ("text", "expected_symbol"),
    [
        ("Fully closed $ENA", "ENAUSDT"),
        ("Closed here fully $NEAR", "NEARUSDT"),
        ("close $INJ at entry", "INJUSDT"),
        ("close $WLD in small profit", "WLDUSDT"),
        ("$WLD close here", "WLDUSDT"),
        ("$JASMY TRADE CLOSED", "JASMYUSDT"),
        ("$BONK trade Closed for 4R profit", "BONKUSDT"),
    ],
)
def test_channel_close_variations_are_recognized(text: str, expected_symbol: str) -> None:
    update = parse_source_management(text)

    assert update is not None
    assert update.symbol == expected_symbol
    assert update.action is ManagementAction.CLOSE
