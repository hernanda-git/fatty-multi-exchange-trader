"""Missing-detail entry fills are execution evidence, not proof of a flat position."""

from decimal import Decimal
from typing import Any

import pytest

from fatty_trader.exchanges.bitget.live import LiveIntentRecord, LiveOrderStatus
from fatty_trader.exchanges.bitget.reconciliation import classify_missing_detail


def entry_intent(role: str = "ENTRY") -> LiveIntentRecord:
    return LiveIntentRecord(
        exchange="bitget",
        client_oid="entry-missing-detail",
        symbol="BTCUSDT",
        side="BUY",
        role=role,
        requested_qty=Decimal("0.01"),
        state="unknown",
        provider_order_id="provider-entry",
    )


async def classify(
    *,
    quantity: str,
    position: Any,
    pending: Any,
    role: str = "ENTRY",
    complete: bool = True,
    match_by: str = "clientOid",
) -> Any:
    intent = entry_intent(role)

    async def read_fills(_: str) -> Any:
        row = {
            match_by: intent.client_oid if match_by == "clientOid" else "provider-entry",
            "fillId": "actual-fill",
            "baseVolume": quantity,
            "price": "50000",
            "feeDetail": [{"totalFee": "-0.12", "feeCoin": "USDT"}],
        }
        return {
            "fillList": [row] if Decimal(quantity) > 0 else [],
            "endId": "" if complete else "next-page",
        }

    async def read_position(_: str) -> Any:
        return position

    async def read_pending(_: str) -> Any:
        return pending

    return await classify_missing_detail(
        intent,
        read_fills=read_fills,
        read_position=read_position,
        read_pending_orders=read_pending,
        provider_order_id=intent.provider_order_id,
        missing_order_confirmed=True,
    )


@pytest.mark.parametrize(
    ("quantity", "expected"),
    [("0.01", LiveOrderStatus.FILLED), ("0.004", LiveOrderStatus.PARTIAL)],
)
@pytest.mark.parametrize("match_by", ["clientOid", "orderId"])
async def test_matched_entry_fills_with_open_position(quantity, expected, match_by):
    outcome = await classify(
        quantity=quantity,
        position=[{"symbol": "BTCUSDT", "total": quantity}],
        pending=[],
        match_by=match_by,
    )
    assert outcome.status is expected
    assert outcome.filled_qty == Decimal(quantity)
    assert outcome.avg_price == Decimal("50000")
    assert outcome.fee == Decimal("0.12")
    assert outcome.provider_fill_ids == ("actual-fill",)
    assert outcome.provider_order_id == "provider-entry"


@pytest.mark.parametrize("quantity", ["0.004", "0.01"])
async def test_entry_fills_remain_actual_with_pending_residual(quantity):
    outcome = await classify(
        quantity=quantity,
        position=[{"symbol": "BTCUSDT", "total": quantity}],
        pending=[{"symbol": "BTCUSDT", "clientOid": "entry-missing-detail"}],
    )
    expected = LiveOrderStatus.PARTIAL if quantity == "0.004" else LiveOrderStatus.FILLED
    assert outcome.status is expected
    assert outcome.filled_qty == Decimal(quantity)
    assert outcome.provider_fills[0]["baseVolume"] == quantity


@pytest.mark.parametrize(
    ("role", "position", "pending", "complete", "quantity", "expected"),
    [
        ("ENTRY", [], [], True, "0.01", LiveOrderStatus.FILLED),
        ("ENTRY", [{"total": "0.01"}], [], False, "0.01", LiveOrderStatus.UNKNOWN),
        ("ENTRY", [{"total": "0.01"}], [], True, "0", LiveOrderStatus.UNKNOWN),
        ("ENTRY", [], [], True, "0", LiveOrderStatus.REJECTED),
        ("ENTRY", None, None, True, "0", LiveOrderStatus.UNKNOWN),
        ("CLOSE", [{"total": "0.01"}], [], True, "0.01", LiveOrderStatus.UNKNOWN),
        ("CLOSE", [], [{"symbol": "BTCUSDT"}], True, "0.01", LiveOrderStatus.UNKNOWN),
        ("CLOSE", None, [], True, "0.01", LiveOrderStatus.UNKNOWN),
        ("CLOSE", [], [], False, "0.01", LiveOrderStatus.UNKNOWN),
        ("CLOSE", [], [], True, "0.01", LiveOrderStatus.FILLED),
        ("CLOSE", [], [], True, "0.004", LiveOrderStatus.PARTIAL),
        ("CLOSE", [], [], True, "0", LiveOrderStatus.REJECTED),
    ],
)
async def test_close_fences_and_entry_fail_closed_controls(
    role, position, pending, complete, quantity, expected
):
    outcome = await classify(
        quantity=quantity, position=position, pending=pending, role=role, complete=complete
    )
    assert outcome.status is expected
    assert outcome.filled_qty == Decimal(quantity)


@pytest.mark.parametrize("quantity", ["0.004", "0.01"])
async def test_unknown_entry_persists_provider_fills_with_open_position(quantity):
    from fatty_trader.exchanges.bitget.client import BitgetApiError
    from fatty_trader.exchanges.bitget.reconciliation import reconcile_unknown_intent

    intent = entry_intent()

    async def read_detail(_: str, __: str) -> Any:
        raise BitgetApiError("order not found", code="40109")

    async def read_fills(_: str) -> Any:
        return [
            {
                "orderId": "provider-entry",
                "fillId": "actual-fill",
                "baseVolume": quantity,
                "price": "50000",
            }
        ]

    async def read_position(_: str) -> Any:
        return [{"symbol": "BTCUSDT", "total": quantity}]

    async def read_pending(_: str) -> Any:
        return [{"symbol": "BTCUSDT", "clientOid": intent.client_oid}]

    result = await reconcile_unknown_intent(
        intent,
        read_order_detail=read_detail,
        read_fills=read_fills,
        read_position=read_position,
        read_pending_orders=read_pending,
    )
    assert result.state == ("partially_filled" if quantity == "0.004" else "filled")
    assert result.filled_qty == Decimal(quantity)
    assert result.provider_fill_ids == ("actual-fill",)
    assert result.avg_price == Decimal("50000")
