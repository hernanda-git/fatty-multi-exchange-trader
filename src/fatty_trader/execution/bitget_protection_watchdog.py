"""REST watchdog for the Bitget protection stream.

The watchdog's job is to make a broken protection stream *loud and
enforced* instead of silent. Two failure shapes are distinguished on purpose,
because they warrant very different responses:

* **Socket death** (`socket-not-connected`): the authenticated WebSocket
  session does not exist at all. Nothing can be protected by a stream that
  never opened, so this latches an entry block.
* **Per-symbol quiet** (`protection-stream-stale`): the socket is connected
  but one symbol has not ticked recently. This is normal market behaviour and
  must never halt a venue.

The latch uses a *dedicated* scope, not the venue-wide ``bitget`` scope. The
venue scope is read by ``bitget_monitor._run_fallback_monitor``, which is the
bot-managed stop-loss path for symbols that reject native SL/TP. Tripping the
venue scope on a dead WebSocket would switch off protection for exactly the
positions that need it. See ``PROTECTION_STREAM_SCOPE``.
"""

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

#: Dedicated kill-switch scope for protection-stream failure.
#:
#: Only the dispatcher entry gate reads this. It is deliberately NOT the venue
#: scope, because the venue scope also gates bot-managed fallback TP/SL.
PROTECTION_STREAM_SCOPE = "bitget-protection-stream"

#: Socket states that mean there is no usable authenticated session. RECONNECTING
#: and STALE are excluded: they can describe a live connection in a bad moment.
_DEAD_SOCKET_STATES = frozenset({"FAILED", "DISCONNECTED"})


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
        kill_switch: Any | None = None,
        stale_cycles_before_latch: int = 3,
    ) -> None:
        self._socket = socket
        self._repository = repository
        self._environment = environment.strip().upper()
        if self._environment not in {"DEMO", "LIVE"}:
            raise ValueError("Bitget watchdog environment must be DEMO or LIVE")
        if stale_cycles_before_latch < 1:
            raise ValueError("stale_cycles_before_latch must be at least 1")
        self._kill_switch = kill_switch
        self._stale_cycles_before_latch = stale_cycles_before_latch
        self._consecutive_bad_cycles = 0
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

    def _socket_is_dead(self) -> bool:
        """Return True when there is no usable authenticated session.

        Checked independently of the symbol set on purpose. The symbol set is
        derived from active fallback rows, so it is empty in the normal steady
        state and immediately after a crash or liquidation - which is exactly
        when a dead socket must still be caught.
        """
        state = getattr(self._socket, "state", None)
        if state is None:
            # Fail CLOSED: a socket that cannot report its state is not
            # evidence of a working session. Treating absence as healthy is
            # exactly the silent-pass failure this watchdog exists to catch.
            return True
        return str(getattr(state, "value", state)).upper() in _DEAD_SOCKET_STATES

    async def run_once(self) -> WatchdogReport:
        socket_dead = self._socket_is_dead()
        reasons: list[str] = []
        if socket_dead:
            reasons.append("socket-not-connected")

        stream_fresh_by_symbol: dict[str, bool] = {}
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
            # A dead socket denies entries even if REST says the capability is
            # verified: without a live stream, protection is not being enforced.
            allow_new_entries[symbol] = (
                read_ok and not socket_dead and (native_verified or stream_fresh)
            )
            if native_verified and read_ok:
                continue
            self._repository.update_stream(
                "bitget",
                self._environment,
                symbol,
                state=(
                    StreamState.FAILED
                    if socket_dead
                    else StreamState.HEALTHY
                    if stream_fresh and read_ok
                    else StreamState.STALE
                    if not stream_fresh
                    else StreamState.FAILED
                ),
                # REST reconciliation must not make an old WS event look fresh.
                last_stream_at=capability.last_stream_at,
                last_error=(
                    "socket-not-connected"
                    if socket_dead
                    else None
                    if stream_fresh and read_ok
                    else "protection-stream-stale"
                    if not stream_fresh
                    else "provider-position-read-failed"
                ),
            )

        unique_reasons = tuple(dict.fromkeys(reasons))
        if socket_dead or provider_failure:
            status = WatchdogStatus.FAILED
        elif "protection-stream-stale" in unique_reasons:
            status = WatchdogStatus.STALE
        else:
            status = WatchdogStatus.HEALTHY
        # Only socket death and provider read failure are severe enough to
        # latch. Per-symbol quiet is reported, not enforced.
        self._track_consecutive_failures(
            status, unique_reasons, latchable=socket_dead or provider_failure
        )
        return WatchdogReport(status, allow_new_entries, unique_reasons)

    def _track_consecutive_failures(
        self, status: WatchdogStatus, reasons: tuple[str, ...], *, latchable: bool
    ) -> None:
        """Latch the protection-stream kill switch once failures persist.

        A dead protection stream must not sit silently: a single bad cycle is
        tolerated for transient blips, but a persistent one blocks new entries
        for the venue. Existing positions and their protective orders are
        untouched - the venue kill switch is not touched either, so bot-managed
        fallback TP/SL keeps running.
        """
        if status is WatchdogStatus.HEALTHY:
            self._consecutive_bad_cycles = 0
            return
        if not latchable:
            # Connected but quiet: report it, never halt on it.
            return
        self._consecutive_bad_cycles += 1
        if self._kill_switch is None:
            return
        if self._consecutive_bad_cycles < self._stale_cycles_before_latch:
            return
        if self._is_latched():
            return

        reason = "protection-watchdog-latched"
        # A provider read failure is the more severe signal: we cannot even
        # confirm what is open, so report that rather than the stream symptom.
        for candidate in (
            "provider-position-read-failed",
            "provider-position-invalid",
            "socket-not-connected",
        ):
            if candidate in reasons:
                reason = candidate
                break
        self._latch(reason)

    def _is_latched(self) -> bool:
        is_active = getattr(self._kill_switch, "is_active", None)
        if not callable(is_active):
            return False
        try:
            return bool(is_active(PROTECTION_STREAM_SCOPE))
        except Exception:
            return False

    def _latch(self, reason: str) -> None:
        """Record the latch without ever propagating a failure.

        The consecutive-failure counter is in-process. If a database error here
        escaped, the watchdog task would die, the process would restart, and
        the counter would reset to zero - so a monitor restarting faster than
        the threshold could never latch at all. A latch we could not record is
        therefore reported loudly and retried on the next cycle, never raised.
        """
        latch = getattr(self._kill_switch, "latch_kill_switch", None)
        if not callable(latch):
            # Require the explicit API. Duck-typing a bare `latch` risks calling
            # an unrelated method (a margin latch, a different venue).
            print(
                f"component=bitget-protection-watchdog state=latch_unavailable "
                f"consecutive_bad_cycles={self._consecutive_bad_cycles} "
                f"kill_switch_type={type(self._kill_switch).__name__}",
                flush=True,
            )
            return
        try:
            latch(PROTECTION_STREAM_SCOPE, reason)
        except Exception as exc:
            print(
                f"component=bitget-protection-watchdog state=latch-failed "
                f"scope={PROTECTION_STREAM_SCOPE} reason={reason} "
                f"consecutive_bad_cycles={self._consecutive_bad_cycles} "
                f"error={type(exc).__name__}",
                flush=True,
            )

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
