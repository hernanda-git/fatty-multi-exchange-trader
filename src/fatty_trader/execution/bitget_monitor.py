"""Read-only Bitget monitor that latches a durable fail-closed kill switch."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from fatty_trader.exchanges.bitget.reconciliation import reconcile_unknown_intent
from fatty_trader.exchanges.bitget.reconciliation_live import confirm_native_protection
from fatty_trader.execution.protection import ProtectionState
from fatty_trader.storage.reconciliation import ReconciliationRepository


class BitgetMonitorClient(Protocol):
    async def get_all_positions(self) -> Any: ...
    async def get_pending_orders(self) -> Any: ...
    async def get_pending_plan_orders(self, symbol: str) -> Any: ...
    async def get_single_position(self, symbol: str) -> Any: ...
    async def get_order_detail(self, symbol: str, *, client_oid: str) -> Any: ...
    async def get_fills(self, symbol: str) -> Any: ...
    async def get_clock_skew_ms(self) -> int: ...


@dataclass(frozen=True)
class MonitorReport:
    status: str
    reasons: tuple[str, ...] = ()


class BitgetMonitor:
    """Observe and reconcile only; it contains no provider mutation methods."""

    def __init__(
        self,
        client: BitgetMonitorClient,
        repository: ReconciliationRepository,
        *,
        scope: str = "bitget",
        max_clock_skew_ms: int = 10_000,
    ) -> None:
        if max_clock_skew_ms < 0:
            raise ValueError("max_clock_skew_ms must be non-negative")
        self._client = client
        self._repository = repository
        self._scope = scope
        self._max_clock_skew_ms = max_clock_skew_ms

    async def run_once(self) -> MonitorReport:
        reasons: list[str] = []
        await self._reconcile_unknown_intents(reasons)
        positions = await self._read_rows(
            self._client.get_all_positions, "provider-positions-invalid", reasons
        )
        orders = await self._read_rows(
            self._client.get_pending_orders, "provider-orders-invalid", reasons
        )
        await self._check_positions(positions, reasons)
        self._check_orders(orders, reasons)
        try:
            skew = await self._client.get_clock_skew_ms()
        except Exception:
            reasons.append("clock-skew-unavailable")
        else:
            if abs(skew) > self._max_clock_skew_ms:
                reasons.append("clock-skew-exceeded")
        unique_reasons = tuple(dict.fromkeys(reasons))
        if unique_reasons:
            for reason in unique_reasons:
                self._repository.latch_kill_switch(self._scope, reason)
            return MonitorReport("kill-switch-latched", unique_reasons)
        if self._repository.kill_switch_active(self._scope):
            return MonitorReport("kill-switch-latched")
        return MonitorReport("ok")

    async def _reconcile_unknown_intents(self, reasons: list[str]) -> None:
        for intent in self._repository.unknown_intents(self._scope):
            try:
                reconciled = await reconcile_unknown_intent(
                    intent,
                    read_order_detail=lambda symbol, oid: self._client.get_order_detail(
                        symbol, client_oid=oid
                    ),
                    read_fills=self._client.get_fills,
                )
                self._repository.update_intent(reconciled)
            except Exception:
                reasons.append(f"unknown-intent-unreconciled:{intent.client_oid}")

    async def _read_rows(
        self,
        reader: Callable[[], Awaitable[Any]],
        invalid_reason: str,
        reasons: list[str],
    ) -> list[dict[str, Any]]:
        try:
            result = await reader()
        except Exception:
            reasons.append(invalid_reason)
            return []
        if isinstance(result, dict) and set(result) <= {"entrustedList", "endId"}:
            result = result.get("entrustedList") or []
        if not isinstance(result, list) or not all(isinstance(row, dict) for row in result):
            reasons.append(invalid_reason)
            return []
        return result

    async def _check_positions(self, positions: list[dict[str, Any]], reasons: list[str]) -> None:
        expected_symbols = self._repository.expected_position_symbols(self._scope)
        for position in positions:
            quantity = _open_quantity(position)
            if quantity is None:
                reasons.append("provider-position-invalid")
                continue
            if quantity == 0:
                continue
            symbol = position.get("symbol")
            if not isinstance(symbol, str) or not symbol:
                reasons.append("provider-position-invalid")
                continue
            if symbol not in expected_symbols:
                reasons.append(f"unexpected-position:{symbol}")
                continue

            async def read_position(target: Any = symbol) -> Any:
                return await self._client.get_single_position(target)

            async def read_pending_plans(target: Any = symbol) -> Any:
                return await self._client.get_pending_plan_orders(target)

            report = await confirm_native_protection(
                read_position,
                read_pending_plans,
                expected_quantity=quantity,
            )
            if report.state is not ProtectionState.VENUE_PROTECTED:
                reasons.append(report.reason or "native-protection-unconfirmed")

    def _check_orders(self, orders: list[dict[str, Any]], reasons: list[str]) -> None:
        known = {intent.client_oid for intent in self._repository.unknown_intents(self._scope)}
        for order in orders:
            oid = order.get("clientOid", order.get("client_oid"))
            if not isinstance(oid, str) or oid not in known:
                reasons.append(f"unexpected-order:{oid if isinstance(oid, str) else 'unknown'}")


def _open_quantity(position: dict[str, Any]) -> Decimal | None:
    try:
        quantity = Decimal(str(position.get("total", position.get("size", "0"))))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return quantity if quantity >= 0 else None
