from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field

from fatty_trader.domain.enums import Direction
from fatty_trader.domain.models import InstrumentSpec, SizingPlan, VenueRiskConfig
from fatty_trader.risk.liquidation import MMTier


class SizingError(ValueError):
    """Raised when a venue minimum cannot be met inside immutable risk rails."""


def _ceil_to_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_CEILING) * step


def minimum_safe_plan(
    *, spec: InstrumentSpec, config: VenueRiskConfig, reference_price: Decimal
) -> SizingPlan:
    if reference_price <= 0:
        raise SizingError("reference price must be positive")

    min_qty_notional = spec.min_qty * reference_price * spec.contract_multiplier
    required_min = max(spec.min_notional, min_qty_notional) * Decimal("1.02")
    leverage_cap = min(spec.max_leverage, config.max_leverage)
    leverage = min(max(config.default_leverage, 1), leverage_cap)
    required_leverage = (required_min / config.base_margin_usdt).to_integral_value(
        rounding=ROUND_CEILING
    )
    leverage = min(max(leverage, int(required_leverage)), leverage_cap)
    margin = config.base_margin_usdt
    if margin * leverage < required_min:
        margin = required_min / Decimal(leverage)

    if margin > config.max_auto_margin_usdt:
        raise SizingError("required margin exceeds auto margin cap")
    if margin > config.free_margin_usdt * config.free_margin_headroom_pct:
        raise SizingError("required margin exceeds safe free-margin headroom")

    notional = margin * leverage
    if notional > config.max_position_notional_usdt:
        raise SizingError("required notional exceeds position cap")

    quantity = _ceil_to_step(
        required_min / (reference_price * spec.contract_multiplier), spec.qty_step
    )
    final_notional = quantity * reference_price * spec.contract_multiplier
    if final_notional > config.max_position_notional_usdt:
        raise SizingError("rounded quantity exceeds position cap")
    return SizingPlan(
        effective_leverage=leverage,
        effective_margin_usdt=margin,
        notional_usdt=final_notional,
        quantity=quantity,
        required_min_notional_usdt=required_min,
    )


class SymbolMetadata(BaseModel):
    """Pure venue metadata for one USDT-M futures symbol (no network).

    Cached from exchange info at startup/refresh; all sizing math reads from
    this snapshot so live planning never blocks on REST.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=3, max_length=32)
    price_precision: int = Field(ge=0, le=12)
    price_tick: Decimal = Field(gt=0)
    size_step: Decimal = Field(gt=0)
    min_order_qty: Decimal = Field(gt=0)
    max_order_qty: Decimal | None = Field(default=None, gt=0)
    contract_value: Decimal = Field(default=Decimal("1"), gt=0)
    max_leverage: int = Field(ge=1, le=125)
    min_notional: Decimal = Field(default=Decimal("5"), ge=0)
    mm_tiers: tuple[MMTier, ...] = Field(default=())


class SymbolMetadataCache:
    """In-memory symbol metadata store (dict-backed, no I/O)."""

    def __init__(self) -> None:
        self._entries: dict[str, SymbolMetadata] = {}

    def register(self, meta: SymbolMetadata) -> None:
        """Insert or replace the metadata snapshot for ``meta.symbol``."""
        self._entries[meta.symbol] = meta

    def get(self, symbol: str) -> SymbolMetadata:
        """Return cached metadata; raise KeyError when unknown."""
        try:
            return self._entries[symbol]
        except KeyError as exc:
            raise KeyError(f"unknown symbol metadata: {symbol}") from exc

    def __len__(self) -> int:
        return len(self._entries)


def round_price_to_tick(price: Decimal, tick: Decimal) -> Decimal:
    """Round ``price`` to the nearest multiple of ``tick`` (half up)."""
    if price <= 0 or tick <= 0:
        raise SizingError("price and tick must be positive")
    steps = (price / tick).to_integral_value(rounding=ROUND_HALF_UP)
    rounded = steps * tick
    if rounded <= 0:
        raise SizingError("tick-rounded price is non-positive")
    return rounded


def round_qty_to_step(quantity: Decimal, step: Decimal) -> Decimal:
    """Floor ``quantity`` down to a multiple of ``step`` (never over-size)."""
    if quantity <= 0 or step <= 0:
        raise SizingError("quantity and step must be positive")
    return (quantity / step).to_integral_value(rounding=ROUND_DOWN) * step


def derive_sl_tp(entry: Decimal, direction: Direction, atr: Decimal) -> tuple[Decimal, Decimal]:
    """Deterministic SL/TP fallback when a signal lacks SL/TP.

    LONG:  SL = entry - 1.5*ATR, TP = entry + 2.0*ATR.
    SHORT: SL = entry + 1.5*ATR, TP = entry - 2.0*ATR.
    Pure function of (entry, direction, atr); same inputs always agree.
    """
    if entry <= 0:
        raise SizingError("entry must be positive")
    if atr <= 0:
        raise SizingError("atr must be positive")
    if direction is Direction.LONG:
        return entry - Decimal("1.5") * atr, entry + Decimal("2") * atr
    return entry + Decimal("1.5") * atr, entry - Decimal("2") * atr
