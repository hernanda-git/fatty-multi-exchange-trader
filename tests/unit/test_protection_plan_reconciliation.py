"""Protection plan legs are never terminal on one absent listing.

LIVE 2026-11-20 RAVE: `place-pos-tpsl` returned plan ids, the next read did not
show the guard yet, and the generic order-detail path wrote `rejected` while the
venue stop was resting. The stop executed 43 minutes later and the monitor saw
an order it could not account for, latching the entry lane.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from fatty_trader.exchanges.bitget.live import LiveIntentRecord
from fatty_trader.exchanges.bitget.reconciliation import reconcile_unknown_intent


def sl_intent() -> LiveIntentRecord:
    return LiveIntentRecord(
        exchange="bitget",
        client_oid="live-bitget-RAVEUSDT-59db70c391105568-sl",
        symbol="RAVEUSDT",
        side="SELL",
        role="SL",
        requested_qty=Decimal("185"),
        state="unknown",
        provider_order_id="1507487858898726912",
    )


def reads(
    *,
    plans: Any,
    fills: list[dict[str, Any]] | None = None,
    position: Any = None,
    pending: Any = None,
    detail_calls: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    async def read_plans(_symbol: str) -> Any:
        if isinstance(plans, Exception):
            raise plans
        return plans

    async def read_fills(_symbol: str) -> Any:
        return fills if fills is not None else []

    async def read_position(_symbol: str) -> Any:
        return position if position is not None else []

    async def read_pending(_symbol: str) -> Any:
        return pending if pending is not None else []

    async def read_detail(symbol: str, oid: str) -> Any:
        if detail_calls is not None:
            detail_calls.append((symbol, oid))
        raise AssertionError("plan legs must never be read through order detail")

    return {
        "read_order_detail": read_detail,
        "read_fills": read_fills,
        "read_position": read_position,
        "read_pending_orders": read_pending,
        "read_pending_plans": read_plans,
    }


@pytest.mark.parametrize("role", ["SL", "TP"])
async def test_resting_plan_is_acknowledged(role: str) -> None:
    intent = sl_intent()
    intent.role = role
    plan_field = "stopLossClientOid" if role == "SL" else "stopSurplusClientOid"
    details: list[tuple[str, str]] = []
    result = await reconcile_unknown_intent(
        intent,
        **reads(
            plans=[
                {
                    "orderId": intent.provider_order_id,
                    plan_field: intent.client_oid,
                }
            ],
            position=[{"symbol": "RAVEUSDT", "total": "185"}],
            detail_calls=details,
        ),
    )
    assert result.state == "acknowledged"
    assert result.provider_order_id == "1507487858898726912"
    assert details == []


async def test_unreadable_plan_listing_is_retryable() -> None:
    details: list[tuple[str, str]] = []
    result = await reconcile_unknown_intent(
        sl_intent(),
        **reads(plans=RuntimeError("provider read failed"), detail_calls=details),
    )
    assert result.state == "unknown"
    assert result.last_error == "protection-plan-read-unavailable"
    assert details == []


async def test_absent_plan_with_open_position_is_never_rejected() -> None:
    details: list[tuple[str, str]] = []
    result = await reconcile_unknown_intent(
        sl_intent(),
        **reads(
            plans={"entrustedList": []},
            position=[{"symbol": "RAVEUSDT", "total": "185"}],
            detail_calls=details,
        ),
    )
    assert result.state == "unknown"
    assert result.last_error == "protection-plan-not-observed"
    assert details == []


async def test_absent_plan_flat_without_fills_is_cancelled() -> None:
    result = await reconcile_unknown_intent(
        sl_intent(),
        **reads(plans=[], position=[], fills=[], pending=[]),
    )
    assert result.state == "cancelled"
    assert result.last_error == "protection-plan-cancelled"


async def test_executed_plan_binds_its_fill() -> None:
    result = await reconcile_unknown_intent(
        sl_intent(),
        **reads(
            plans=[],
            position=[],
            pending=[],
            fills=[
                {
                    "orderId": "1507487858898726912",
                    "fillId": "1507498769521557504",
                    "baseVolume": "185",
                    "price": "0.12227",
                    "feeDetail": [{"totalFee": "-0.0136", "feeCoin": "USDT"}],
                }
            ],
        ),
    )
    assert result.state == "filled"
    assert result.filled_qty == Decimal("185")
    assert result.avg_price == Decimal("0.12227")
    assert result.provider_fill_ids == ("1507498769521557504",)
