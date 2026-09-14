"""REST watchdog for the Bitget protection stream."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from fatty_trader.exchanges.bitget.protection_capability import (
    BitgetProtectionCapability,
    NativeProtectionState,
    StreamState,
)
from fatty_trader.storage.protection_capabilities import ProtectionCapabilityRepository


class WatchdogStatus(StrEnum):
    HEALTHY = "HEALTHY"
    STALE = "STALE"
    FAILED = "FAILED"


@dataclass(frozen=True)
class WatchdogReport:
    status: WatchdogStatus
    allow_new_entries: dict[str, bool]
    reasons: tuple[str, ...] = ()


class BitgetProtectionWatchdog:
    """Check stream freshness and provider position truth without provider mutation."""

    def __init__(
        self,
        socket: Any,
        repository: ProtectionCapabilityRepository,
        *,
        environment: str,
        symbols: list[str] | tuple[str, ...],
        read_position: Callable[[str], Awaitable[Any]],
        now: Callable[[], Any],
    ) -> None:
        self._socket = socket
        self._repository = repository
        self._environment = environment.strip().upper()
        if self._environment not in {"DEMO", "LIVE"}:
            raise ValueError("Bitget watchdog environment must be DEMO or LIVE")
        self._symbols = tuple(
            dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip())
        )
        self._read_position = read_position
        self._now = now

    @property
    def stream_symbols(self) -> tuple[str, ...]:
        """Return the current dynamic ticker subscription symbols."""
        return tuple(getattr(self._socket, "symbols", self._symbols))

    def refresh_symbols(self, symbols: list[str] | tuple[str, ...]) -> None:
        """Refresh the symbol set from active fallback positions."""
        self._symbols = tuple(
            dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip())
        )

    async def run_once(self) -> WatchdogReport:
        if not self._symbols:
            return WatchdogReport(WatchdogStatus.HEALTHY, {})
        stream_fresh_by_symbol: dict[str, bool] = {}
        reasons: list[str] = []
        for symbol in self._symbols:
            stream_fresh = bool(self._socket.check_freshness(symbol))
            stream_fresh_by_symbol[symbol] = stream_fresh
            if not stream_fresh and "protection-stream-stale" not in reasons:
                reasons.append("protection-stream-stale")

        allow_new_entries: dict[str, bool] = {}
        provider_failure = False
        for symbol in self._symbols:
            stream_fresh = stream_fresh_by_symbol[symbol]
            read_ok = True
            try:
                positions = await self._read_position(symbol)
            except Exception:
                read_ok = False
                provider_failure = True
                if "provider-position-read-failed" not in reasons:
                    reasons.append("provider-position-read-failed")
            else:
                if not isinstance(positions, list) or not all(
                    isinstance(position, dict) for position in positions
                ):
                    read_ok = False
                    provider_failure = True
                    if "provider-position-invalid" not in reasons:
                        reasons.append("provider-position-invalid")

            capability = self._ensure_capability(symbol)
            native_verified = capability.native_state is NativeProtectionState.VERIFIED
            allow_new_entries[symbol] = read_ok and (native_verified or stream_fresh)
            if native_verified and read_ok:
                continue
            self._repository.update_stream(
                "bitget",
                self._environment,
                symbol,
                state=(
                    StreamState.HEALTHY
                    if stream_fresh and read_ok
                    else StreamState.STALE
                    if not stream_fresh
                    else StreamState.FAILED
                ),
                # REST reconciliation must not make an old WS event look fresh.
                last_stream_at=capability.last_stream_at,
                last_error=(
                    None
                    if stream_fresh and read_ok
                    else "protection-stream-stale"
                    if not stream_fresh
                    else "provider-position-read-failed"
                ),
            )

        unique_reasons = tuple(dict.fromkeys(reasons))
        if provider_failure:
            status = WatchdogStatus.FAILED
        elif "protection-stream-stale" in unique_reasons:
            status = WatchdogStatus.STALE
        else:
            status = WatchdogStatus.HEALTHY
        return WatchdogReport(status, allow_new_entries, unique_reasons)

    def _ensure_capability(self, symbol: str) -> BitgetProtectionCapability:
        capability = self._repository.get("bitget", self._environment, symbol)
        if capability is not None:
            return capability
        capability = BitgetProtectionCapability(
            exchange="bitget",
            environment=self._environment,
            symbol=symbol,
        )
        self._repository.upsert(capability)
        return capability
