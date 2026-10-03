"""Provider read failures must name the real cause, not collapse into one string.

Production incident: every ``state=kill-switch-latched reasons=provider-fills-invalid``
line was preceded by ``provider-position-read-failed``, and neither string carried the
underlying exception, so a transport blip and a contract change were indistinguishable.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from fatty_trader.exchanges.bitget.client import BitgetApiError
from fatty_trader.exchanges.bitget.live import InMemoryLiveIntentStore
from fatty_trader.exchanges.bitget.protection_capability import (
    BitgetProtectionCapability,
    NativeProtectionState,
    StreamState,
)
from fatty_trader.exchanges.bitget.websocket import WebSocketConnectionState
from fatty_trader.execution.bitget_monitor import BitgetMonitor, provider_read_failure_reason
from fatty_trader.execution.bitget_protection_watchdog import (
    BitgetProtectionWatchdog,
    WatchdogStatus,
)
from fatty_trader.storage.protection_capabilities import InMemoryProtectionCapabilityRepository
from fatty_trader.storage.reconciliation import InMemoryReconciliationRepository

_NOW_SOCKET = WebSocketConnectionState.CONNECTED


class ExplodingVenue:
    """Venue double whose reads raise a specific provider exception."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def get_all_positions(self) -> Any:
        raise self._exc

    async def get_pending_orders(self) -> Any:
        raise self._exc

    async def get_pending_plan_orders(self, symbol: str) -> Any:
        raise self._exc

    async def get_single_position(self, symbol: str) -> Any:
        raise self._exc

    async def get_order_detail(self, symbol: str, *, client_oid: str) -> Any:
        raise self._exc

    async def get_fills(self, symbol: str | None = None) -> Any:
        raise self._exc

    async def get_clock_skew_ms(self) -> int:
        return 0


async def test_fills_read_exception_names_exception_type_and_provider_code(
    caplog: pytest.LogCaptureFixture,
) -> None:
    exc = BitgetApiError("Bitget GET fills rate limited", code="30006")
    repository = InMemoryReconciliationRepository()

    with caplog.at_level(logging.ERROR, logger="fatty_trader.execution.bitget_monitor"):
        report = await BitgetMonitor(
            ExplodingVenue(exc), repository, live_intent_store=InMemoryLiveIntentStore()
        ).run_once()

    assert report.status == "kill-switch-latched"
    assert "provider-fills-read-failed:BitgetApiError:30006" in report.reasons
    # The collapsed string must be gone: it hid the cause.
    assert "provider-fills-invalid" not in report.reasons


async def test_fills_read_exception_without_code_omits_the_code_segment() -> None:
    repository = InMemoryReconciliationRepository()

    report = await BitgetMonitor(
        ExplodingVenue(TimeoutError("boom")),
        repository,
        live_intent_store=InMemoryLiveIntentStore(),
    ).run_once()

    assert "provider-fills-read-failed:TimeoutError" in report.reasons


async def test_fills_shape_violation_is_reported_as_shape_invalid() -> None:
    class ShapedVenue(ExplodingVenue):
        async def get_all_positions(self) -> Any:
            return []

        async def get_pending_orders(self) -> Any:
            return []

        async def get_fills(self, symbol: str | None = None) -> Any:
            return {"fillList": "not-a-list-of-dicts"}

    repository = InMemoryReconciliationRepository()

    report = await BitgetMonitor(
        ShapedVenue(RuntimeError("unused")),
        repository,
        live_intent_store=InMemoryLiveIntentStore(),
    ).run_once()

    assert "provider-fills-shape-invalid" in report.reasons
    assert not any(reason.startswith("provider-fills-read-failed") for reason in report.reasons)


async def test_fills_read_failure_is_logged_with_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = InMemoryReconciliationRepository()

    with caplog.at_level(logging.ERROR, logger="fatty_trader.execution.bitget_monitor"):
        await BitgetMonitor(
            ExplodingVenue(TimeoutError("provider read blew up")),
            repository,
            live_intent_store=InMemoryLiveIntentStore(),
        ).run_once()

    records = [record for record in caplog.records if "fills" in record.getMessage()]
    assert records, "the provider read failure was not logged at all"
    assert any(
        "provider read blew up" in record.getMessage() or record.exc_info for record in records
    ), "the underlying exception was neither logged nor attached to the record"


async def test_positions_read_failure_names_cause_and_orders_do_not_collapse(
    caplog: pytest.LogCaptureFixture,
) -> None:
    repository = InMemoryReconciliationRepository()
    venue = ExplodingVenue(BitgetApiError("rate limited", code="30006"))

    with caplog.at_level(logging.ERROR, logger="fatty_trader.execution.bitget_monitor"):
        report = await BitgetMonitor(venue, repository).run_once()

    assert "provider-positions-read-failed:BitgetApiError:30006" in report.reasons
    assert "provider-orders-read-failed:BitgetApiError:30006" in report.reasons
    assert "provider-positions-invalid" not in report.reasons
    assert "provider-orders-invalid" not in report.reasons


async def test_positions_shape_violation_is_reported_as_shape_invalid() -> None:
    class ShapedVenue(ExplodingVenue):
        async def get_all_positions(self) -> Any:
            return "not-a-list"

        async def get_pending_orders(self) -> Any:
            return []

    repository = InMemoryReconciliationRepository()

    report = await BitgetMonitor(ShapedVenue(RuntimeError("unused")), repository).run_once()

    assert "provider-positions-shape-invalid" in report.reasons


async def test_monitor_reason_still_latches_fail_closed() -> None:
    """Diagnosability must not weaken the latch: a bad read still blocks entries."""
    repository = InMemoryReconciliationRepository()

    await BitgetMonitor(ExplodingVenue(TimeoutError("boom")), repository).run_once()

    assert repository.kill_switch_active("bitget") is True


class FakeSocket:
    def __init__(self, fresh: bool) -> None:
        self.fresh = fresh
        self.state = _NOW_SOCKET

    def check_freshness(self, symbol: str | None = None) -> bool:
        return self.fresh


@pytest.fixture
def capability_repository() -> InMemoryProtectionCapabilityRepository:
    repository = InMemoryProtectionCapabilityRepository()
    repository.upsert(
        BitgetProtectionCapability(
            exchange="bitget",
            environment="LIVE",
            symbol="BTCUSDT",
            native_state=NativeProtectionState.UNSUPPORTED,
            fallback_allowed=True,
            stream_state=StreamState.HEALTHY,
        )
    )
    return repository


async def test_watchdog_position_read_failure_names_exception_type_and_code(
    capability_repository: InMemoryProtectionCapabilityRepository,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def read_position(_: str) -> list[dict[str, str]]:
        raise BitgetApiError("Bitget GET all-position rate limited", code="30006")

    watchdog = BitgetProtectionWatchdog(
        FakeSocket(True),
        capability_repository,
        environment="LIVE",
        symbols=["BTCUSDT"],
        read_position=read_position,
        now=lambda: None,
    )

    with caplog.at_level(logging.ERROR, logger="fatty_trader.execution.bitget_protection_watchdog"):
        report = await watchdog.run_once()

    assert report.status is WatchdogStatus.FAILED
    assert report.allow_new_entries == {"BTCUSDT": False}
    assert report.reasons == ("provider-position-read-failed:BitgetApiError:30006",)
    messages = [record.getMessage() for record in caplog.records]
    assert any("provider-position-read-failed" in message for message in messages), messages


async def test_watchdog_reuses_the_monitor_reason_helper(
    capability_repository: InMemoryProtectionCapabilityRepository,
) -> None:
    """Monitor and watchdog must not drift into two spellings of the same cause."""
    assert (
        provider_read_failure_reason("provider-position", BitgetApiError("x", code="00000"))
        == "provider-position-read-failed:BitgetApiError:00000"
    )
    assert provider_read_failure_reason("provider-fills", TimeoutError("x")) == (
        "provider-fills-read-failed:TimeoutError"
    )
