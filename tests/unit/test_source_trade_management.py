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
        ("closed $PUMP for challenge +20$", "PUMPUSDT"),
        ("closed $PUMP for challenge", "PUMPUSDT"),
        ("closed $PUMP", "PUMPUSDT"),
    ],
)
def test_channel_close_variations_are_recognized(text: str, expected_symbol: str) -> None:
    update = parse_source_management(text)

    assert update is not None
    assert update.symbol == expected_symbol
    assert update.action is ManagementAction.CLOSE

def test_multiparagraph_commentary_preserves_close_action() -> None:
    text = (
        "$PUMP up more than 3R on my main account and closed here\n\n"
        "But from now on when I close the trade on the challenge then its "
        "same as I close it in normal trade to not make things complicated"
    )
    update = parse_source_management(text)
    assert update is not None
    assert update.symbol == "PUMPUSDT"
    assert update.action is ManagementAction.CLOSE


@pytest.mark.parametrize(
    ("text", "expected_symbol"),
    [
        (
            "closed half $ONE position\n\nWill keep it running with half size",
            "ONEUSDT",
        ),
        ("close half $ONE", "ONEUSDT"),
        ("closing half $BTC", "BTCUSDT"),
        ("took half profit on $ONE", "ONEUSDT"),
        ("booked half on $SOL", "SOLUSDT"),
        ("partial close on $ONE", "ONEUSDT"),
        ("closed partial $ETH", "ETHUSDT"),
        ("close 50% on $ONE", "ONEUSDT"),
        ("50% close on $DOGE", "DOGEUSDT"),
    ],
)
def test_half_and_partial_close_instructions_are_recognized_as_tp1(
    text: str, expected_symbol: str
) -> None:
    update = parse_source_management(text)
    assert update is not None
    assert update.symbol == expected_symbol
    assert update.action is ManagementAction.TP1_BOOKED


def test_symbol_less_stop_to_entry_targets_every_open_position() -> None:
    update = parse_source_management(
        "Going to sleep now, goodnight guys and dont forget to move your "
        "stoploss to entry❤️"
    )

    assert update is not None
    assert update.symbol is None
    assert update.action is ManagementAction.SL_TO_ENTRY


@pytest.mark.parametrize(
    "text",
    [
        "close everything now",
        "book some profits also❤️",
        "close half here",
        "if price comes back move stoploss to entry",
        "never move your stoploss to entry",
    ],
)
def test_symbol_less_closes_and_conditional_stops_stay_ambiguous(text: str) -> None:
    assert parse_source_management(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("sl moved to 4.14 on $SNXX | Current risk 0.5R at sl", "4.14"),
        ("SL is now at 4.14 on $SNXX", "4.14"),
        ("moved stop loss to 4.14 $SNXX", "4.14"),
        ("stop loss at 4.14 on #SNXX", "4.14"),
        ("moving my $SNXX sl to 4.14", "4.14"),
    ],
)
def test_explicit_stop_move_carries_the_pinned_price(text: str, expected: str) -> None:
    from decimal import Decimal

    update = parse_source_management(text)

    assert update is not None
    assert update.symbol == "SNXXUSDT"
    assert update.action is ManagementAction.SL_MOVE
    assert update.target_price == Decimal(expected)


@pytest.mark.parametrize(
    "text",
    [
        "$SNXX LONG TRADE | ENTRY: 4.24 | TARGET: 5.26 | STOPLOSS: 4.072",
        "stop loss hit at 4.14 on $SNXX",
        "sl moved to 4.14 on $SNXX, if price holds",
        "$SNXX sl risk 0.5R at sl",
        "SL is below 4.14 on $SNXX",
    ],
)
def test_stop_move_requires_unconditional_management_cue(text: str) -> None:
    assert parse_source_management(text) is None


def test_stop_to_entry_still_wins_over_an_explicit_price_move() -> None:
    update = parse_source_management("move sl to entry on $SNXX, then trail to 4.14")

    assert update is not None
    assert update.action is ManagementAction.SL_TO_ENTRY
    assert update.target_price is None
