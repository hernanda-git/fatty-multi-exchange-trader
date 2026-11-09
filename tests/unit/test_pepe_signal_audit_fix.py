"""Unit regression proofs for the Bitget PEPE 3R trade and historical signal parsing fixes."""

from __future__ import annotations

from decimal import Decimal
import pytest

from fatty_trader.analyzer.deterministic_parser import parse_explicit_signal
from fatty_trader.analyzer.image_analysis import signal_from_image_json
from fatty_trader.domain.enums import Direction
from fatty_trader.domain.models import CanonicalSignal
from fatty_trader.risk.price_scale import align_price_scale


def test_pepe_short_limit_message_16468_deterministic_parse() -> None:
    text = (
        "$PEPE short limit around 0.51 - 0.513\n\n"
        "Stoploss placed right above todays high at 0.521"
    )
    sig = parse_explicit_signal(text, message_id=16468)
    assert sig is not None
    assert sig.pair_token == "PEPE"
    assert sig.direction is Direction.SHORT
    assert sig.entry_price == Decimal("0.51")
    assert sig.stop_loss == Decimal("0.521")
    assert sig.take_profits == ()


def test_pepe_image_json_with_range_entry_and_chart_scale_target() -> None:
    data = {
        "setup": {
            "side": "SHORT",
            "asset": "PEPE",
            "entry": "0.51–0.513",
            "action": "ENTER",
            "levels": {
                "chart_entry_zone": "0.50546e-5–0.51928e-5",
                "lower_marked_level": "0.46913e-5",
            },
            "stop_loss": "0.521",
            "take_profits": ["0.46933e-5"],
        }
    }
    sig = signal_from_image_json(data, message_id=16468, source_revision="a" * 64)
    assert sig is not None
    assert sig.pair_token == "PEPE"
    assert sig.direction is Direction.SHORT
    assert sig.entry_price == Decimal("0.51")
    assert sig.stop_loss == Decimal("0.521")
    assert len(sig.take_profits) == 1
    # Scaled to entry magnitude: 0.46933 (below entry 0.51)
    assert sig.take_profits[0] == Decimal("0.4693300000")


def test_align_price_scale_pepe_with_chart_scale_target() -> None:
    # When signal has entry=0.51, SL=0.521, and TP is in market units 0.0000046933
    entry, sl, tps, scale = align_price_scale(
        entry_price=Decimal("0.51"),
        stop_loss=Decimal("0.521"),
        take_profits=(Decimal("0.0000046933"),),
        market_price=Decimal("0.00000510"),
        direction="SHORT",
    )
    assert scale == Decimal("100000")
    assert entry == Decimal("0.0000051")
    assert sl == Decimal("0.00000521")
    # Take profit was already in market scale, so it is preserved without double-division
    assert tps == (Decimal("0.0000046933"),)


def test_align_price_scale_pepe_with_unscaled_target() -> None:
    # When signal has all values in 100k scale (entry=0.51, SL=0.521, TP=0.46933)
    entry, sl, tps, scale = align_price_scale(
        entry_price=Decimal("0.51"),
        stop_loss=Decimal("0.521"),
        take_profits=(Decimal("0.46933"),),
        market_price=Decimal("0.00000510"),
        direction="SHORT",
    )
    assert scale == Decimal("100000")
    assert entry == Decimal("0.0000051")
    assert sl == Decimal("0.00000521")
    assert tps == (Decimal("0.0000046933"),)


def test_historical_missed_signals_now_parse_cleanly() -> None:
    # Message 16434: TRUMP LONG
    sig_trump = parse_explicit_signal(
        "$TRUMP LONG @1.683\n\nlonging again here with sl below 1.652",
        message_id=16434,
    )
    assert sig_trump is not None
    assert sig_trump.pair_token == "TRUMP"
    assert sig_trump.direction is Direction.LONG
    assert sig_trump.entry_price == Decimal("1.683")
    assert sig_trump.stop_loss == Decimal("1.652")

    # Message 16340: NEAR long limits between range
    sig_near = parse_explicit_signal(
        "$NEAR placed long limits between 5.03 - 4.93\n\n"
        "For me that area is the midrange on daily timeframe and a high liquidity zone!\n\n"
        "Sl below 4.885",
        message_id=16340,
    )
    assert sig_near is not None
    assert sig_near.pair_token == "NEAR"
    assert sig_near.direction is Direction.LONG
    assert sig_near.entry_price == Decimal("5.03")
    assert sig_near.stop_loss == Decimal("4.885")

    # Message 16358: PUMP short limit
    sig_pump = parse_explicit_signal(
        "#PUMP $PUMP placing short limit at 0.004195\n\nSl: 0.004285",
        message_id=16358,
    )
    assert sig_pump is not None
    assert sig_pump.pair_token == "PUMP"
    assert sig_pump.direction is Direction.SHORT
    assert sig_pump.entry_price == Decimal("0.004195")
    assert sig_pump.stop_loss == Decimal("0.004285")

    # Message 16354: DASH with STOPPOSS typo
    sig_dash = parse_explicit_signal(
        "#DASH $DASH LONG TRADE\n\nENTRY: 53.25\n\nTARGETS: 55.6 - 59.6\n\nSTOPPOSS: 51.95",
        message_id=16354,
    )
    assert sig_dash is not None
    assert sig_dash.pair_token == "DASH"
    assert sig_dash.direction is Direction.LONG
    assert sig_dash.entry_price == Decimal("53.25")
    assert sig_dash.stop_loss == Decimal("51.95")
    assert sig_dash.take_profits == (Decimal("55.6"), Decimal("59.6"))
