from decimal import Decimal

import pytest

from fatty_trader.exchanges.bitget.metadata import metadata_from_contract
from fatty_trader.exchanges.bitget.validation import OrderValidationError, validate_order


def test_metadata_is_resolved_from_requested_contract_fields() -> None:
    meta = metadata_from_contract(
        {
            "symbol": "BTCUSDT",
            "pricePlace": "2",
            "priceEndStep": "0.01",
            "sizeMultiplier": "0.001",
            "minTradeNum": "0.001",
            "maxTradeNum": "100",
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


def test_metadata_uses_non_empty_position_limit_when_order_limit_is_blank() -> None:
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
    assert meta.max_order_qty == Decimal("150")


def test_validation_rejects_wrong_symbol_precision_and_notional() -> None:
    meta = metadata_from_contract(
        {
            "symbol": "DOGEUSDT",
            "pricePlace": "5",
            "priceEndStep": "0.00001",
            "sizeMultiplier": "1",
            "minTradeNum": "10",
            "maxTradeNum": "100000",
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
            "priceEndStep": "0.01",
            "sizeMultiplier": "0.001",
            "minTradeNum": "0.001",
            "maxTradeNum": "100",
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
