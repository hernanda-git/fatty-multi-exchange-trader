from decimal import Decimal

import pytest

from fatty_trader.exchanges.bitget.metadata import metadata_from_contract
from fatty_trader.exchanges.bitget.validation import OrderValidationError, validate_order


def test_metadata_is_resolved_from_requested_contract_fields() -> None:
    meta = metadata_from_contract(
        {
            "symbol": "BTCUSDT",
            "pricePlace": "2",
            "priceEndStep": "1",
            "sizeMultiplier": "0.001",
            "minTradeNum": "0.001",
            "maxOrderQty": "100",
            "maxMarketOrderQty": "100",
            "minTradeUSDT": "5",
            "maxLever": "50",
        }
    )
    assert meta.symbol == "BTCUSDT"
    assert meta.price_tick == Decimal("0.01")
    assert meta.size_step == Decimal("0.001")
    assert meta.min_order_qty == Decimal("0.001")
    assert meta.min_notional == Decimal("5")
    assert meta.max_leverage == 50


def test_metadata_accepts_current_bitget_v2_max_order_fields() -> None:
    meta = metadata_from_contract(
        {
            "symbol": "BTCUSDT",
            "pricePlace": "1",
            "priceEndStep": "1",
            "sizeMultiplier": "0.0001",
            "minTradeNum": "0.0001",
            "maxOrderQty": "1000",
            "maxMarketOrderQty": "500",
            "minTradeUSDT": "5",
            "maxLever": "125",
        }
    )
    assert meta.max_order_qty == Decimal("1000")
    assert meta.max_market_order_qty == Decimal("500")


def test_metadata_never_uses_position_count_as_quantity_limit() -> None:
    meta = metadata_from_contract(
        {
            "symbol": "BTCUSDT",
            "pricePlace": "1",
            "priceEndStep": "1",
            "sizeMultiplier": "0.0001",
            "minTradeNum": "0.0001",
            "maxOrderQty": "",
            "maxMarketOrderQty": "",
            "maxPositionNum": "150",
            "minTradeUSDT": "5",
            "maxLever": "125",
        }
    )
    assert meta.max_order_qty is None
    assert meta.max_market_order_qty is None


def test_validation_rejects_wrong_symbol_precision_and_notional() -> None:
    meta = metadata_from_contract(
        {
            "symbol": "DOGEUSDT",
            "pricePlace": "5",
            "priceEndStep": "1",
            "sizeMultiplier": "1",
            "minTradeNum": "10",
            "maxOrderQty": "100000",
            "maxMarketOrderQty": "100000",
            "minTradeUSDT": "5",
            "maxLever": "20",
        }
    )
    with pytest.raises(OrderValidationError, match="symbol"):
        validate_order("BTCUSDT", "BUY", Decimal("0.1"), Decimal("100"), meta)
    with pytest.raises(OrderValidationError, match="price tick"):
        validate_order("DOGEUSDT", "BUY", Decimal("0.123456"), Decimal("100"), meta)
    with pytest.raises(OrderValidationError, match="minimum notional"):
        validate_order("DOGEUSDT", "BUY", Decimal("0.1"), Decimal("10"), meta)


def test_entry_is_not_reduce_only_but_exit_must_be_reduce_only() -> None:
    meta = metadata_from_contract(
        {
            "symbol": "BTCUSDT",
            "pricePlace": "2",
            "priceEndStep": "1",
            "sizeMultiplier": "0.001",
            "minTradeNum": "0.001",
            "maxOrderQty": "100",
            "maxMarketOrderQty": "100",
            "minTradeUSDT": "5",
            "maxLever": "50",
        }
    )
    validate_order("BTCUSDT", "BUY", Decimal("50000.00"), Decimal("0.001"), meta)
    with pytest.raises(OrderValidationError, match="reduce-only"):
        validate_order(
            "BTCUSDT",
            "SELL",
            Decimal("50000.00"),
            Decimal("0.001"),
            meta,
            reduce_only=False,
            exit_order=True,
        )


def test_price_end_step_scales_the_decimal_precision() -> None:
    meta = metadata_from_contract(
        {
            "symbol": "BTCUSDT",
            "pricePlace": "1",
            "priceEndStep": "5",
            "sizeMultiplier": "0.001",
            "minTradeNum": "0.001",
            "maxMarketOrderQty": "500",
            "maxOrderQty": "1000",
            "minTradeUSDT": "5",
            "maxLever": "50",
        }
    )
    assert meta.price_tick == Decimal("0.5")
    with pytest.raises(OrderValidationError, match="price tick"):
        validate_order("BTCUSDT", "BUY", Decimal("50000.2"), Decimal("0.001"), meta)
    validate_order("BTCUSDT", "BUY", Decimal("50000.5"), Decimal("0.001"), meta)
    with pytest.raises(OrderValidationError, match="maximum"):
        validate_order(
            "BTCUSDT", "BUY", Decimal("50000"), Decimal("501"), meta, order_type="market"
        )
    validate_order("BTCUSDT", "BUY", Decimal("50000"), Decimal("501"), meta, order_type="limit")
