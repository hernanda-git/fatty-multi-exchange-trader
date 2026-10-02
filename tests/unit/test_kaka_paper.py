"""Paper-engine, worker, and isolation tests for the Kaka lane.

The isolation tests are the important ones: a paper source must have no route into the live
money lane, and the analyzer must refuse to pick up its channel's messages.
"""

from __future__ import annotations

import pathlib
from decimal import Decimal
from typing import Any

import pytest

from fatty_trader.kaka import worker
from fatty_trader.kaka.paper import (
    LEVERAGE,
    MARGIN_PER_LEG_USDT,
    TAKER_FEE_RATE,
    add_leg,
    breakeven,
    close_trade,
    open_trade,
    pnl_usdt,
)
from fatty_trader.kaka.parser import KakaEvent, KakaEventType, parse_kaka_event

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LIVE_CHANNEL_ID = -1001252615519
KAKA_CHANNEL_ID = -1003763643270


def event(text: str) -> KakaEvent:
    return parse_kaka_event(text, message_id=1000)


# --------------------------------------------------------------------------- engine


def test_cancel_tp_is_target_removal_not_position_cancel() -> None:
    assert event("ETH Cancel TP").type is KakaEventType.TP_REMOVED


def test_management_preserves_explicit_symbol() -> None:
    for text in (
        "ETH Set sl at be",
        "ETH SL hit",
        "ETH Close",
        "ETH Remove tp",
        "ETH Tp -120",
        "ETH Take 2nd entry 110",
        "ETH Move sl -95",
        "ETH Cancelling the entry",
    ):
        assert parse_kaka_event(text, message_id=2).symbol == "ETHUSDT", text


def test_open_uses_stated_entry_and_our_sizing() -> None:
    trade = open_trade(event("Short $ETH | SL -2401 | ENTRY -2400"), market_price=None)

    assert trade.symbol == "ETHUSDT"
    assert trade.side == "SHORT"
    assert trade.entry_price == Decimal("2400")
    assert trade.stop_loss == Decimal("2401")
    assert trade.margin_usdt == MARGIN_PER_LEG_USDT
    assert trade.notional_usdt == MARGIN_PER_LEG_USDT * LEVERAGE


def test_market_entry_needs_a_price_and_refuses_without_one() -> None:
    market = event("Short $ETH | SL -2401")
    with pytest.raises(ValueError, match="no entry price"):
        open_trade(market, market_price=None)
    assert open_trade(market, market_price=Decimal("2400")).entry_price == Decimal("2400")


def test_stop_already_crossed_is_refused() -> None:
    # Same shape as the real ENA case: the stop sits on the wrong side of the market now.
    short_dead = parse_kaka_event("Short $ETH | SL -2401", message_id=1)
    with pytest.raises(ValueError, match="already crossed"):
        open_trade(short_dead, market_price=Decimal("2405"))  # stop 2401 <= entry for a short
    long_dead = parse_kaka_event("Long -$ETH | SL -2479", message_id=2)
    with pytest.raises(ValueError, match="already crossed"):
        open_trade(long_dead, market_price=Decimal("2470"))  # stop 2479 >= entry for a long


def test_second_entry_averages_into_one_position() -> None:
    trade = open_trade(event("Long -$ETH | ENTRY -100 | SL -90"), market_price=None)
    added = add_leg(trade, price=Decimal("110"), market_price=None)

    assert added.legs == 2
    assert added.entry_price == Decimal("40") / (Decimal("20") / 100 + Decimal("20") / 110)
    assert abs(
        added.notional_usdt / added.entry_price - (Decimal("20") / 100 + Decimal("20") / 110)
    ) < Decimal("1e-26")
    assert added.notional_usdt == Decimal("40")
    assert added.margin_usdt == Decimal("2")


def test_breakeven_moves_the_stop_to_entry() -> None:
    trade = open_trade(event("Long -$ETH | ENTRY -100 | SL -90"), market_price=None)
    assert breakeven(trade).stop_loss == Decimal("100")


def test_pnl_is_signed_and_charged_fees_on_both_sides() -> None:
    trade = open_trade(event("Long -$ETH | ENTRY -100 | SL -90"), market_price=None)

    expected = Decimal("20") * Decimal("0.10") - (Decimal("20") + Decimal("22")) * TAKER_FEE_RATE
    assert pnl_usdt(trade, Decimal("110")) == expected.quantize(Decimal("0.000001"))
    assert pnl_usdt(trade, Decimal("90")) < 0


def test_stop_hit_after_breakeven_is_a_scrape_not_a_loss() -> None:
    """Matches the channel's own 'SL got hit, but we ended at breakeven' message."""
    trade = open_trade(event("Long -$SOL | ENTRY -100 | SL -96"), market_price=None)
    at_be = breakeven(trade)
    closed = close_trade(at_be, exit_price=at_be.stop_loss, reason="stop-hit")

    assert closed.state == "closed"
    assert closed.realized_pnl_usdt == (Decimal("20") * TAKER_FEE_RATE * 2 * -1).quantize(
        Decimal("0.000001")
    )


def test_cannot_add_to_or_close_a_closed_trade() -> None:
    trade = open_trade(event("Long -$ETH | ENTRY -100 | SL -90"), market_price=None)
    closed = close_trade(trade, exit_price=Decimal("105"), reason="manual-close")
    with pytest.raises(ValueError):
        add_leg(closed, price=Decimal("101"), market_price=None)
    with pytest.raises(ValueError):
        close_trade(closed, exit_price=Decimal("106"), reason="manual-close")


# --------------------------------------------------------------------------- worker


class FakeCursor:
    def __init__(self, *, fetchone_row: Any = None, fetchall_rows: list[Any] | None = None) -> None:
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self._fetchone = fetchone_row
        self._fetchall = fetchall_rows or []

    def execute(self, statement: str, params: tuple[Any, ...] = ()) -> None:
        self.statements.append((" ".join(statement.split()), params))

    def fetchone(self) -> Any:
        return self._fetchone

    def fetchall(self) -> list[Any]:
        return self._fetchall

    def statements_matching(self, needle: str) -> list[tuple[str, tuple[Any, ...]]]:
        return [
            entry for entry in self.statements if needle in entry[0] or needle in repr(entry[1])
        ]


def test_duplicate_management_is_noop_before_target_or_price_lookup() -> None:
    from dataclasses import asdict

    trade = open_trade(event("Long ETH ENTRY 100 SL 90"), market_price=None)

    class DuplicateCursor(FakeCursor):
        def __init__(self):
            super().__init__(
                fetchone_row={"id": "11111111-1111-1111-1111-111111111111", **asdict(trade)}
            )

        def fetchall(self):
            if "FROM paper_kaka_events" in self.statements[-1][0]:
                return [{"source_message_id": 1000}]
            return []

    cursor = DuplicateCursor()
    prices = []
    worker.handle_event(cursor, event("ETH DCA 110"), lambda symbol: prices.append(symbol))
    assert not cursor.statements_matching("UPDATE paper_kaka_trades")
    assert prices == []
    assert not cursor.statements_matching("SELECT id, source_message_id")


def test_open_event_writes_a_paper_trade_only() -> None:
    cursor = FakeCursor(fetchone_row=None, fetchall_rows=[])

    worker.handle_event(
        cursor,
        event("🧨BTC LONG  SETUP | 📍 Entry: $76,150 | 🛑 SL: $75,210"),
        lambda _symbol: None,
    )

    assert len(cursor.statements_matching("INSERT INTO paper_kaka_trades")) == 1
    assert cursor.statements_matching("INSERT INTO paper_kaka_events")
    # Never the live ledger.
    for needle in ("dispatches", "live_order_intents", "fills"):
        assert cursor.statements_matching(needle) == []


def test_second_open_for_the_same_symbol_is_ignored() -> None:
    cursor = FakeCursor(
        fetchone_row={
            "id": "11111111-1111-1111-1111-111111111111",
            "source_message_id": 1,
            "symbol": "ETHUSDT",
            "side": "SHORT",
            "entry_price": Decimal("2400"),
            "stop_loss": Decimal("2401"),
            "take_profit": None,
            "dca_level": None,
            "notional_usdt": Decimal("20"),
            "margin_usdt": Decimal("1"),
            "leverage": 20,
            "legs": 1,
            "state": "open",
            "close_price": None,
            "close_reason": None,
            "realized_pnl_usdt": None,
        }
    )

    worker.handle_event(
        cursor,
        event("Short $ETH | SL -2401"),
        lambda _symbol: Decimal("2400"),
    )

    assert cursor.statements_matching("INSERT INTO paper_kaka_trades") == []
    assert cursor.statements_matching("OPEN_IGNORED")


def test_management_event_without_an_open_trade_is_recorded_not_invented() -> None:
    cursor = FakeCursor(fetchone_row=None, fetchall_rows=[])

    worker.handle_event(cursor, event("Set sl at be"), lambda _symbol: Decimal("100"))

    assert cursor.statements_matching("INSERT INTO paper_kaka_trades") == []
    assert cursor.statements_matching("UNMATCHED")


# --------------------------------------------------------------------------- isolation


def test_paper_lane_never_references_the_live_paths() -> None:
    package = REPO_ROOT / "src" / "fatty_trader" / "kaka"
    source = "\n".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))

    for forbidden in (
        "BitgetLiveClient",
        "live_order_intents",
        "fallback_protection",
        "venue_kill_switches",
        "BITGET_API_KEY",
        "BITGET_API_SECRET",
        "BITGET_EXECUTION_ENABLED",
        "async_venue",
    ):
        assert forbidden not in source, forbidden


def test_paper_service_is_absent_from_live_deployment() -> None:
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "paper-kaka:" not in compose
    assert "PAPER_KAKA_CHANNEL_ID" not in compose
    assert "TRADER_MODE: DEMO" not in compose
    assert "TELEGRAM_SOURCE_CHANNELS: ${TELEGRAM_SOURCE_CHANNELS:-@fattyfatclub}" in compose


def test_analyzer_claim_is_scoped_to_the_live_channel() -> None:
    """Adding a paper source channel must never let its messages create live dispatches."""
    source = (REPO_ROOT / "src" / "fatty_trader" / "analyzer" / "postgres_worker.py").read_text(
        encoding="utf-8"
    )

    assert "AND channel_id = ANY(%s)" in source
    assert "channel_ids: tuple[int, ...] = (-1001252615519,)" in source


def test_analyzer_channel_ids_defaults_to_the_live_channel_only() -> None:
    from fatty_trader.service import analyzer_channel_ids

    assert analyzer_channel_ids({}) == (LIVE_CHANNEL_ID,)
    assert KAKA_CHANNEL_ID not in analyzer_channel_ids({})
    assert analyzer_channel_ids({"ANALYZER_CHANNEL_IDS": "-1001252615519,-1003763643270"}) == (
        LIVE_CHANNEL_ID,
        KAKA_CHANNEL_ID,
    )
    with pytest.raises(ValueError):
        analyzer_channel_ids({"ANALYZER_CHANNEL_IDS": "abc"})


def test_paper_shapes_are_all_classified_on_real_messages() -> None:
    """Every real message shape from the channel dump must classify, and OPEN must be valid."""
    shapes = {
        "🧨BTC LONG  SETUP\n📍 Entry: $76,150\n🛑 SL: $75,210": KakaEventType.OPEN,
        "Short $ETH | SL -2401": KakaEventType.OPEN,
        "Set sl at be": KakaEventType.BREAKEVEN,
        "Tp -2540": KakaEventType.TP,
        "SL hit | Loss -46$": KakaEventType.STOP_HIT,
        "Close | Profit +95$": KakaEventType.CLOSE,
        "Remove tp": KakaEventType.TP_REMOVED,
        "Take 2nd entry 2475": KakaEventType.ADD,
        "Move sl -74,980": KakaEventType.STOP_MOVE,
        "Cancelling the entry for now.": KakaEventType.CANCEL,
        "👍👍👍": KakaEventType.NOISE,
    }
    for text, expected in shapes.items():
        assert parse_kaka_event(text, message_id=1).type is expected, text


def test_paper_worker_uses_dict_rows() -> None:
    """store.load_open_trade indexes columns by name; tuple rows raised TypeError in prod."""
    source = (REPO_ROOT / "src" / "fatty_trader" / "service.py").read_text(encoding="utf-8")
    block = source.split("async def run_paper_kaka", 1)[1].split("\ndef ", 1)[0]
    assert "row_factory=dict_row" in block


def test_digest_is_idempotent_per_day_and_renders_as_html() -> None:
    from fatty_trader.kaka.worker import build_digest_text, enqueue_digest
    from fatty_trader.notifications import format_notification_html

    class DigestCursor(FakeCursor):
        def __init__(self) -> None:
            super().__init__(
                fetchone_row={
                    "closed_today": 2,
                    "wins_today": 1,
                    "pnl_today": Decimal("-0.31"),
                    "closed_all": 27,
                    "wins_all": 15,
                    "pnl_all": Decimal("-2.3959"),
                    "open_now": 1,
                },
                fetchall_rows=[
                    {
                        "symbol": "ETHUSDT",
                        "side": "LONG",
                        "entry_price": Decimal("2524"),
                        "stop_loss": Decimal("2480"),
                        "legs": 1,
                    }
                ],
            )
            self._rows = [self._fetchone, ("digest-id",)]

        def fetchone(self) -> Any:
            return self._rows.pop(0) if self._rows else None

    cursor = DigestCursor()
    text = build_digest_text(cursor, day_start="2026-09-27T00:00:00+00:00")
    assert "win rate 55.6%" in text
    assert "-2.3959 USDT" in text
    assert "ETHUSDT LONG" in text

    queued = enqueue_digest(cursor, dedup_key="kaka-paper-digest:2026-09-28", text=text)
    assert queued is True
    assert cursor.statements_matching("INSERT INTO notifications_outbox")

    html = format_notification_html({"kind": "kaka-paper-digest", "text": text})
    assert "Kaka paper digest" in html
    assert "<pre>" in html


def test_media_only_message_is_recorded_and_never_invented() -> None:
    cursor = FakeCursor(fetchone_row=None, fetchall_rows=[])

    worker._handle_media_only(
        cursor,
        message_id=321,
        media_path="/app/runtime/media/downloads/-1003763643270/321/source.jpg",
        image_analyzer=None,
        market_price_lookup=lambda _symbol: Decimal("100"),
        counts={"opened": 0, "updated": 0, "closed": 0, "refused": 0, "noise": 0},
    )

    assert cursor.statements_matching("MEDIA_ONLY")
    assert cursor.statements_matching("INSERT INTO paper_kaka_trades") == []


def test_media_image_signal_opens_a_paper_trade_when_analysis_is_enabled() -> None:
    cursor = FakeCursor(fetchone_row=None, fetchall_rows=[])

    def analyzer(_path: str, *, message_id: int):
        return parse_kaka_event("Short $ETH | ENTRY -2400 | SL -2401", message_id=message_id)

    counts = {"opened": 0, "updated": 0, "closed": 0, "refused": 0, "noise": 0}
    worker._handle_media_only(
        cursor,
        message_id=321,
        media_path="/tmp/chart.jpg",
        image_analyzer=analyzer,
        market_price_lookup=lambda _symbol: Decimal("2400"),
        counts=counts,
    )

    assert counts["opened"] == 1
    assert cursor.statements_matching("INSERT INTO paper_kaka_trades")


def test_media_image_failure_is_recorded_not_guessed() -> None:
    cursor = FakeCursor(fetchone_row=None, fetchall_rows=[])

    def analyzer(_path: str, *, message_id: int):
        raise RuntimeError("vision unavailable")

    counts = {"opened": 0, "updated": 0, "closed": 0, "refused": 0, "noise": 0}
    worker._handle_media_only(
        cursor,
        message_id=321,
        media_path="/tmp/chart.jpg",
        image_analyzer=analyzer,
        market_price_lookup=lambda _symbol: Decimal("100"),
        counts=counts,
    )

    assert counts["refused"] == 1
    assert cursor.statements_matching("REFUSED:IMAGE")
