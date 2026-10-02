"""Offline replay parity; prices and HTTP responses are deterministic fixtures."""

import importlib.util
import json
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "kaka_replay", Path(__file__).resolve().parents[2] / "scripts/replay_kaka_paper.py"
)
replay_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = replay_module
spec.loader.exec_module(replay_module)


@pytest.mark.parametrize("include_completed", [True, False])
def test_price_lookup_only_uses_previous_completed_candle(monkeypatch, include_completed):
    when = datetime.fromisoformat("2026-09-01T00:02:30+00:00")
    minute_ms = int(when.timestamp() // 60) * 60000
    candles = [[str(minute_ms), "0", "0", "0", "999"]]
    if include_completed:
        candles.append([str(minute_ms - 60000), "0", "0", "0", "101"])

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self):
            return json.dumps({"data": candles}).encode()

    urls = []

    def fixture_urlopen(url, **_):
        urls.append(url)
        return Response()

    monkeypatch.setattr(replay_module, "urlopen", fixture_urlopen)
    replay_module._cache.clear()
    assert replay_module.price_at("ETHUSDT", when, retries=0) == (
        Decimal(101) if include_completed else None
    )
    assert f"startTime={minute_ms - 60000}" in urls[0]
    assert f"endTime={minute_ms - 1}" in urls[0]


def test_replay_duplicate_dca_changes_quantity_once():
    result = replay_module.replay(
        [
            row(1, "Long ETH ENTRY 100 SL 90"),
            row(2, "ETH DCA 110"),
            row(2, "ETH DCA 110"),
            row(3, "ETH Cancel TP"),
            row(4, "ETH Close"),
        ],
        price_lookup=lambda *_: Decimal(120),
    )
    assert len(result.trades) == 1
    assert result.trades[0]["legs"] == 2
    assert result.trades[0]["pnl_usdt"] == Decimal("5.766691")


def row(mid, text):
    return {"message_id": mid, "text": text, "date_utc": f"2026-09-01T00:{mid:02}:30+00:00"}


@pytest.mark.parametrize(
    "opens, close",
    [
        (["Long ETH ENTRY 100 SL 90", "Long BTC ENTRY 200 SL 180"], "Close"),
        (["Long ETH ENTRY 100 SL 90"], "BTC Close"),
    ],
)
def test_replay_refuses_ambiguous_or_missing_explicit_target(opens, close):
    rows = [row(i + 1, text) for i, text in enumerate(opens)] + [row(3, close)]
    result = replay_module.replay(rows, price_lookup=lambda *_: Decimal(110))
    assert all(t["pnl_usdt"] is None for t in result.trades)
    assert result.skipped == [{"message_id": 3, "reason": "unmatched:CLOSE"}]
