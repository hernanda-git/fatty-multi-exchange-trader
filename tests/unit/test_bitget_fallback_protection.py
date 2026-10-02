from __future__ import annotations

from decimal import Decimal

import pytest

from fatty_trader.execution import bitget_fallback_protection as fallback


class FakeClient:
    def __init__(self, positions) -> None:
        self.positions = iter(positions)
        self.close_calls: list[dict[str, str]] = []

    async def get_single_position(self, symbol: str) -> list[dict[str, str]]:
        del symbol
        return next(self.positions)

    async def get_ticker(self, symbol: str) -> dict[str, str]:
        del symbol
        return {"markPrice": "90"}

    async def place_market_close(self, **kwargs: str) -> dict[str, str]:
        self.close_calls.append(kwargs)
        return {"orderId": "provider-close-1"}


def test_open_quantity_handles_bitget_envelope_and_rejects_malformed_rows() -> None:
    assert fallback._open_quantity({"data": [{"total": "0.008"}]}) == Decimal("0.008")
    assert fallback._open_quantity({"data": []}) == Decimal("0")
    assert fallback._open_quantity([{"symbol": "BTCUSDT"}]) is None


@pytest.mark.parametrize(
    "value",
    [
        [{"total": "0"}, {"symbol": "BTCUSDT"}],
        [{"total": "0"}, None],
        [{"total": "0"}, {"total": "NaN"}],
        [{"total": "0"}, {"total": "bad"}],
        [{"total": "-0.01"}],
        [{"total": "0", "size": "0.01"}],
        {"code": "40000", "data": []},
        {"data": [], "positionList": [{"total": "0.01"}]},
    ],
)
def test_any_malformed_position_readback_is_unknown(value):
    assert fallback._open_quantity(value) is None


@pytest.fixture
def active_entry() -> dict[str, object]:
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "symbol": "BTCUSDT",
        "environment": "DEMO",
        "direction": "LONG",
        "entry_price": Decimal("100"),
        "stop_loss": Decimal("95"),
        "take_profits": [Decimal("110")],
        "quantity": Decimal("0.01"),
        "state": "active",
        "close_order_id": None,
    }


@pytest.mark.asyncio
async def test_fallback_never_posts_when_provider_is_flat(
    monkeypatch: pytest.MonkeyPatch, active_entry: dict[str, object]
) -> None:
    client = FakeClient([[]])
    cancelled: list[str] = []
    monkeypatch.setattr(fallback, "load_active", lambda: [active_entry])
    monkeypatch.setattr(fallback, "mark_position_flat", cancelled.append)

    result = await fallback.run_fallback_monitor_async(client, environment="DEMO")

    assert result == []
    assert client.close_calls == []
    assert cancelled == [str(active_entry["id"])]


@pytest.mark.asyncio
async def test_fallback_persists_closing_until_provider_is_flat(
    monkeypatch: pytest.MonkeyPatch, active_entry: dict[str, object]
) -> None:
    monkeypatch.setattr(
        fallback, "_owned_quantity", lambda positions, entry: fallback._open_quantity(positions)
    )
    client = FakeClient([[{"total": "0.01"}], [{"total": "0.01"}]])
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(fallback, "load_active", lambda: [active_entry])
    monkeypatch.setattr(
        fallback, "_ensure_close_intent", lambda *args: calls.append(("intent", args))
    )
    monkeypatch.setattr(
        fallback, "_update_close_intent", lambda *args: calls.append(("update", args))
    )
    monkeypatch.setattr(fallback, "mark_closing", lambda *args: calls.append(("closing", args)))
    monkeypatch.setattr(fallback, "mark_triggered", lambda *args: calls.append(("triggered", args)))

    result = await fallback.run_fallback_monitor_async(client, environment="DEMO")

    assert result == []
    assert len(client.close_calls) == 1
    assert client.close_calls[0]["side"] == "SELL"
    assert any(kind == "closing" for kind, _ in calls)
    assert not any(kind == "triggered" for kind, _ in calls)


@pytest.mark.asyncio
async def test_flat_readback_alone_does_not_confirm_close(
    monkeypatch: pytest.MonkeyPatch, active_entry: dict[str, object]
) -> None:
    monkeypatch.setattr(
        fallback, "_owned_quantity", lambda positions, entry: fallback._open_quantity(positions)
    )
    client = FakeClient([[{"total": "0.01"}], []])
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(fallback, "load_active", lambda: [active_entry])
    monkeypatch.setattr(
        fallback, "_ensure_close_intent", lambda *args: calls.append(("intent", args))
    )
    monkeypatch.setattr(
        fallback, "_update_close_intent", lambda *args: calls.append(("update", args))
    )
    monkeypatch.setattr(fallback, "mark_closing", lambda *args: calls.append(("closing", args)))
    monkeypatch.setattr(fallback, "mark_triggered", lambda *args: calls.append(("triggered", args)))

    result = await fallback.run_fallback_monitor_async(client, environment="DEMO")

    assert result == []
    assert len(client.close_calls) == 1
    assert not any(kind == "triggered" for kind, _ in calls)
    assert any(kind == "update" and args[1] == "unknown" for kind, args in calls)


@pytest.mark.asyncio
async def test_fallback_does_not_post_when_close_intent_is_already_claimed(
    monkeypatch: pytest.MonkeyPatch, active_entry: dict[str, object]
) -> None:
    monkeypatch.setattr(
        fallback, "_owned_quantity", lambda positions, entry: fallback._open_quantity(positions)
    )
    client = FakeClient([[{"total": "0.01"}]])
    monkeypatch.setattr(fallback, "load_active", lambda: [active_entry])
    monkeypatch.setattr(fallback, "_ensure_close_intent", lambda *args: False)

    result = await fallback.run_fallback_monitor_async(client, environment="DEMO")

    assert result == []
    assert client.close_calls == []


@pytest.mark.asyncio
async def test_unknown_close_result_marks_fallback_closing_before_post(
    monkeypatch: pytest.MonkeyPatch, active_entry: dict[str, object]
) -> None:
    monkeypatch.setattr(
        fallback, "_owned_quantity", lambda positions, entry: fallback._open_quantity(positions)
    )

    class UnknownClient(FakeClient):
        async def place_market_close(self, **kwargs: str) -> dict[str, str]:
            self.close_calls.append(kwargs)
            raise TimeoutError("close result unknown")

    client = UnknownClient([[{"total": "0.01"}]])
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(fallback, "load_active", lambda: [active_entry])
    monkeypatch.setattr(fallback, "_ensure_close_intent", lambda *args: True)
    monkeypatch.setattr(fallback, "mark_closing", lambda *args: calls.append(("closing", args)))
    monkeypatch.setattr(
        fallback, "_update_close_intent", lambda *args: calls.append(("update", args))
    )

    await fallback.run_fallback_monitor_async(client, environment="DEMO")

    assert len(client.close_calls) == 1
    assert any(kind == "closing" for kind, _ in calls)
    assert any(kind == "update" and args[1] == "unknown" for kind, args in calls)


@pytest.mark.asyncio
async def test_stream_close_callback_clamps_to_live_quantity_and_reconciles(
    monkeypatch: pytest.MonkeyPatch, active_entry: dict[str, object]
) -> None:
    monkeypatch.setattr(
        fallback, "_owned_quantity", lambda positions, entry: fallback._open_quantity(positions)
    )
    client = FakeClient([[{"total": "0.008"}], []])
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(fallback, "_ensure_close_intent", lambda *args: True)
    monkeypatch.setattr(
        fallback, "_update_close_intent", lambda *args: calls.append(("update", args))
    )
    monkeypatch.setattr(fallback, "mark_closing", lambda *args: calls.append(("closing", args)))
    monkeypatch.setattr(fallback, "mark_triggered", lambda *args: calls.append(("triggered", args)))

    result = await fallback.submit_fallback_close_async(
        client,
        active_entry,
        mark_price=Decimal("94"),
        reason="sl_hit",
        environment="DEMO",
    )

    assert result["submitted"] is True
    assert result["reason"] == "close-fill-evidence-pending"
    assert client.close_calls[0]["quantity"] == "0.008"
    assert not any(kind == "triggered" for kind, _ in calls)
    assert any(kind == "update" and args[1] == "unknown" for kind, args in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "positions",
    [
        [{"symbol": "PUMPUSDT", "holdSide": "long", "cTime": "2000", "total": "0.01"}],
        [{"symbol": "BTCUSDT", "holdSide": "short", "cTime": "1000", "total": "0.01"}],
        [{"symbol": "OTHERUSDT", "holdSide": "long", "cTime": "1000", "total": "0.01"}],
        [{"symbol": "BTCUSDT", "holdSide": "long", "cTime": "2000", "total": "0.01"}],
        [{"symbol": "BTCUSDT", "holdSide": "long", "cTime": "1000", "total": "0.02"}],
        [{"total": "0.01"}],
    ],
)
async def test_ambiguous_or_replacement_ownership_never_posts(monkeypatch, active_entry, positions):
    client = FakeClient([positions, []])
    monkeypatch.setattr(fallback, "load_active", lambda: [active_entry])
    monkeypatch.setattr(fallback, "_ensure_close_intent", lambda *args: True)
    monkeypatch.setattr(fallback, "mark_closing", lambda *args: None)
    monkeypatch.setattr(fallback, "mark_triggered", lambda *args: None)
    monkeypatch.setattr(fallback, "_update_close_intent", lambda *args: None)
    await fallback.run_fallback_monitor_async(client, environment="DEMO")
    assert client.close_calls == []


@pytest.mark.asyncio
async def test_flat_closing_without_matched_fills_never_fabricates_price(monkeypatch, active_entry):
    active_entry["state"] = "closing"
    client = FakeClient([[]])
    events = []
    monkeypatch.setattr(fallback, "load_active", lambda: [active_entry])
    monkeypatch.setattr(
        fallback, "mark_triggered", lambda *args: events.append(("triggered", args))
    )
    monkeypatch.setattr(
        fallback, "_update_close_intent", lambda *args: events.append(("intent", args))
    )
    await fallback.run_fallback_monitor_async(client, environment="DEMO")
    assert client.close_calls == []
    assert not any(name == "triggered" for name, _ in events)
    assert not any(name == "intent" and args[1] == "filled" for name, args in events)


@pytest.mark.asyncio
async def test_rest_malformed_readback_is_unknown(monkeypatch, active_entry):
    monkeypatch.setattr(
        fallback, "_owned_quantity", lambda positions, entry: fallback._open_quantity(positions)
    )
    client = FakeClient([[{"total": "0.01"}], {"unexpected": "shape"}])
    events = []
    monkeypatch.setattr(fallback, "load_active", lambda: [active_entry])
    monkeypatch.setattr(fallback, "_ensure_close_intent", lambda *args: True)
    monkeypatch.setattr(fallback, "mark_closing", lambda *args: None)
    monkeypatch.setattr(
        fallback, "mark_triggered", lambda *args: events.append(("triggered", args))
    )
    monkeypatch.setattr(
        fallback, "_update_close_intent", lambda *args: events.append(("intent", args))
    )
    assert await fallback.run_fallback_monitor_async(client, environment="DEMO") == []
    assert not any(name == "triggered" for name, _ in events)
    assert any(name == "intent" and args[1] == "unknown" for name, args in events)


@pytest.mark.asyncio
async def test_failed_fill_envelope_cannot_reach_accounting(monkeypatch, active_entry):
    class FailedReadClient:
        async def get_fills(self, symbol):
            return {"code": "40000", "data": {"fillList": [{"tradeId": "not-evidence"}]}}

    monkeypatch.setattr(
        fallback,
        "_persist_close_fills",
        lambda *args, **kwargs: pytest.fail("failed envelope is not evidence"),
    )
    assert (
        await fallback._reconcile_close_evidence(FailedReadClient(), active_entry, flat=True)
        is False
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("positions", [[], [{"total": "0.004"}]])
async def test_stream_callback_recovers_closing_get_only(monkeypatch, active_entry, positions):
    active_entry["state"] = "closing"
    client = FakeClient([positions])
    reads = []

    async def reconcile(client, entry, *, flat):
        reads.append(flat)
        return flat

    monkeypatch.setattr(fallback, "_reconcile_close_evidence", reconcile)
    monkeypatch.setattr(
        fallback, "mark_position_flat", lambda *args: pytest.fail("cannot cancel ambiguous close")
    )
    result = await fallback.submit_fallback_close_async(
        client, active_entry, mark_price=Decimal("90"), reason="sl_hit", environment="DEMO"
    )
    assert result["submitted"] is False
    assert reads == [not positions]
    assert client.close_calls == []


def test_acknowledgement_only_legacy_reconcile_cannot_fabricate_fill(monkeypatch):
    writes = []
    monkeypatch.setattr(fallback, "_db_exec", lambda *args: writes.append(args))
    fallback.reconcile_close("BTCUSDT", "close-oid", "provider-order")
    assert writes == []


@pytest.mark.asyncio
async def test_fallback_unknown_close_readback_is_not_confirmed_flat(
    monkeypatch: pytest.MonkeyPatch, active_entry: dict[str, object]
) -> None:
    monkeypatch.setattr(
        fallback, "_owned_quantity", lambda positions, entry: fallback._open_quantity(positions)
    )

    class UnknownReadbackClient(FakeClient):
        async def get_single_position(self, symbol: str) -> list[dict[str, str]] | dict[str, str]:
            del symbol
            if not self.close_calls:
                return [{"total": "0.01"}]
            return {"unexpected": "shape"}  # malformed is UNKNOWN, never flat

    client = UnknownReadbackClient([])
    events: list[tuple[str, tuple[object, ...]]] = []
    monkeypatch.setattr(fallback, "_ensure_close_intent", lambda *args: True)
    monkeypatch.setattr(
        fallback, "_update_close_intent", lambda *args: events.append(("intent", args))
    )
    monkeypatch.setattr(fallback, "mark_closing", lambda *args: events.append(("closing", args)))
    monkeypatch.setattr(
        fallback, "mark_triggered", lambda *args: events.append(("triggered", args))
    )

    result = await fallback.submit_fallback_close_async(
        client, active_entry, mark_price=Decimal("94"), reason="sl_hit", environment="DEMO"
    )

    assert result["reason"] == "close-readback-unknown"
    assert not any(name == "triggered" for name, _ in events)
    assert any(name == "intent" and args[1] == "unknown" for name, args in events)
