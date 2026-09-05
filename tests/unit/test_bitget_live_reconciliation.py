import asyncio
from decimal import Decimal

from fatty_trader.exchanges.bitget.read_model import BitgetPositionState
from fatty_trader.exchanges.bitget.reconciliation_live import (
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
