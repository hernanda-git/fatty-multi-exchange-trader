"""Offline reconciliation of Bitget provider exits without a bot client OID."""

from __future__ import annotations

from decimal import Decimal

import pytest

from fatty_trader.exchanges.bitget.live import InMemoryLiveIntentStore
from fatty_trader.exchanges.bitget.provider_fill_reconciliation import (
    ProviderFillReconciliationError,
    reconcile_provider_exit,
)

_SYSTEM_LIQUIDATION = {
    "instId": "WLDUSDT",
    "side": "sell",
    "ordId": "1483115597766737921",
    "tradeId": "1483115597766737921-fill",
    "fillSz": "91",
    "fillPx": "0.3841",
    "fillFee": "-0.02097366",
    "fillFeeCcy": "USDT",
    "profit": "-1.17999853",
    "enterPointSource": "SYS",
    "tradeSide": "burst_sell_single",
    "fillTime": "1790037434000",
}


def test_system_liquidation_creates_one_reconciled_close_and_fill() -> None:
    store = InMemoryLiveIntentStore()

    first = reconcile_provider_exit(store, _SYSTEM_LIQUIDATION)
    second = reconcile_provider_exit(store, dict(_SYSTEM_LIQUIDATION))

    assert first.source == "SYSTEM_LIQUIDATION"
    assert first.client_oid == second.client_oid
    assert first.provider_order_id == "1483115597766737921"
    assert first.provider_fill_id == "1483115597766737921-fill"
    record = store.get(first.client_oid)
    assert record is not None
    assert record.role == "CLOSE"
    assert record.state == "filled"
    assert record.filled_qty == Decimal("91")
    assert record.avg_price == Decimal("0.3841")
    assert record.fee == Decimal("0.02097366")
    assert record.provider_fill_ids == ("1483115597766737921-fill",)
    assert len(store.fills) == 1
    assert store.provider_events == [
        {
            "exchange": "bitget",
            "provider_fill_id": "1483115597766737921-fill",
            "source": "SYSTEM_LIQUIDATION",
        }
    ]


def test_provider_fill_with_client_oid_is_not_treated_as_unmatched() -> None:
    payload = dict(_SYSTEM_LIQUIDATION)
    payload["clientOid"] = "live-bitget-WLDUSDT-entry"

    with pytest.raises(ProviderFillReconciliationError, match="client"):
        reconcile_provider_exit(InMemoryLiveIntentStore(), payload)


def test_provider_fill_requires_positive_quantity_and_price() -> None:
    for field in ("fillSz", "fillPx"):
        payload = dict(_SYSTEM_LIQUIDATION)
        payload[field] = "0"
        with pytest.raises(ProviderFillReconciliationError):
            reconcile_provider_exit(InMemoryLiveIntentStore(), payload)


def test_real_bitget_fee_detail_shape_is_normalized() -> None:
    payload = dict(_SYSTEM_LIQUIDATION)
    payload.pop("fillFee")
    payload["feeDetail"] = [{"feeCoin": "USDT", "totalFee": "-0.02097366"}]

    result = reconcile_provider_exit(InMemoryLiveIntentStore(), payload)

    assert result.record.fee == Decimal("0.02097366")
