from __future__ import annotations

from decimal import Decimal

import pytest

from fatty_trader.execution import bitget_fallback_protection as fallback


class FakeClient:
    def __init__(self, positions: list[list[dict[str, str]]]) -> None:
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


@pytest.fixture
def active_entry() -> dict[str, object]:
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "symbol": "BTCUSDT",
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

    result = await fallback.run_fallback_monitor_async(client)

    assert result == []
    assert client.close_calls == []
    assert cancelled == [str(active_entry["id"])]


@pytest.mark.asyncio
async def test_fallback_persists_closing_until_provider_is_flat(
    monkeypatch: pytest.MonkeyPatch, active_entry: dict[str, object]
) -> None:
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

    result = await fallback.run_fallback_monitor_async(client)

    assert result == []
    assert len(client.close_calls) == 1
    assert client.close_calls[0]["side"] == "SELL"
    assert any(kind == "closing" for kind, _ in calls)
    assert not any(kind == "triggered" for kind, _ in calls)


@pytest.mark.asyncio
async def test_fallback_marks_triggered_only_after_flat_readback(
    monkeypatch: pytest.MonkeyPatch, active_entry: dict[str, object]
) -> None:
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

    result = await fallback.run_fallback_monitor_async(client)

    assert len(result) == 1
    assert result[0]["reason"] == "sl_hit"
    assert len(client.close_calls) == 1
    assert any(kind == "triggered" for kind, _ in calls)
    assert any(kind == "update" and args[1] == "filled" for kind, args in calls)
