"""Real service -> canonical image validator -> committed paper SQL, fake provider only."""

import asyncio
import hashlib
import json
import os
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from fatty_trader import service
from fatty_trader.analyzer.codex_runner import CodexRunResult
from fatty_trader.storage.migrations import PAPER_KAKA_SCHEMA_SQL


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["valid", "unknown", "contradictory"])
async def test_paper_image_service_commits_audited_event(monkeypatch, tmp_path, scenario):
    dsn = os.environ.get("FATTY_TEST_POSTGRES_DSN", "")
    if not dsn:
        pytest.skip("Dedicated disposable PostgreSQL required")
    info = psycopg.conninfo.conninfo_to_dict(dsn)
    assert info.get("dbname") == "fatty_test" and info.get("user") == "fatty_test"
    assert str(info.get("host", "")).startswith("/") and info.get("password") == "testonly"
    original_connect = psycopg.connect
    schema = "paper_image_" + uuid4().hex
    with original_connect(dsn, autocommit=True) as admin:
        assert admin.execute(
            "SELECT current_database(), current_user, inet_server_addr()"
        ).fetchone() == ("fatty_test", "fatty_test", None)
        admin.execute(f'CREATE SCHEMA "{schema}"')

    def connect(**kwargs):
        connection = original_connect(dsn, row_factory=dict_row)
        connection.execute(f'SET search_path TO "{schema}", public')
        return connection

    try:
        chart = tmp_path / "chart.png"
        chart.write_bytes(b"fake chart fixture; provider output explicitly injected")
        digest = hashlib.sha256(chart.read_bytes()).hexdigest()
        revision = hashlib.sha256(b"telegram source fixture revision").hexdigest()
        with connect() as connection:
            connection.execute(PAPER_KAKA_SCHEMA_SQL)
            connection.execute(
                "CREATE TABLE telegram_messages (id uuid PRIMARY KEY, channel_id bigint, "
                "message_id bigint, raw_text text, media_path text, revision_hash text, "
                "media_sha256 text, intake_state text, received_at timestamptz)"
            )
            connection.execute(
                "INSERT INTO telegram_messages VALUES (%s,%s,%s,'',%s,%s,%s,'RECEIVED',now())",
                (uuid4(), -1003763643270, 321, str(chart), revision, digest),
            )

        class Runner:
            def run(self, prompt, *, image_paths):
                assert image_paths == (str(chart),)
                setup = {
                    "action": "ENTER",
                    "asset": "ETH",
                    "side": "SHORT",
                    "entry": 2400,
                    "stop_loss": None
                    if scenario == "unknown"
                    else (2350 if scenario == "contradictory" else 2450),
                    "take_profits": [2300],
                }
                return CodexRunResult(
                    True,
                    False,
                    False,
                    0,
                    None,
                    json.dumps({"message": "fixture", "setup": setup}),
                    "",
                )

        from fatty_trader.analyzer import market_price

        monkeypatch.setattr(psycopg, "connect", connect)
        monkeypatch.setattr(service, "build_codex_runner", lambda env: Runner())
        monkeypatch.setattr(market_price, "public_last_price", lambda symbol: Decimal("2400"))

        async def no_digest(hour):
            pass

        async def stop(seconds):
            raise asyncio.CancelledError

        monkeypatch.setattr(service, "_maybe_enqueue_digest", no_digest)
        monkeypatch.setattr(service.asyncio, "sleep", stop)
        with pytest.raises(asyncio.CancelledError):
            await service.run_paper_kaka({"PAPER_KAKA_IMAGE_ANALYSIS": "1"})
        with connect() as connection:
            trades = connection.execute("SELECT * FROM paper_kaka_trades").fetchall()
            events = connection.execute("SELECT * FROM paper_kaka_events").fetchall()
            assert len(events) == 1
            assert (
                connection.execute("SELECT intake_state FROM telegram_messages").fetchone()[
                    "intake_state"
                ]
                == "ANALYZED"
            )
            if scenario != "valid":
                assert not trades and events[0]["event_type"] == "MEDIA_ONLY"
            else:
                assert len(trades) == 1 and events[0]["event_type"] == "OPEN"
                assert trades[0]["entry_price"] == Decimal("2400")
                audit = json.loads(events[0]["parsed_json"]["raw"])
                assert audit["source_revision"] == revision and audit["media_sha256"] == digest
    finally:
        with original_connect(dsn, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
            assert not admin.execute(
                "SELECT 1 FROM pg_namespace WHERE nspname=%s", (schema,)
            ).fetchall()
