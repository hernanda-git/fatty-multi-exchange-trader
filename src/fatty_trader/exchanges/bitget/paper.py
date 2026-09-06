"""Credential-free Bitget DEMO execution adapter."""

from __future__ import annotations

from fatty_trader.config.bitget import BitgetVenueConfig, BitgetVenueState
from fatty_trader.domain.enums import Exchange
from fatty_trader.execution.protection import ProtectionPlan, ProtectionReport, ProtectionState
from fatty_trader.execution.service import PaperOrderRequest, PaperOrderResult


class BitgetPaperAdapter:
    """Simulate fills locally and expose explicit protection/reconciliation contracts."""

    def __init__(self, config: BitgetVenueConfig) -> None:
        self._config = config
        self._orders: dict[str, PaperOrderResult] = {}

    @property
    def state(self) -> BitgetVenueState:
        return self._config.state

    @property
    def is_enabled(self) -> bool:
        return self.state is BitgetVenueState.DEMO_READY

    def __call__(self, request: PaperOrderRequest) -> PaperOrderResult:
        if request.exchange is not Exchange.BITGET or not self.is_enabled:
            raise ValueError("Bitget DEMO adapter is disabled")
        result = PaperOrderResult(
            request.client_order_id,
            f"paper-bitget-{request.client_order_id[-8:]}",
            request.sizing.quantity,
            request.signal.entry_price,
        )
        self._orders[result.client_order_id] = result
        return result

    def ensure_protection(self, plan: ProtectionPlan) -> ProtectionReport:
        return ProtectionReport(ProtectionState.VENUE_PROTECTED, plan.quantity)

    def reconcile_protection(self, plan: ProtectionPlan) -> ProtectionReport:
        return ProtectionReport(ProtectionState.VENUE_PROTECTED, plan.quantity)
