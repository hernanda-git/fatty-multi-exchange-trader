from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from fatty_trader.exchanges.bitget.live import InMemoryLiveIntentStore, LiveIntentRecord
from fatty_trader.execution.bitget_monitor import BitgetMonitor
from fatty_trader.storage.reconciliation import InMemoryReconciliationRepository


@dataclass
class ReadOnlyVenue:
    positions: list[dict[str, str]] = field(default_factory=list)
    orders: list[dict[str, str]] = field(default_factory=list)
    plans: list[dict[str, str]] = field(default_factory=list)
    details: dict[str, dict[str, str]] = field(default_factory=dict)
    fills: list[dict[str, str]] = field(default_factory=list)
    clock_skew_ms: int = 0
    calls: list[str] = field(default_factory=list)

    async def get_all_positions(self) -> list[dict[str, str]]:
        self.calls.append("get_all_positions")
        return self.positions

    async def get_pending_orders(self) -> list[dict[str, str]]:
        self.calls.append("get_pending_orders")
        return self.orders

    async def get_pending_plan_orders(self, symbol: str) -> list[dict[str, str]]:
        self.calls.append("get_pending_plan_orders")
        return self.plans

    async def get_single_position(self, symbol: str) -> list[dict[str, str]]:
        self.calls.append("get_single_position")
        return [row for row in self.positions if row.get("symbol") == symbol]

    async def get_order_detail(self, symbol: str, *, client_oid: str) -> dict[str, str]:
        self.calls.append("get_order_detail")
        return self.details[client_oid]

    async def get_fills(self, symbol: str | None = None) -> list[dict[str, str]]:
        self.calls.append("get_fills")
        return self.fills

    async def get_clock_skew_ms(self) -> int:
        self.calls.append("get_clock_skew_ms")
        return self.clock_skew_ms


@pytest.mark.asyncio
async def test_clean_flat_account_is_ok_without_latching_kill_switch() -> None:
    venue = ReadOnlyVenue()
    repository = InMemoryReconciliationRepository()

    report = await BitgetMonitor(venue, repository).run_once()

    assert report.status == "ok"
    assert report.reasons == ()
    assert repository.kill_switch_active("bitget") is False
    assert repository.alerts == []
    assert all(call.startswith("get_") for call in venue.calls)


@pytest.mark.asyncio
async def test_empty_bitget_pending_order_envelope_is_normalized() -> None:
    venue = ReadOnlyVenue()
    venue.get_pending_orders = lambda: _empty_order_envelope()  # type: ignore[method-assign]
    repository = InMemoryReconciliationRepository()

    report = await BitgetMonitor(venue, repository).run_once()

    assert report.status == "ok"
    assert repository.kill_switch_active("bitget") is False


@pytest.mark.asyncio
async def test_fill_read_failure_is_not_misdiagnosed_as_shape_failure(caplog) -> None:
    from fatty_trader.exchanges.bitget.client import BitgetApiError

    class FailedVenue(ReadOnlyVenue):
        async def get_fills(self, symbol=None):
            raise BitgetApiError("do not log secret-provider-body", code="429", http_status=429)

    repository = InMemoryReconciliationRepository()
    report = await BitgetMonitor(
        FailedVenue(), repository, live_intent_store=InMemoryLiveIntentStore()
    ).run_once()
    assert report.reasons == ("provider-fills-read-failed",)
    assert repository.kill_switch_active("bitget")
    assert "exception=BitgetApiError" in caplog.text
    assert "code=429" in caplog.text
    assert "http_status=429" in caplog.text
    assert "secret-provider-body" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"fillList": None}, [None], None])
async def test_invalid_fill_shape_latches_with_safe_shape_diagnostic(payload, caplog) -> None:
    class InvalidVenue(ReadOnlyVenue):
        async def get_fills(self, symbol=None):
            return payload

    repository = InMemoryReconciliationRepository()
    report = await BitgetMonitor(
        InvalidVenue(), repository, live_intent_store=InMemoryLiveIntentStore()
    ).run_once()
    assert report.reasons == ("provider-fills-invalid",)
    assert repository.kill_switch_active("bitget")
    assert "reason=provider-fills-invalid" in caplog.text
    assert "shape=" in caplog.text
    assert "exception=" not in caplog.text


@pytest.mark.asyncio
async def test_clean_cycle_exposes_persisted_latch_separately_from_current_failures() -> None:
    repository = InMemoryReconciliationRepository()
    repository.latch_kill_switch("bitget", "provider-fills-invalid")
    report = await BitgetMonitor(ReadOnlyVenue(), repository).run_once()
    assert report.status == "kill-switch-latched"
    assert report.reasons == ()  # Clean current evidence, not a new fill failure.
    assert report.latched_reason == "provider-fills-invalid"


@pytest.mark.asyncio
async def test_unavailable_clock_latches_and_clean_next_cycle_does_not_release():
    class Venue(ReadOnlyVenue):
        unavailable = True

        async def get_clock_skew_ms(self):
            if self.unavailable:
                raise TimeoutError("unavailable clock evidence")
            return 0

    venue = Venue()
    repository = InMemoryReconciliationRepository()
    monitor = BitgetMonitor(venue, repository)
    first = await monitor.run_once()
    assert first.reasons == ("clock-skew-unavailable",)
    assert repository.kill_switch_active("bitget")
    venue.unavailable = False
    second = await monitor.run_once()
    assert second.status == "kill-switch-latched"
    assert second.reasons == ()
    assert second.latched_reason == "clock-skew-unavailable"
    assert repository.kill_switch_active("bitget")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [
        "unresolved_intents",
        "expected_position_symbols",
        "has_unhandled_post_fill_mismatch",
        "kill_switch_active",
        "kill_switch_reason",
        "latch_kill_switch",
        "update_intent",
    ],
)
async def test_monitor_storage_does_not_delay_in_flight_clock_response(
    monkeypatch, operation
) -> None:
    import asyncio
    import threading
    import time

    from fatty_trader.exchanges.bitget.client import BitgetRestClient

    entered = threading.Event()
    release = threading.Event()
    request_started = asyncio.Event()
    respond = asyncio.Event()
    repository = InMemoryReconciliationRepository()
    venue = ReadOnlyVenue()
    if operation == "kill_switch_reason":
        repository.latch_kill_switch("bitget", "clock-skew-unavailable")
    elif operation == "latch_kill_switch":
        venue.clock_skew_ms = 10_001
    elif operation == "update_intent":
        repository.intents.append(
            LiveIntentRecord(
                exchange="bitget",
                client_oid="known-unknown",
                symbol="BTCUSDT",
                side="BUY",
                state="unknown",
                requested_qty=Decimal("0.01"),
            )
        )
        venue.details["known-unknown"] = {"status": "cancelled", "orderId": "provider-1"}
    original = getattr(repository, operation)

    def block_store(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            release.wait(timeout=2)
        return original(*args, **kwargs)

    monkeypatch.setattr(repository, operation, block_store)

    async def time_response(*args, **kwargs):
        request_started.set()
        await respond.wait()
        return {"serverTime": int(time.time() * 1000)}

    client = BitgetRestClient("", "", "")
    monkeypatch.setattr(client, "_request", time_response)
    clock_task = asyncio.create_task(client.get_clock_skew_ms())
    monitor_task = None
    try:
        await asyncio.wait_for(request_started.wait(), timeout=1)
        monitor_task = asyncio.create_task(BitgetMonitor(venue, repository).run_once())
        assert await asyncio.to_thread(entered.wait, 1)
        respond.set()
        await asyncio.wait_for(clock_task, timeout=1)
        assert not monitor_task.done(), "storage must not block shared clock/socket I/O"
    finally:
        release.set()
        respond.set()
        await asyncio.gather(
            clock_task, *([monitor_task] if monitor_task else []), return_exceptions=True
        )
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("storage_fails", [False, True])
async def test_storage_boundary_drains_repeated_cancellation_without_hiding_failures(
    storage_fails,
) -> None:
    import asyncio
    import threading

    from fatty_trader.execution.async_storage import _run_sync_storage

    entered = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    writes = []

    def persist():
        entered.set()
        release.wait(timeout=2)
        writes.append("committed")
        completed.set()
        if storage_fails:
            raise RuntimeError("storage failure")
        return "stored"

    operation = asyncio.create_task(_run_sync_storage(persist))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        operation.cancel("initial timeout")
        await asyncio.sleep(0)
        operation.cancel("second cancellation")
        await asyncio.sleep(0)
        assert not operation.done(), "cancellation cannot abandon an in-flight storage write"
        assert not completed.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError, match="initial timeout") as cancelled:
            await operation
        assert completed.is_set()
        assert writes == ["committed"]
        if storage_fails:
            assert isinstance(cancelled.value.__cause__, RuntimeError)
            assert str(cancelled.value.__cause__) == "storage failure"
        else:
            assert cancelled.value.__cause__ is None
    finally:
        release.set()
        await asyncio.gather(operation, return_exceptions=True)


@pytest.mark.parametrize(
    "operation",
    [
        "unresolved_intents",
        "expected_position_symbols",
        "kill_switch_active",
        "kill_switch_reason",
        "has_unhandled_post_fill_mismatch",
        "latch_kill_switch",
        "update_intent",
        "alert_protection_unconfirmed",
    ],
)
@pytest.mark.parametrize("fails", [False, True])
def test_postgres_monitor_repository_closes_owned_connection(operation, fails):
    from fatty_trader.storage.reconciliation import PostgresReconciliationRepository

    events = []
    failure = RuntimeError("SQL failed")

    class Connection:
        def cursor(self):
            return self

        def execute(self, *args):
            events.append("execute")
            if fails:
                raise failure

        def fetchall(self):
            return []

        def fetchone(self):
            return (False,) if operation == "has_unhandled_post_fill_mismatch" else None

        def commit(self):
            events.append("commit")

        def rollback(self):
            events.append("rollback")

        def close(self):
            events.append("close")

    repository = PostgresReconciliationRepository(Connection)
    argument = (
        LiveIntentRecord(exchange="bitget", client_oid="owned", symbol="BTCUSDT", side="BUY")
        if operation == "update_intent"
        else "bitget"
    )
    args = (argument, "clock-skew-unavailable") if operation == "latch_kill_switch" else (argument,)
    if operation == "alert_protection_unconfirmed":
        args = (argument, "protection-alert:dedup", {"kind": "protection-alert"})
    if fails:
        with pytest.raises(RuntimeError) as raised:
            getattr(repository, operation)(*args)
        assert raised.value is failure
    else:
        getattr(repository, operation)(*args)
    assert events[-1] == "close"
    assert events.count("close") == 1
    if operation in {"latch_kill_switch", "update_intent", "alert_protection_unconfirmed"}:
        assert events[-2] == ("rollback" if fails else "commit")


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["positions", "orders"])
@pytest.mark.parametrize("failure", ["exception", "shape"])
async def test_monitor_row_read_diagnostics_distinguish_exception_from_shape(
    endpoint,
    failure,
    caplog,
) -> None:
    venue = ReadOnlyVenue()

    async def reader():
        if failure == "exception":
            raise TimeoutError("secret-debug-body")
        return [None]

    method = "get_all_positions" if endpoint == "positions" else "get_pending_orders"
    setattr(venue, method, reader)
    repository = InMemoryReconciliationRepository()
    report = await BitgetMonitor(venue, repository).run_once()
    expected = f"provider-{endpoint}-{'read-failed' if failure == 'exception' else 'invalid'}"
    assert report.reasons == (expected,)
    assert repository.kill_switch_active("bitget")
    assert f"reason={expected}" in caplog.text
    assert ("exception=TimeoutError" if failure == "exception" else "shape=list") in caplog.text
    assert "secret-debug-body" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("rate_limited_reads", [1, 3])
async def test_real_rest_client_monitor_retries_transient_429_but_latches_exhaustion(
    rate_limited_reads,
    monkeypatch,
    caplog,
) -> None:
    import time

    import httpx

    from fatty_trader.exchanges.bitget.client import BitgetRestClient

    delays = []
    fills_reads = []

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr("asyncio.sleep", sleep)

    def handler(request):
        assert request.method == "GET"  # Production monitor stays provider-read-only.
        path = request.url.path
        if path.endswith("/fills"):
            fills_reads.append(request)
            if len(fills_reads) <= rate_limited_reads:
                return httpx.Response(429, text="never-log-sensitive-body")
            data = {"fillList": [], "endId": ""}
        elif path.endswith("/time"):
            data = {"serverTime": str(int(time.time() * 1000))}
        elif path.endswith("/orders-pending"):
            data = {"entrustedList": None, "endId": None}
        else:
            assert path.endswith("/all-position")
            data = []
        return httpx.Response(200, json={"code": "00000", "data": data})

    client = BitgetRestClient("test", "test", "test", transport=httpx.MockTransport(handler))
    repository = InMemoryReconciliationRepository()
    try:
        report = await BitgetMonitor(
            client, repository, live_intent_store=InMemoryLiveIntentStore()
        ).run_once()
    finally:
        await client.aclose()
    if rate_limited_reads == 1:
        assert report.status == "ok"
        assert report.reasons == ()
        assert not repository.kill_switch_active("bitget")
        assert len(fills_reads) == 2
        assert delays == [1.0]
    else:
        assert report.reasons == ("provider-fills-read-failed",)
        assert repository.kill_switch_active("bitget")
        assert len(fills_reads) == 3
        assert delays == [1.0, 2.0]
        assert "http_status=429" in caplog.text
    assert "never-log-sensitive-body" not in caplog.text


async def _empty_order_envelope() -> dict[str, object]:
    return {"entrustedList": None, "endId": None}


@pytest.mark.asyncio
async def test_unknown_intent_is_reconciled_by_get_only_without_latching_kill_switch() -> None:
    intent = LiveIntentRecord(
        exchange="bitget",
        client_oid="known-unknown",
        symbol="BTCUSDT",
        side="BUY",
        state="unknown",
        requested_qty=Decimal("0.01"),
    )
    venue = ReadOnlyVenue(
        details={"known-unknown": {"status": "filled", "orderId": "provider-1"}},
        fills=[{"fillId": "fill-1", "orderId": "provider-1", "size": "0.01", "price": "60000"}],
    )
    repository = InMemoryReconciliationRepository(intents=[intent])

    report = await BitgetMonitor(venue, repository).run_once()

    assert report.status == "ok"
    assert repository.intents[0].state == "filled"
    assert repository.intents[0].provider_order_id == "provider-1"
    assert venue.calls.count("get_order_detail") == 1
    assert all(call.startswith("get_") for call in venue.calls)


@pytest.mark.asyncio
async def test_demo_monitor_reports_but_never_latches_kill_switch() -> None:
    venue = ReadOnlyVenue(orders=[{"clientOid": "foreign-demo-order"}])
    repository = InMemoryReconciliationRepository()

    report = await BitgetMonitor(venue, repository, enforce_kill_switch=False).run_once()

    assert report.status == "degraded"
    assert report.reasons == ("unexpected-order:foreign-demo-order",)
    assert repository.kill_switch_active("bitget") is False
    assert repository.alerts == []


@pytest.mark.asyncio
async def test_persisted_post_fill_mismatch_latches_and_blocks_entries() -> None:
    """A durable mismatch must latch: the recording adapter cannot block a restart."""
    venue = ReadOnlyVenue()
    repository = InMemoryReconciliationRepository()
    repository.record_post_fill_mismatch()

    report = await BitgetMonitor(venue, repository).run_once()

    assert report.status == "kill-switch-latched"
    assert report.reasons == ("post-fill-margin-or-leverage-mismatch",)
    assert repository.kill_switch_active("bitget") is True
    assert repository.alerts == ["post-fill-margin-or-leverage-mismatch"]


@pytest.mark.asyncio
async def test_post_fill_mismatch_is_alert_only_when_enforcement_is_off() -> None:
    venue = ReadOnlyVenue()
    repository = InMemoryReconciliationRepository()
    repository.record_post_fill_mismatch()

    report = await BitgetMonitor(venue, repository, enforce_kill_switch=False).run_once()

    assert report.status == "degraded"
    assert repository.kill_switch_active("bitget") is False


@pytest.mark.asyncio
async def test_release_clears_an_old_mismatch_but_a_new_one_relatches() -> None:
    """A released switch must be releasable: only mismatches after it re-latch."""
    venue = ReadOnlyVenue()
    repository = InMemoryReconciliationRepository()
    repository.record_post_fill_mismatch()
    first = await BitgetMonitor(venue, repository).run_once()
    assert first.status == "kill-switch-latched"

    repository.release_kill_switch("bitget", "operator-approved-release")
    released = await BitgetMonitor(venue, repository).run_once()
    assert released.status == "ok"
    assert repository.kill_switch_active("bitget") is False

    repository.record_post_fill_mismatch()
    again = await BitgetMonitor(venue, repository).run_once()
    assert again.status == "kill-switch-latched"
    assert repository.kill_switch_active("bitget") is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("positions", "orders", "plans", "clock_skew_ms", "reason"),
    [
        (
            [{"symbol": "BTCUSDT", "total": "0.01", "marginMode": "crossed"}],
            [],
            [],
            0,
            "margin-mode-not-isolated",
        ),
        ([], [{"clientOid": "foreign-order"}], [], 0, "unexpected-order:foreign-order"),
        ([], [], [], 10_001, "clock-skew-exceeded"),
    ],
)
async def test_unsafe_provider_read_latches_kill_switch_and_deduplicates_alert(
    positions: list[dict[str, str]],
    orders: list[dict[str, str]],
    plans: list[dict[str, str]],
    clock_skew_ms: int,
    reason: str,
) -> None:
    venue = ReadOnlyVenue(
        positions=positions, orders=orders, plans=plans, clock_skew_ms=clock_skew_ms
    )
    repository = InMemoryReconciliationRepository(
        expected_symbols={"BTCUSDT"} if positions else set()
    )
    monitor = BitgetMonitor(venue, repository, max_clock_skew_ms=10_000)

    first = await monitor.run_once()
    second = await monitor.run_once()

    assert first.status == second.status == "kill-switch-latched"
    assert reason in first.reasons
    assert repository.kill_switch_active("bitget") is True
    assert repository.alerts == [reason]
    assert all(call.startswith("get_") for call in venue.calls)


@pytest.mark.asyncio
async def test_missing_protection_is_alert_only_without_fallback_mutations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fatty_trader.execution import bitget_fallback_protection, bitget_monitor

    monkeypatch.setattr(bitget_fallback_protection, "load_active", lambda: [])
    monkeypatch.setattr(bitget_monitor, "_symbol_capability", lambda symbol, environment=None: None)
    venue = ReadOnlyVenue(
        positions=[{"symbol": "BTCUSDT", "total": "0.01", "marginMode": "isolated"}]
    )
    repository = InMemoryReconciliationRepository(expected_symbols={"BTCUSDT"})

    report = await BitgetMonitor(venue, repository).run_once()

    assert report.status == "degraded"
    assert report.reasons == ("missing-stop-loss",)
    assert repository.kill_switch_active("bitget") is False
    # Alert-only: durable and detailed, never a latch and never a close.
    assert repository.alerts == []
    assert len(repository.alert_payloads) == 1


@pytest.mark.asyncio
async def test_missing_protection_is_degraded_when_fallback_monitor_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fatty_trader.execution import bitget_fallback_protection, bitget_monitor

    venue = ReadOnlyVenue(
        positions=[{"symbol": "BTCUSDT", "total": "0.01", "marginMode": "isolated"}]
    )
    repository = InMemoryReconciliationRepository(expected_symbols={"BTCUSDT"})
    fallback_calls: list[str] = []

    async def run_fallback(client: object, *, environment=None) -> list[dict[str, str]]:
        del client
        assert environment is None
        fallback_calls.append("called")
        return []

    monkeypatch.setattr(bitget_fallback_protection, "run_fallback_monitor_async", run_fallback)
    monkeypatch.setattr(bitget_fallback_protection, "load_active", lambda: [])
    monkeypatch.setattr(bitget_monitor, "_symbol_capability", lambda symbol, environment=None: None)

    report = await BitgetMonitor(
        venue,
        repository,
        fallback_mutations_enabled=True,
    ).run_once()

    assert report.status == "degraded"
    assert report.reasons == ("missing-stop-loss",)
    assert fallback_calls == ["called"]
    assert repository.kill_switch_active("bitget") is False
    assert repository.alerts == []


def _lane_row(**overrides: object) -> dict[str, object]:
    """One durable local-management row as the REST lane persists it."""
    row: dict[str, object] = {
        "id": "fallback-1",
        "exchange": "bitget",
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "entry_price": Decimal("100"),
        "stop_loss": Decimal("95"),
        "take_profits": [Decimal("110"), Decimal("120")],
        "quantity": Decimal("0.01"),
        "state": "active",
        "close_order_id": None,
        "position_key": "entry-root-1",
        "provider_position_epoch": "1699999999000",
        "environment": "DEMO",
    }
    row.update(overrides)
    return row


def _stub_protection(
    monkeypatch: pytest.MonkeyPatch,
    *,
    lane_rows: list[dict[str, object]],
    capability: object | None,
) -> None:
    from fatty_trader.execution import bitget_fallback_protection, bitget_monitor

    async def no_triggers(client: object, *, environment=None) -> list[dict[str, str]]:
        del client, environment
        return []

    monkeypatch.setattr(bitget_fallback_protection, "load_active", lambda: list(lane_rows))
    monkeypatch.setattr(bitget_fallback_protection, "run_fallback_monitor_async", no_triggers)
    monkeypatch.setattr(
        bitget_monitor, "_symbol_capability", lambda symbol, environment=None: capability
    )


def _capability(**overrides: object) -> object:
    from fatty_trader.exchanges.bitget.protection_capability import (
        BitgetProtectionCapability,
        StreamState,
    )

    values: dict[str, object] = {
        "exchange": "bitget",
        "environment": "DEMO",
        "symbol": "BTCUSDT",
        "stream_state": StreamState.HEALTHY,
        "last_stream_at": datetime.now(UTC),
    }
    values.update(overrides)
    return BitgetProtectionCapability(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_unconfirmed_protection_alert_is_detailed_and_once_per_position_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BITGET_PROTECTION_STREAM_ENABLED", "1")
    monkeypatch.setenv("BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED", "1")
    _stub_protection(monkeypatch, lane_rows=[_lane_row()], capability=_capability())
    venue = ReadOnlyVenue(
        positions=[
            {
                "symbol": "BTCUSDT",
                "total": "0.01",
                "holdSide": "long",
                "marginMode": "isolated",
                "openPriceAvg": "100.5",
                "cTime": "1700000000000",
            }
        ]
    )
    repository = InMemoryReconciliationRepository(expected_symbols={"BTCUSDT"})
    monitor = BitgetMonitor(venue, repository, fallback_mutations_enabled=True)

    report = await monitor.run_once()
    await monitor.run_once()

    assert report.status == "degraded"
    assert report.reasons == ("missing-stop-loss",)
    assert repository.kill_switch_active("bitget") is False
    # One alert for the epoch, and the second cycle did not queue another.
    assert len(repository.alert_payloads) == 1
    payload = repository.alert_payloads[0]
    assert payload["kind"] == "protection-alert"
    assert payload["reason"] == "missing-stop-loss"
    assert payload["verdict"] == "missing-stop-loss-locally-managed"
    assert payload["capability_readback_reason"] == "unavailable"
    assert payload["scope"] == "bitget"
    assert payload["symbol"] == "BTCUSDT"
    assert payload["direction"] == "LONG"
    assert payload["quantity"] == "0.01"
    assert payload["entry_price"] == "100.5"
    assert payload["stop_loss"] == "95"
    assert payload["take_profits"] == ["110", "120"]
    assert payload["position_key"] == "entry-root-1"
    assert payload["provider_position_epoch"] == "1700000000000"
    assert payload["locally_managed"] is True
    assert payload["local_lane_state"] == "active"
    assert payload["local_lane_rest_mutations_enabled"] is True
    assert payload["local_lane_stream_enabled"] is True
    assert payload["local_lane_stream_mutations_enabled"] is True
    assert payload["local_lane_stream_state"] == "HEALTHY"
    assert payload["local_lane_stream_fresh"] is True
    assert isinstance(payload["local_lane_stream_last_event_seconds"], float)
    assert payload["local_lane_registration_read_ok"] is True
    assert payload["protection_state"] == "DEGRADED"
    assert payload["observed_quantity"] == "0.01"
    assert payload["action"] == (
        "bot will not close this position; check the exchange side and manage it manually"
    )

    # A new provider position epoch is a new incident and alerts again.
    venue.positions[0]["cTime"] = "1700000005000"
    await monitor.run_once()

    assert len(repository.alert_payloads) == 2
    assert repository.alert_payloads[1]["provider_position_epoch"] == "1700000005000"


@pytest.mark.asyncio
async def test_unconfirmed_protection_alert_reports_unregistered_and_unarmed_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BITGET_PROTECTION_STREAM_ENABLED", raising=False)
    monkeypatch.delenv("BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED", raising=False)
    _stub_protection(monkeypatch, lane_rows=[], capability=None)
    venue = ReadOnlyVenue(
        positions=[
            {
                "symbol": "BTCUSDT",
                "total": "0.01",
                "holdSide": "short",
                "marginMode": "isolated",
            }
        ]
    )
    repository = InMemoryReconciliationRepository(expected_symbols={"BTCUSDT"})

    report = await BitgetMonitor(venue, repository).run_once()

    assert report.reasons == ("missing-stop-loss",)
    assert repository.kill_switch_active("bitget") is False
    payload = repository.alert_payloads[0]
    assert payload["direction"] == "SHORT"
    assert payload["locally_managed"] is False
    assert payload["verdict"] == "missing-stop-loss"
    assert payload["local_lane_state"] == "unregistered"
    assert payload["local_lane_rest_mutations_enabled"] is False
    assert payload["local_lane_stream_enabled"] is False
    assert payload["local_lane_stream_mutations_enabled"] is False
    assert payload["local_lane_stream_state"] == "DISABLED"
    assert payload["local_lane_stream_fresh"] is None
    assert payload["stop_loss"] == "unavailable"
    assert payload["take_profits"] == []
    assert payload["entry_price"] == "unavailable"
    assert payload["action"] == (
        "bot will not close this position; check the exchange side and manage it manually"
    )


@pytest.mark.parametrize(
    ("age_seconds", "expected_fresh"),
    [(0.0, True), (600.0, False)],
)
@pytest.mark.asyncio
async def test_unconfirmed_protection_alert_reports_stream_freshness(
    monkeypatch: pytest.MonkeyPatch,
    age_seconds: float,
    expected_fresh: bool,
) -> None:
    monkeypatch.setenv("BITGET_PROTECTION_STREAM_ENABLED", "1")
    monkeypatch.setenv("BITGET_PROTECTION_STREAM_MUTATIONS_ENABLED", "1")
    _stub_protection(
        monkeypatch,
        lane_rows=[_lane_row()],
        capability=_capability(last_stream_at=datetime.now(UTC) - timedelta(seconds=age_seconds)),
    )
    venue = ReadOnlyVenue(
        positions=[{"symbol": "BTCUSDT", "total": "0.01", "marginMode": "isolated"}]
    )
    repository = InMemoryReconciliationRepository(expected_symbols={"BTCUSDT"})

    await BitgetMonitor(venue, repository, fallback_mutations_enabled=True).run_once()

    payload = repository.alert_payloads[0]
    assert payload["locally_managed"] is True
    assert payload["local_lane_stream_state"] == "HEALTHY"
    assert payload["local_lane_stream_fresh"] is expected_fresh
    assert payload["local_lane_stream_last_event_seconds"] >= age_seconds


@pytest.mark.asyncio
async def test_locally_managed_verdict_alert_fires_for_a_latchable_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The adapter's recorded -locally-managed verdict is never silent."""
    _stub_protection(
        monkeypatch,
        lane_rows=[],
        capability=_capability(last_error="margin-mode-not-isolated-locally-managed"),
    )
    venue = ReadOnlyVenue(positions=[{"symbol": "BTCUSDT", "total": "0.01", "marginMode": "cross"}])
    repository = InMemoryReconciliationRepository(expected_symbols={"BTCUSDT"})

    report = await BitgetMonitor(venue, repository).run_once()

    assert report.reasons == ("margin-mode-not-isolated",)
    assert repository.alerts == ["margin-mode-not-isolated"]
    payload = repository.alert_payloads[0]
    assert payload["reason"] == "margin-mode-not-isolated"
    assert payload["verdict"] == "margin-mode-not-isolated-locally-managed"
    assert payload["capability_readback_reason"] == "margin-mode-not-isolated-locally-managed"
    assert payload["locally_managed"] is False
    assert payload["local_lane_state"] == "unregistered"


@pytest.mark.asyncio
async def test_unexpected_position_is_latched_even_when_native_protection_exists() -> None:
    venue = ReadOnlyVenue(
        positions=[{"symbol": "BTCUSDT", "total": "0.01", "marginMode": "isolated"}],
        plans=[
            {"planType": "loss_plan", "size": "0.01"},
            {"planType": "profit_plan", "size": "0.01"},
        ],
    )
    repository = InMemoryReconciliationRepository()

    report = await BitgetMonitor(venue, repository).run_once()

    assert report.status == "kill-switch-latched"
    assert report.reasons == ("unexpected-position:BTCUSDT",)


@pytest.mark.asyncio
async def test_system_liquidation_fill_is_reconciled_without_global_switch() -> None:
    venue = ReadOnlyVenue(
        fills=[
            {
                "symbol": "WLDUSDT",
                "side": "sell",
                "ordId": "provider-order-1",
                "tradeId": "provider-fill-1",
                "fillSz": "91",
                "fillPx": "0.3841",
                "fillFee": "-0.02097366",
                "profit": "-1.17999853",
                "enterPointSource": "SYS",
                "tradeSide": "burst_sell_single",
            }
        ]
    )
    reconciliation = InMemoryReconciliationRepository()
    intents = InMemoryLiveIntentStore()

    report = await BitgetMonitor(
        venue,
        reconciliation,
        live_intent_store=intents,
        enforce_kill_switch=False,
    ).run_once()

    assert report.status == "ok"
    assert report.provider_exits_reconciled == 1
    assert reconciliation.kill_switch_active("bitget") is False
    assert len(intents.fills) == 1
    assert intents.provider_events[0]["source"] == "SYSTEM_LIQUIDATION"
