"""Offline timing proofs; never call a provider or change safety thresholds."""

import httpx
import pytest
from test_bitget_monitor import ReadOnlyVenue

from fatty_trader.exchanges.bitget import client as module
from fatty_trader.exchanges.bitget.client import (
    BitgetApiError,
    BitgetClockSkewInconclusiveError,
    BitgetRestClient,
    ClockSkewEstimate,
)
from fatty_trader.execution.bitget_monitor import BitgetMonitor
from fatty_trader.storage.reconciliation import InMemoryReconciliationRepository


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "offset,expected",
    [
        (0, ()),
        (6000, ("clock-skew-exceeded",)),
        (-6000, ("clock-skew-exceeded",)),
        (4950, ("clock-skew-inconclusive",)),
    ],
)
async def test_monitor_classifies_entire_uncertainty_interval(offset, expected):
    venue = ReadOnlyVenue(clock_skew_ms=ClockSkewEstimate(offset, 101))
    report = await BitgetMonitor(
        venue, InMemoryReconciliationRepository(), max_clock_skew_ms=5000
    ).run_once()
    assert report.reasons == expected


@pytest.mark.asyncio
async def test_monitor_reports_timing_uncertainty_separately_from_proved_skew():
    class Venue(ReadOnlyVenue):
        async def get_clock_skew_ms(self):
            raise BitgetClockSkewInconclusiveError("clock-skew-inconclusive")

    report = await BitgetMonitor(
        Venue(), InMemoryReconciliationRepository(), max_clock_skew_ms=5000
    ).run_once()
    assert report.reasons == ("clock-skew-inconclusive",)


@pytest.mark.asyncio
async def test_preflight_does_not_admit_uncertain_clock_boundary():
    from test_bitget_async_venue import Client

    from fatty_trader.exchanges.bitget.async_venue import AsyncBitgetVenue

    with pytest.raises(ValueError, match="clock-skew-inconclusive"):
        await AsyncBitgetVenue(Client(clock_skew_ms=ClockSkewEstimate(29950, 101))).preflight(
            "BTCUSDT"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("offset", [0, 6000, -6000])
async def test_clock_estimate_uses_request_midpoint(offset, monkeypatch):
    now = [1000.0]
    mono = [10.0]
    monkeypatch.setattr(module.time, "time", lambda: now[0])
    monkeypatch.setattr(module.time, "monotonic", lambda: mono[0])

    def respond(request):
        now[0] += 0.2
        mono[0] += 0.2
        return httpx.Response(200, json={"code": "00000", "data": {"serverTime": 1000100 - offset}})

    client = BitgetRestClient(
        "offline", "offline", "offline", transport=httpx.MockTransport(respond)
    )
    try:
        estimate = await client.get_clock_skew_ms()
        assert estimate == offset
        assert 100 <= estimate.uncertainty_ms <= 102
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("retry", [False, True])
async def test_synchronized_slow_or_retried_get_is_inconclusive_not_proved_skew(retry, monkeypatch):
    now = [1000.0]
    mono = [10.0]
    calls = 0
    monkeypatch.setattr(module.time, "time", lambda: now[0])
    monkeypatch.setattr(module.time, "monotonic", lambda: mono[0])

    def respond(request):
        nonlocal calls
        calls += 1
        now[0] += 3 if retry else 6
        mono[0] += 3 if retry else 6
        if retry and calls == 1:
            return httpx.Response(429)
        return httpx.Response(
            200, json={"code": "00000", "data": {"serverTime": int(now[0] * 1000)}}
        )

    async def sleep(delay):
        now[0] += delay
        mono[0] += delay

    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    client = BitgetRestClient(
        "offline", "offline", "offline", transport=httpx.MockTransport(respond)
    )
    try:
        with pytest.raises(BitgetApiError, match="clock-skew-inconclusive"):
            await client.get_clock_skew_ms()
        assert calls == (2 if retry else 1)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_wall_clock_step_during_request_is_inconclusive(monkeypatch):
    now = [1000.0]
    mono = [10.0]
    monkeypatch.setattr(module.time, "time", lambda: now[0])
    monkeypatch.setattr(module.time, "monotonic", lambda: mono[0])

    def respond(request):
        now[0] += 2
        mono[0] += 0.1
        return httpx.Response(200, json={"code": "00000", "data": {"serverTime": 1000050}})

    client = BitgetRestClient(
        "offline", "offline", "offline", transport=httpx.MockTransport(respond)
    )
    try:
        with pytest.raises(BitgetApiError, match="clock-skew-inconclusive"):
            await client.get_clock_skew_ms()
    finally:
        await client.aclose()
