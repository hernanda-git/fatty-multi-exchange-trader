"""Exercise the service callback through the real paper worker, without providers."""

import asyncio
import hashlib
import json
from decimal import Decimal

import pytest

from fatty_trader import service
from fatty_trader.analyzer.codex_runner import CodexRunResult


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario", ["valid", "unknown", "contradictory", "original-veto", "legacy"]
)
async def test_chart_service_persists_valid_paper_event(monkeypatch, tmp_path, scenario):
    chart = tmp_path / "chart.png"
    chart.write_bytes(b"test chart fixture")
    row = {
        "id": "fixture-message",
        "message_id": 321,
        "raw_text": "",
        "media_path": str(chart),
        "revision_hash": hashlib.sha256(b"source identity").hexdigest(),
        "media_sha256": hashlib.sha256(chart.read_bytes()).hexdigest(),
    }
    if scenario == "legacy":
        row["revision_hash"] = None
    statements = []
    claimed = False

    class Cursor:
        def execute(self, sql, params=()):
            self.sql = sql
            statements.append((sql, params))

        def fetchall(self):
            nonlocal claimed
            if "FOR UPDATE SKIP LOCKED" in self.sql and not claimed:
                claimed = True
                return [row]
            return []

        def fetchone(self):
            if "FROM telegram_messages" in self.sql:
                return (
                    {**row, "raw_text": "Do not enter. Wait for confirmation."}
                    if scenario == "original-veto"
                    else row
                )
            return None

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda **kwargs: Connection())
    calls = []

    class Runner:
        def run(self, prompt, *, image_paths):
            calls.append((prompt, image_paths))
            return CodexRunResult(
                stdout=json.dumps(
                    {
                        "message": "Visible ETH short setup",
                        "setup": {
                            "action": "ENTER",
                            "asset": "ETH",
                            "side": "SHORT",
                            "entry": 2400,
                            "stop_loss": None
                            if scenario == "unknown"
                            else (2350 if scenario == "contradictory" else 2450),
                            "take_profits": [2300],
                        },
                    }
                ),
                stderr="",
                succeeded=True,
                terminal_failure=False,
                exit_code=0,
                failure_reason=None,
                timed_out=False,
            )

    monkeypatch.setattr(service, "build_codex_runner", lambda env: Runner())
    from fatty_trader.analyzer import market_price

    monkeypatch.setattr(market_price, "public_last_price", lambda symbol: Decimal("2400"))

    async def no_digest(hour):
        pass

    async def stop(seconds):
        raise asyncio.CancelledError

    monkeypatch.setattr(service, "_maybe_enqueue_digest", no_digest)
    monkeypatch.setattr(service.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        await service.run_paper_kaka({"PAPER_KAKA_IMAGE_ANALYSIS": "1"})
    assert calls[0][1] == (str(chart),)
    trades = [params for sql, params in statements if "INSERT INTO paper_kaka_trades" in sql]
    events = [params for sql, params in statements if "INSERT INTO paper_kaka_events" in sql]
    if scenario in {"unknown", "contradictory", "original-veto"}:
        assert not trades
        assert any(params[2] == "MEDIA_ONLY" for params in events)
        return
    assert len(trades) == 1
    assert trades[0][2:6] == ("ETHUSDT", "SHORT", Decimal("2400"), Decimal("2450"))
    assert any(params[2] == "OPEN" for params in events)
    opened = next(params for params in events if params[2] == "OPEN")
    audit = json.loads(json.loads(opened[4])["raw"])
    assert audit["media_sha256"] == row["media_sha256"]
    assert len(audit["source_revision"]) == 64
    if scenario == "valid":
        assert audit["source_revision"] == row["revision_hash"]
