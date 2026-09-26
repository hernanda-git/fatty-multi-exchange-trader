from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from fatty_trader.risk.liquidation import MMTier
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
    price_tick = Decimal(1).scaleb(-price_precision)
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


def mm_tiers_from_position_lever(rows: list[dict[str, Any]], symbol: str) -> tuple[MMTier, ...]:
    """Build MMR tiers from ``/api/v2/mix/market/query-position-lever`` rows.

    Bitget does not publish maintenance-margin rates in ``/market/contracts``;
    they live in the position-lever table, a flat list of
    ``{symbol, level, startUnit, endUnit, keepMarginRate}`` rows ordered by size.
    ``keepMarginRate`` IS the maintenance-margin rate (it is not a
    complement), and the unbounded final tier is marked with ``endUnit == 0``.

    Tiers are sorted by ``endUnit`` ascending and the widest becomes the
    catch-all (``None`` bound) so ``select_mmr`` can never fall through a gap.
    Fails closed on a bad payload rather than returning an empty tuple, which
    would abort live sizing.
    """
    requested = symbol.upper()
    matching = [row for row in rows if str(row.get("symbol", "")).upper() == requested]
    if not matching:
        raise ValueError(f"unknown Bitget position-lever symbol: {requested}")

    tiers: list[MMTier] = []
    for row in matching:
        raw_bound = row.get("endUnit")
        # Bitget marks the unbounded final tier with endUnit == 0.
        bound = (
            None
            if raw_bound in (None, "", 0, "0")
            else _decimal(raw_bound, "position-lever endUnit", positive=False)
        )
        mmr = _decimal(row.get("keepMarginRate"), "keepMarginRate", positive=False)
        if mmr <= 0 or mmr > 1:
            raise ValueError("keepMarginRate must be in (0, 1]")
        tiers.append(MMTier(upper_bound_notional=bound, mmr=mmr))

    ordered = sorted(
        tiers,
        key=lambda tier: (
            tier.upper_bound_notional is None,
            tier.upper_bound_notional or 0,
        ),
    )
    # The widest tier becomes the catch-all so any notional resolves an MMR.
    ordered[-1] = MMTier(upper_bound_notional=None, mmr=ordered[-1].mmr)
    return tuple(ordered)


def find_contract(contracts: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
    """Find exactly the requested contract or fail closed."""
    requested = symbol.upper()
    for contract in contracts:
        if str(contract.get("symbol", "")).upper() == requested:
            return contract
    raise KeyError(f"unknown Bitget contract: {requested}")
