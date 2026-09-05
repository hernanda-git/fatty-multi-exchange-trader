from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol


class BitgetReadModelError(ValueError):
    """A provider read lacked a required, documented field."""


class BitgetReadClient(Protocol):
    async def get_account(self, symbol: str) -> Any: ...
    async def get_single_position(self, symbol: str) -> Any: ...


@dataclass(frozen=True)
class BitgetAccountState:
    available: Decimal
    margin_mode: str
    position_mode: str
    long_leverage: Decimal
    short_leverage: Decimal


@dataclass(frozen=True)
class BitgetPositionState:
    symbol: str
    hold_side: str
    quantity: Decimal
    entry_price: Decimal
    margin_mode: str
    leverage: Decimal
    stop_loss_id: str | None
    take_profit_id: str | None


def _required_decimal(payload: dict[str, Any], field: str) -> Decimal:
    value = payload.get(field)
    if value is None or str(value).strip() == "":
        raise BitgetReadModelError(f"Bitget response missing required field: {field}")
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BitgetReadModelError(f"Bitget response has invalid decimal field: {field}") from exc


def _required_text(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if value is None or not str(value).strip():
        raise BitgetReadModelError(f"Bitget response missing required field: {field}")
    return str(value)


def _is_open_position(row: dict[str, Any]) -> bool:
    try:
        return Decimal(str(row.get("total", "0"))) > 0
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BitgetReadModelError(
            "Bitget position response has invalid decimal field: total"
        ) from exc


async def read_account_state(client: BitgetReadClient, symbol: str) -> BitgetAccountState:
    payload = await client.get_account(symbol)
    if not isinstance(payload, dict):
        raise BitgetReadModelError("Bitget account response must be an object")
    return BitgetAccountState(
        available=_required_decimal(payload, "available"),
        margin_mode=_required_text(payload, "marginMode").lower(),
        position_mode=_required_text(payload, "posMode"),
        long_leverage=_required_decimal(payload, "isolatedLongLever"),
        short_leverage=_required_decimal(payload, "isolatedShortLever"),
    )


async def read_position_state(client: BitgetReadClient, symbol: str) -> BitgetPositionState | None:
    payload = await client.get_single_position(symbol)
    if not isinstance(payload, list):
        raise BitgetReadModelError("Bitget position response must be a list")
    nonzero = [row for row in payload if isinstance(row, dict) and _is_open_position(row)]
    if not nonzero:
        return None
    if len(nonzero) != 1:
        raise BitgetReadModelError("Bitget position response has multiple active sides")
    row = nonzero[0]
    return BitgetPositionState(
        symbol=_required_text(row, "symbol"),
        hold_side=_required_text(row, "holdSide").lower(),
        quantity=_required_decimal(row, "total"),
        entry_price=_required_decimal(row, "openPriceAvg"),
        margin_mode=_required_text(row, "marginMode").lower(),
        leverage=_required_decimal(row, "leverage"),
        stop_loss_id=str(row["stopLossId"]) if row.get("stopLossId") else None,
        take_profit_id=str(row["takeProfitId"]) if row.get("takeProfitId") else None,
    )
