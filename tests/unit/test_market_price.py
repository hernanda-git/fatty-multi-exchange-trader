"""Offline tests for the public market-price lookup used by scalp signals."""

from __future__ import annotations

import json
import time
from decimal import Decimal

import pytest

from fatty_trader.analyzer import market_price


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def _stub(monkeypatch: pytest.MonkeyPatch, payload: object) -> list[str]:
    """Replace urlopen with a recorder returning ``payload`` as JSON."""
    calls: list[str] = []

    def fake_urlopen(url: str, timeout: float = 5.0) -> FakeResponse:
        calls.append(url)
        return FakeResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr(market_price, "urlopen", fake_urlopen)
    monkeypatch.setattr(market_price, "_cache", {})
    return calls


def ticker(symbol: str, price: str) -> dict:
    return {
        "code": "00000",
        "data": [{"symbol": symbol, "lastPr": price, "ts": str(int(time.time() * 1000))}],
    }


def test_reads_last_price_from_the_public_ticker(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(
        monkeypatch,
        ticker("ENAUSDT", "0.27217"),
    )

    price = market_price.public_last_price("ena")

    assert price == Decimal("0.27217")
    # Exact query: the pair token is suffixed once, and the endpoint rejects a doubled
    # suffix with HTTP 400 (that is how "ENAUSDTUSDT" failed in production).
    assert calls[0].endswith("?symbol=ENAUSDT&productType=USDT-FUTURES")


def test_price_is_cached_between_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, ticker("SOLUSDT", "0.5"))

    assert market_price.public_last_price("SOL") == Decimal("0.5")
    assert market_price.public_last_price("SOL") == Decimal("0.5")
    assert len(calls) == 1


def test_cache_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _stub(monkeypatch, ticker("SOLUSDT", "0.5"))

    market_price.public_last_price("SOL", now=1_000.0)
    market_price.public_last_price("SOL", now=1_000.0 + market_price._CACHE_TTL_SECONDS + 1)

    assert len(calls) == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"code": "40034", "msg": "error"},
        {"data": [{"symbol": "ENAUSDT"}]},
        {"data": [{"lastPr": "0"}]},
        {"data": [{"lastPr": "abc"}]},
        {"data": []},
    ],
)
def test_unusable_payloads_return_none(monkeypatch: pytest.MonkeyPatch, payload: object) -> None:
    _stub(monkeypatch, payload)

    assert market_price.public_last_price("ENA") is None


def test_network_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_url: str, timeout: float = 5.0) -> FakeResponse:
        raise OSError("network down")

    monkeypatch.setattr(market_price, "urlopen", boom)
    monkeypatch.setattr(market_price, "_cache", {})

    assert market_price.public_last_price("ENA") is None


@pytest.mark.parametrize(
    "mutation",
    ["wrong_symbol", "provider_error", "missing_timestamp", "stale", "future", "infinite", "nan"],
)
def test_quote_requires_success_exact_symbol_and_fresh_finite_data(monkeypatch, mutation):
    payload = ticker("ENAUSDT", "0.3")
    row = payload["data"][0]
    if mutation == "wrong_symbol":
        row["symbol"] = "BTCUSDT"
    elif mutation == "provider_error":
        payload["code"] = "40034"
    elif mutation == "missing_timestamp":
        del row["ts"]
    elif mutation == "stale":
        row["ts"] = str(int((time.time() - 31) * 1000))
    elif mutation == "future":
        row["ts"] = str(int((time.time() + 31) * 1000))
    else:
        row["lastPr"] = "Infinity" if mutation == "infinite" else "NaN"
    _stub(monkeypatch, payload)
    assert market_price.public_last_price("ENA") is None
    assert market_price._cache == {}
