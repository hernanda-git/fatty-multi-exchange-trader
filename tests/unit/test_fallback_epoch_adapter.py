"""Credential-free real REST adapter ownership/serialization proofs."""

import json
from dataclasses import replace

import httpx
import pytest
from test_bitget_async_execution import _filled_entry_intent, _protection_plan
from test_fallback_registration_truth import persisted_fallback_rows as persisted_fallback_rows

from fatty_trader.exchanges.bitget.async_execution import AsyncBitgetExecution
from fatty_trader.exchanges.bitget.async_venue import AsyncBitgetVenue
from fatty_trader.exchanges.bitget.client import BitgetRestClient
from fatty_trader.exchanges.bitget.live import InMemoryLiveIntentStore
from fatty_trader.execution.protection import ProtectionState


def fixture_client(mode="DEMO", epoch="1000", fill_epoch="1000", positions=None, fills=None):
    intent = replace(_filled_entry_intent(), state="filled", provider_order_id="order-1")
    requests = []
    position = {"symbol": "BTCUSDT", "holdSide": "long", "total": "0.001", "cTime": epoch}
    fill = {
        "symbol": "BTCUSDT",
        "side": "buy",
        "tradeSide": "open",
        "orderId": "order-1",
        "clientOid": intent.client_oid,
        "tradeId": "100",
        "baseVolume": "0.001",
        "price": "50000",
        "cTime": fill_epoch,
    }

    def respond(request):
        requests.append(request)
        assert request.headers["ACCESS-KEY"] == "test-key"
        assert (request.headers.get("paptrading") == "1") == (mode == "DEMO")
        path = request.url.path
        if path.endswith("place-pos-tpsl"):
            body = json.loads(request.content)
            assert body["symbol"] == "BTCUSDT"
            assert body["holdSide"] == "buy"
            assert "size" not in body
            assert "stopLossSize" not in body
            assert body["stopLossTriggerPrice"] == "48000"
            return httpx.Response(
                200, json={"code": "43011", "msg": "symbol does not support stop loss"}
            )
        if path.endswith("single-position"):
            assert request.url.params["symbol"] == "BTCUSDT"
            data = positions if positions is not None else [position]
        elif path.endswith("fills"):
            batch = fills if fills is not None else [fill]
            if request.url.params.get("idLessThan"):
                data = {"fillList": [], "endId": ""}
            else:
                data = {"fillList": batch, "endId": batch[-1]["tradeId"] if batch else ""}
        else:
            raise AssertionError(f"unexpected request {request.method} {path}")
        return httpx.Response(200, json={"code": "00000", "data": data})

    client = BitgetRestClient(
        "test-key", "test-secret", "test-pass", mode=mode, transport=httpx.MockTransport(respond)
    )
    store = InMemoryLiveIntentStore()
    store.claim(intent)
    return client, store, intent, requests


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["DEMO", "LIVE"])
async def test_exact_provider_epoch_and_authenticated_environment_are_persisted(
    mode, persisted_fallback_rows
):
    client, store, intent, requests = fixture_client(mode)
    try:
        adapter = AsyncBitgetExecution(
            client, AsyncBitgetVenue(client), environment=mode, fallback_protection_enabled=True
        )
        assert await adapter._fallback_identity(intent, _protection_plan(), store) == ("1000", mode)
        result = await adapter.protect_filled_position(intent, _protection_plan(), store)
        assert persisted_fallback_rows[0][-2:] == ("1000", mode)
        assert persisted_fallback_rows[0][7] == intent.client_oid
        assert any(r.url.path.endswith("single-position") for r in requests)
        assert any(r.url.path.endswith("fills") for r in requests)
        assert result.state is ProtectionState.DEGRADED
        assert result.reason == "native-protection-unsupported-fallback-registered-not-enforcing"
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["DEMO", "LIVE"])
async def test_monitor_passes_authenticated_environment(mode, monkeypatch):
    from types import SimpleNamespace

    from fatty_trader.execution import bitget_fallback_protection as fallback
    from fatty_trader.execution.bitget_monitor import BitgetMonitor

    received = []

    async def run(client, *, environment=None):
        received.append(environment)
        return []

    monkeypatch.setattr(fallback, "run_fallback_monitor_async", run)
    monitor = BitgetMonitor(
        SimpleNamespace(environment=mode), SimpleNamespace(), fallback_mutations_enabled=True
    )
    reasons = []
    await monitor._run_fallback_monitor(reasons)
    assert received == [mode]
    assert not reasons


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"epoch": None},
        {"epoch": "01000"},
        {"epoch": "0"},
        {"epoch": "2000"},
        {"fill_epoch": "999"},
        {"fills": []},
        {"positions": []},
        {
            "positions": [
                {"symbol": "BTCUSDT", "holdSide": "short", "total": "0.001", "cTime": "1000"}
            ]
        },
        {
            "positions": [
                {"symbol": "BTCUSDT", "holdSide": "long", "total": "0.002", "cTime": "1000"}
            ]
        },
    ],
)
async def test_unproven_or_replacement_position_cannot_register(kwargs, persisted_fallback_rows):
    client, store, intent, requests = fixture_client(**kwargs)
    try:
        adapter = AsyncBitgetExecution(
            client, AsyncBitgetVenue(client), environment="DEMO", fallback_protection_enabled=True
        )
        result = await adapter.protect_filled_position(intent, _protection_plan(), store)
        assert result.state is ProtectionState.DEGRADED
        assert result.reason == "fallback-registration-failed"
        assert persisted_fallback_rows == []
        assert not any(r.url.path.endswith("place-order") for r in requests)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_caller_environment_cannot_override_authenticated_environment(
    persisted_fallback_rows,
):
    client, store, intent, requests = fixture_client("DEMO")
    try:
        adapter = AsyncBitgetExecution(
            client, AsyncBitgetVenue(client), environment="LIVE", fallback_protection_enabled=True
        )
        result = await adapter.protect_filled_position(intent, _protection_plan(), store)
        assert result.state is ProtectionState.DEGRADED
        assert persisted_fallback_rows == []
    finally:
        await client.aclose()
