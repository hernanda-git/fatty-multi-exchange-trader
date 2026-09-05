import asyncio

from fatty_trader.exchanges.bitget.read_model import BitgetPositionState
from fatty_trader.exchanges.bitget.reconciliation_live import (
    ProtectionReadiness,
    evaluate_position_protection,
)


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
