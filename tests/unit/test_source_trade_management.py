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
