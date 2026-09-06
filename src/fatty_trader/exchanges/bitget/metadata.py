from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from fatty_trader.risk.sizing import SymbolMetadata


def _decimal(value: Any, field: str, *, positive: bool = True) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}") from exc
    if positive and result <= 0:
        raise ValueError(f"{field} must be positive")
    return result


def _first_present(contract: dict[str, Any], *fields: str) -> Any:
    for field in fields:
        value = contract.get(field)
        if value not in (None, ""):
            return value
    return None


def metadata_from_contract(contract: dict[str, Any]) -> SymbolMetadata:
    """Normalize one Bitget V2 contract record; never selects another symbol."""
    symbol = str(contract.get("symbol", "")).upper()
    if not symbol:
        raise ValueError("contract symbol is required")
    price_precision = int(contract.get("pricePlace", 0))
    price_tick = _decimal(
        contract.get("priceEndStep") or Decimal(1).scaleb(-price_precision), "price tick"
    )
    size_step = _decimal(contract.get("sizeMultiplier"), "size multiplier")
    min_qty = _decimal(contract.get("minTradeNum"), "minimum order quantity")
    max_qty = _decimal(
        _first_present(
            contract,
            "maxTradeNum",
            "maxOrderQty",
            "maxMarketOrderQty",
            "maxPositionNum",
        ),
        "maximum order quantity",
    )
    min_notional = _decimal(contract.get("minTradeUSDT", "0"), "minimum notional", positive=False)
    max_leverage = int(_decimal(contract.get("maxLever"), "maximum leverage"))
    contract_value = _decimal(
        contract.get("contractValue", contract.get("contractSize", "1")), "contract value"
    )
    return SymbolMetadata(
        symbol=symbol,
        price_precision=price_precision,
        price_tick=price_tick,
        size_step=size_step,
        min_order_qty=min_qty,
        max_order_qty=max_qty,
        contract_value=contract_value,
        max_leverage=max_leverage,
        min_notional=min_notional,
    )


def find_contract(contracts: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
    """Find exactly the requested contract or fail closed."""
    requested = symbol.upper()
    for contract in contracts:
        if str(contract.get("symbol", "")).upper() == requested:
            return contract
    raise KeyError(f"unknown Bitget contract: {requested}")
