import asyncio
from decimal import Decimal

from fatty_trader.exchanges.bitget.read_model import BitgetPositionState
from fatty_trader.exchanges.bitget.reconciliation_live import (
    NativeProtectionExpectation,
    ProtectionReadiness,
    confirm_native_protection,
    evaluate_position_protection,
)
from fatty_trader.execution.protection import ProtectionState


def _position(*, stop_loss_id: str | None, take_profit_id: str | None) -> BitgetPositionState:
    from decimal import Decimal

    return BitgetPositionState(
        symbol="BTCUSDT",
        hold_side="long",
        quantity=Decimal("0.01"),
        entry_price=Decimal("60000"),
        margin_mode="isolated",
        leverage=Decimal("20"),
        stop_loss_id=stop_loss_id,
        take_profit_id=take_profit_id,
    )


async def _read(position: BitgetPositionState | None) -> BitgetPositionState | None:
    return position


def test_open_position_with_native_stop_and_profit_is_ready() -> None:
    state = asyncio.run(
        evaluate_position_protection(
            lambda: _read(_position(stop_loss_id="sl", take_profit_id="tp"))
        )
    )
    assert state is ProtectionReadiness.PROTECTED


def test_open_position_missing_stop_is_kill_switch_condition() -> None:
    state = asyncio.run(
        evaluate_position_protection(
            lambda: _read(_position(stop_loss_id=None, take_profit_id="tp"))
        )
    )
    assert state is ProtectionReadiness.MISSING_STOP_LOSS


def test_flat_account_has_no_protection_requirement() -> None:
    state = asyncio.run(evaluate_position_protection(lambda: _read(None)))
    assert state is ProtectionReadiness.FLAT


def test_native_confirmation_rejects_changed_margin_mode_even_when_plan_ids_exist() -> None:
    async def position() -> list[dict[str, str]]:
        return [{"total": "0.01", "marginMode": "crossed"}]

    async def plans() -> list[dict[str, str]]:
        return [
            {"planType": "loss_plan", "size": "0.01"},
            {"planType": "profit_plan", "size": "0.01"},
        ]

    report = asyncio.run(
        confirm_native_protection(position, plans, expected_quantity=Decimal("0.01"))
    )

    assert report.state is ProtectionState.DEGRADED
    assert report.reason == "margin-mode-not-isolated"


def test_native_confirmation_falls_back_to_position_fields_when_plans_unsupported() -> None:
    """Symbols like GRASSUSDT reject orders-plan-pending with 400172."""

    async def position() -> list[dict[str, str | None]]:
        return [
            {
                "total": "15",
                "marginMode": "isolated",
                "stopLossId": "sl-123",
                "takeProfitId": "tp-456",
            }
        ]

    async def plans() -> list[dict[str, str]]:
        raise Exception("400172 Parameter verification failed")

    report = asyncio.run(
        confirm_native_protection(position, plans, expected_quantity=Decimal("15"))
    )

    assert report.state is ProtectionState.VENUE_PROTECTED


def test_native_confirmation_fails_when_plans_unsupported_and_no_stop_loss() -> None:
    async def position() -> list[dict[str, str | None]]:
        return [
            {
                "total": "15",
                "marginMode": "isolated",
                "stopLossId": None,
                "takeProfitId": "tp-456",
            }
        ]

    async def plans() -> list[dict[str, str]]:
        raise Exception("400172 Parameter verification failed")

    report = asyncio.run(
        confirm_native_protection(position, plans, expected_quantity=Decimal("15"))
    )

    assert report.state is ProtectionState.DEGRADED
    assert report.reason == "missing-stop-loss"


def _exact_position() -> list[dict[str, str]]:
    return [
        {
            "symbol": "WLDUSDT",
            "holdSide": "buy",
            "total": "91",
            "marginMode": "isolated",
            "posMode": "one_way_mode",
            "stopLossId": "sl-plan-1",
            "takeProfitId": "tp-plan-1",
        }
    ]


def _exact_plans() -> list[dict[str, str]]:
    return [
        {
            "symbol": "WLDUSDT",
            "holdSide": "buy",
            "planType": "pos_loss",
            "orderId": "sl-plan-1",
            "stopLossClientOid": "entry-sl",
            "triggerPrice": "0.388",
            "executePrice": "0",
            "triggerType": "mark_price",
            "planStatus": "live",
            "size": "",
        },
        {
            "symbol": "WLDUSDT",
            "holdSide": "buy",
            "planType": "pos_profit",
            "orderId": "tp-plan-1",
            "stopSurplusClientOid": "entry-tp",
            "triggerPrice": "0.427",
            "executePrice": "0",
            "triggerType": "mark_price",
            "planStatus": "live",
            "size": "",
        },
    ]


def _expectation(**overrides: object) -> NativeProtectionExpectation:
    values: dict[str, object] = {
        "symbol": "WLDUSDT",
        "hold_side": "buy",
        "quantity": Decimal("91"),
        "stop_loss": Decimal("0.388"),
        "take_profit": Decimal("0.427"),
        "stop_loss_client_oid": "entry-sl",
        "take_profit_client_oid": "entry-tp",
        "stop_loss_provider_order_id": "sl-plan-1",
        "take_profit_provider_order_id": "tp-plan-1",
    }
    values.update(overrides)
    return NativeProtectionExpectation(**values)  # type: ignore[arg-type]


def test_exact_native_confirmation_requires_all_provider_protection_fields() -> None:
    async def position() -> list[dict[str, str]]:
        return _exact_position()

    async def plans() -> list[dict[str, str]]:
        return _exact_plans()

    report = asyncio.run(
        confirm_native_protection(
            position,
            plans,
            expectation=_expectation(),
        )
    )

    assert report.state is ProtectionState.VENUE_PROTECTED
    assert report.observed_quantity == Decimal("91")
    assert report.reason is None


def test_exact_native_confirmation_rejects_wrong_trigger_and_market_mode() -> None:
    async def position() -> list[dict[str, str]]:
        return _exact_position()

    async def plans() -> list[dict[str, str]]:
        rows = _exact_plans()
        rows[0]["triggerPrice"] = "0.389"
        rows[1]["executePrice"] = "0.427"
        return rows

    report = asyncio.run(confirm_native_protection(position, plans, expectation=_expectation()))

    assert report.state is ProtectionState.DEGRADED
    assert report.reason in {"stop-loss-trigger-mismatch", "take-profit-execute-mode-mismatch"}


def test_exact_native_confirmation_rejects_wrong_symbol_or_side() -> None:
    async def position() -> list[dict[str, str]]:
        rows = _exact_position()
        rows[0]["symbol"] = "BTCUSDT"
        return rows

    async def plans() -> list[dict[str, str]]:
        return _exact_plans()

    report = asyncio.run(confirm_native_protection(position, plans, expectation=_expectation()))

    assert report.state is ProtectionState.DEGRADED
    assert report.reason == "position-symbol-mismatch"


def test_exact_native_confirmation_does_not_accept_unavailable_plan_read() -> None:
    async def position() -> list[dict[str, str]]:
        return _exact_position()

    async def plans() -> list[dict[str, str]]:
        raise Exception("400172 Parameter verification failed")

    report = asyncio.run(confirm_native_protection(position, plans, expectation=_expectation()))

    assert report.state is ProtectionState.DEGRADED
    assert report.reason == "provider-plans-unavailable"
