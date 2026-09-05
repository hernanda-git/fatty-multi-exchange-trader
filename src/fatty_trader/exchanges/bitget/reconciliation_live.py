from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import StrEnum

from fatty_trader.exchanges.bitget.read_model import BitgetPositionState


class ProtectionReadiness(StrEnum):
    FLAT = "flat"
    PROTECTED = "protected"
    MISSING_STOP_LOSS = "missing_stop_loss"
    MISSING_TAKE_PROFIT = "missing_take_profit"


async def evaluate_position_protection(
    read_position: Callable[[], Awaitable[BitgetPositionState | None]],
) -> ProtectionReadiness:
    """Classify live protection from a fresh provider read without mutating the venue."""
    position = await read_position()
    if position is None:
        return ProtectionReadiness.FLAT
    if position.stop_loss_id is None:
        return ProtectionReadiness.MISSING_STOP_LOSS
    if position.take_profit_id is None:
        return ProtectionReadiness.MISSING_TAKE_PROFIT
    return ProtectionReadiness.PROTECTED
