"""Attribution must not double count and must survive unlinked entries."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fatty_trader.ops.signal_scorecard import Trade, attribute, summarize

START = datetime(2026, 11, 19, 0, 0, tzinfo=UTC)


def _dispatch(dispatch_id, symbol, created, *, entry="1.00", stop="0.90", state="CLOSED"):
    return {
        "dispatch_id": dispatch_id,
        "symbol": symbol,
        "direction": "LONG",
        "entry_price": Decimal(entry),
        "stop_loss": Decimal(stop),
        "state": state,
        "terminal_reason": None,
        "created_at": created,
    }


def _fill(symbol, oid, price, qty, realized, fee, at):
    return {
        "symbol": symbol,
        "client_order_id": oid,
        "price": Decimal(price),
        "quantity": Decimal(qty),
        "realized_pnl": None if realized is None else Decimal(realized),
        "fee": Decimal(fee),
        "filled_at": at,
    }


def test_two_trades_on_one_symbol_are_separated_by_the_next_dispatch():
    dispatches = [
        _dispatch("a", "AAAUSDT", START),
        _dispatch("b", "AAAUSDT", START + timedelta(hours=4)),
    ]
    intents = [
        {"dispatch_id": "a", "client_order_id": "oid-a"},
        {"dispatch_id": "b", "client_order_id": "oid-b"},
    ]
    fills = [
        _fill("AAAUSDT", "oid-a", "1.00", "100", None, "0.05", START),
        _fill("AAAUSDT", "close-a", "1.10", "100", "9.0", "0.06", START + timedelta(hours=2)),
        _fill("AAAUSDT", "oid-b", "1.20", "100", None, "0.05", START + timedelta(hours=4)),
        _fill("AAAUSDT", "close-b", "1.15", "100", "-5.0", "0.06", START + timedelta(hours=6)),
    ]

    trades = attribute(
        dispatches=dispatches, intents=intents, fills=fills, now=START + timedelta(days=1)
    )

    assert [trade.realized for trade in trades] == [Decimal("9.0"), Decimal("-5.0")]
    assert [trade.fees for trade in trades] == [Decimal("0.11"), Decimal("0.11")]
    # R = net / (risk_per_unit * qty): (9 - 0.11) / (0.10 * 100)
    assert trades[0].r_multiple == Decimal("0.889")
    assert all(trade.linked for trade in trades)


def test_unlinked_entry_still_scores_from_the_dispatch_time():
    dispatches = [_dispatch("solo", "BBBUSDT", START)]
    fills = [
        _fill("BBBUSDT", "live-bbb", "1.00", "50", None, "0.02", START + timedelta(minutes=3)),
        _fill("BBBUSDT", "provider-bbb", "0.95", "50", "-2.5", "0.02", START + timedelta(hours=1)),
    ]

    trades = attribute(dispatches=dispatches, intents=[], fills=fills, now=START + timedelta(days=1))

    assert trades[0].linked is False
    assert trades[0].filled_qty == Decimal("50")
    assert trades[0].realized == Decimal("-2.5")
    assert trades[0].net == Decimal("-2.54")


def test_summary_reports_expectancy_and_unlinked_entries():
    trades = [
        Trade(
            dispatch_id="a",
            symbol="AAAUSDT",
            direction="LONG",
            state="CLOSED",
            terminal_reason=None,
            signal_entry=Decimal("1"),
            stop_loss=Decimal("0.9"),
            filled_price=Decimal("1"),
            filled_qty=Decimal("100"),
            realized=Decimal("9"),
            fees=Decimal("0.11"),
            opened_at=START,
            closed_at=START,
            linked=True,
        ),
        Trade(
            dispatch_id="b",
            symbol="BBBUSDT",
            direction="LONG",
            state="FILLED",
            terminal_reason="recovery-missing-protection",
            signal_entry=Decimal("1"),
            stop_loss=Decimal("0.9"),
            filled_price=Decimal("1"),
            filled_qty=Decimal("10"),
            realized=Decimal("0"),
            fees=Decimal("0.01"),
            opened_at=START,
            closed_at=None,
            linked=False,
        ),
    ]

    summary = summarize(trades)

    assert summary["dispatches"] == 2
    assert summary["entered"] == 2
    # The FILLED dispatch has no realised result: it is entered, not closed.
    assert summary["closed"] == 1
    assert summary["wins"] == 1
    assert summary["unlinked"] == 1
    assert summary["net"] == Decimal("8.89")
    assert summary["win_rate"] == Decimal("1")


def test_uuid_dispatch_ids_still_link_to_their_entry_intent_fills():
    from uuid import uuid4

    dispatch_id = uuid4()
    dispatches = [_dispatch(dispatch_id, "CCCUSDT", START)]
    intents = [{"dispatch_id": dispatch_id, "client_order_id": "live-ccc"}]
    fills = [
        _fill("CCCUSDT", "live-ccc", "1.00", "10", None, "0.01", START),
        _fill("CCCUSDT", "provider-ccc", "1.05", "10", "0.5", "0.01", START + timedelta(hours=1)),
    ]

    trades = attribute(
        dispatches=dispatches, intents=intents, fills=fills, now=START + timedelta(days=1)
    )

    assert trades[0].linked is True
    assert trades[0].filled_qty == Decimal("10")
    assert trades[0].realized == Decimal("0.5")
