from __future__ import annotations

from decimal import Decimal

from fatty_trader.risk.sizing import SymbolMetadata


class OrderValidationError(ValueError):
    """Raised when an order cannot pass local Bitget contract validation."""


def _is_multiple(value: Decimal, step: Decimal) -> bool:
    quotient = value / step
    return quotient == quotient.to_integral_value()


def validate_order(
    symbol: str,
    side: str,
    price: Decimal,
    quantity: Decimal,
    metadata: SymbolMetadata,
    *,
    reduce_only: bool = False,
    exit_order: bool = False,
) -> None:
    """Validate one order before any provider mutation is attempted."""
    if symbol.upper() != metadata.symbol.upper():
        raise OrderValidationError("symbol does not match contract metadata")
    if side not in {"BUY", "SELL"}:
        raise OrderValidationError("side must be BUY or SELL")
    if price <= 0 or not _is_multiple(price, metadata.price_tick):
        raise OrderValidationError("price tick validation failed")
    if quantity <= 0 or not _is_multiple(quantity, metadata.size_step):
        raise OrderValidationError("quantity step validation failed")
    if quantity < metadata.min_order_qty:
        raise OrderValidationError("quantity is below minimum order quantity")
    if metadata.max_order_qty is not None and quantity > metadata.max_order_qty:
        raise OrderValidationError("quantity exceeds maximum order quantity")
    if quantity * price * metadata.contract_value < metadata.min_notional:
        raise OrderValidationError("quantity is below minimum notional")
    if exit_order and not reduce_only:
        raise OrderValidationError("exit orders must be reduce-only")
