from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fatty_trader.domain.enums import Exchange
from fatty_trader.execution.protection import ProtectionPlan, ProtectionReport, ProtectionState
from fatty_trader.execution.service import PaperOrderRequest, PaperOrderResult


@dataclass(frozen=True)
class BinancePaperConfig:
    mode: Literal["PAPER"] = "PAPER"
    enabled: bool = True


class BinancePaperAdapter:
    """Deterministic in-memory Binance executor; it has no network or credentials."""

    def __init__(self, config: BinancePaperConfig | None = None) -> None:
        self._config = config or BinancePaperConfig()
        self._orders: dict[str, PaperOrderResult] = {}

    @property
    def is_enabled(self) -> bool:
        return self._config.mode == "PAPER" and self._config.enabled

    def __call__(self, request: PaperOrderRequest) -> PaperOrderResult:
        if request.exchange is not Exchange.BINANCE or not self.is_enabled:
            raise ValueError("Binance PAPER adapter is disabled")
        result = PaperOrderResult(
            request.client_order_id,
            f"paper-binance-{request.client_order_id[-8:]}",
            request.sizing.quantity,
            request.signal.entry_price,
        )
        self._orders[result.client_order_id] = result
        return result

    def ensure_protection(self, plan: ProtectionPlan) -> ProtectionReport:
        return ProtectionReport(ProtectionState.VENUE_PROTECTED, plan.quantity)

    def reconcile_protection(self, plan: ProtectionPlan) -> ProtectionReport:
        return ProtectionReport(ProtectionState.VENUE_PROTECTED, plan.quantity)
