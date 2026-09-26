from decimal import Decimal
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fatty_trader.domain.enums import Direction, Exchange, MarginMode

# Highest ``maxLever`` any venue may advertise on these models. This is venue
# metadata, NOT trading leverage: the executable leverage policy for Bitget LIVE is
# pinned separately (``risk.live_policy._MAX_LIVE_LEVERAGE``). Bitget currently
# reports 150 on BTC/ETH, so a lower ceiling here would reject the instrument.
MAX_VENUE_LEVERAGE_CEILING: Final = 150


class CanonicalSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_message_id: int = Field(gt=0)
    source_revision: str = Field(min_length=64, max_length=64)
    pair_token: str = Field(pattern=r"^[A-Z0-9]{2,20}$")
    direction: Direction
    entry_price: Decimal = Field(gt=0)
    stop_loss: Decimal = Field(gt=0)
    take_profits: tuple[Decimal, ...] = Field(default=(), max_length=5)

    @model_validator(mode="after")
    def validate_geometry(self) -> "CanonicalSignal":
        if self.direction is Direction.LONG and self.stop_loss >= self.entry_price:
            raise ValueError("long stop loss must be below entry")
        if self.direction is Direction.SHORT and self.stop_loss <= self.entry_price:
            raise ValueError("short stop loss must be above entry")
        if any(target <= 0 for target in self.take_profits):
            raise ValueError("take profits must be positive")
        if self.direction is Direction.LONG and any(
            target <= self.entry_price for target in self.take_profits
        ):
            raise ValueError("long take profit must be above entry")
        if self.direction is Direction.SHORT and any(
            target >= self.entry_price for target in self.take_profits
        ):
            raise ValueError("short take profit must be below entry")
        return self


class InstrumentSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    exchange: Exchange
    symbol: str = Field(min_length=3, max_length=32)
    qty_step: Decimal = Field(gt=0)
    min_qty: Decimal = Field(gt=0)
    min_notional: Decimal = Field(ge=0)
    max_leverage: int = Field(ge=1, le=MAX_VENUE_LEVERAGE_CEILING)
    contract_multiplier: Decimal = Field(default=Decimal("1"), gt=0)


class VenueRiskConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    exchange: Exchange
    base_margin_usdt: Decimal = Field(gt=0)
    default_leverage: int = Field(ge=1, le=MAX_VENUE_LEVERAGE_CEILING)
    max_leverage: int = Field(ge=1, le=MAX_VENUE_LEVERAGE_CEILING)
    max_auto_margin_usdt: Decimal = Field(gt=0)
    free_margin_usdt: Decimal = Field(ge=0)
    free_margin_headroom_pct: Decimal = Field(gt=0, le=1)
    max_position_notional_usdt: Decimal = Field(gt=0)
    margin_mode: MarginMode


class SizingPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    effective_leverage: int
    effective_margin_usdt: Decimal
    notional_usdt: Decimal
    quantity: Decimal
    required_min_notional_usdt: Decimal


class BitgetLiveRiskConfig(BaseModel):
    """Frozen domain mirror of the Bitget LIVE venue contract (no secrets)."""

    model_config = ConfigDict(frozen=True)

    product_type: Literal["USDT-FUTURES"] = "USDT-FUTURES"
    margin_coin: Literal["USDT"] = "USDT"
    margin_mode: Literal["isolated"] = "isolated"
    # LIVE is pinned to exactly 20x. Defaults of 20/50 would silently allow 50x
    # when a caller omits these fields, so the ceiling is 20, not 50.
    min_leverage: int = Field(default=20, ge=20, le=20)
    max_leverage: int = Field(default=20, ge=20, le=20)
    allocation_pct: Decimal = Field(default=Decimal("0.20"), gt=0, le=1)
    max_normal_positions: int = Field(default=5, gt=0)
    liquidation_buffer: Decimal = Field(default=Decimal("0.10"), gt=0, le=1)
    minimum_liquidation_gap_pct: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    minimum_liquidation_ticks: int = Field(default=0, ge=0)
    latency_slippage_allowance: Decimal = Field(default=Decimal("0"), ge=0)
    # Hard per-trade cap on isolated margin. Required for LIVE: omitting it would
    # size trades from the allocation fraction alone. When set, no trade may use
    # more margin than this even when the allocation fraction would allow it.
    # Allocation becomes a ceiling too: effective margin is the smaller of the
    # two.
    max_margin_per_trade_usdt: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_leverage_range(self) -> "BitgetLiveRiskConfig":
        if self.min_leverage > self.max_leverage:
            raise ValueError("min_leverage must not exceed max_leverage")
        if self.min_leverage != 20 or self.max_leverage != 20:
            raise ValueError(
                "Bitget LIVE leverage is fixed at 20x "
                f"(got {self.min_leverage}/{self.max_leverage})"
            )
        return self
