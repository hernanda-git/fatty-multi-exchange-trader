"""Worker proofs against disposable PostgreSQL, fake model only, no provider I/O."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest

from fatty_trader.analyzer.codex_runner import CodexRunResult
from fatty_trader.analyzer.postgres_worker import process_received_batch


@pytest.fixture
def database():
    import psycopg

    dsn = os.environ.get("FATTY_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("requires disposable FATTY_TEST_POSTGRES_DSN")
    schema = f"fatty_analyzer_{uuid4().hex}"
    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.execute(
            "SELECT current_database(), current_user, inet_server_addr()"
        ).fetchone() == ("fatty_test", "fatty_test", None)
        conn.execute(f'CREATE SCHEMA "{schema}"')

    def connect():
        return psycopg.connect(dsn, options=f"-c search_path={schema}")

    try:
        with connect() as conn:
            conn.execute(
                "\n"
                "                CREATE TABLE telegram_messages (\n"
                "                    id uuid PRIMARY KEY, channel_id bigint, "
                "message_id bigint,\n"
                "                    revision_hash text, raw_text text, received_at "
                "timestamptz DEFAULT now(),\n"
                "                    has_media boolean DEFAULT false, media_path text, "
                "media_sha256 text,\n"
                "                    media_mime_type text, media_size_bytes bigint, "
                "intake_state text DEFAULT 'RECEIVED');\n"
                "                CREATE TABLE canonical_signals (\n"
                "                    id uuid PRIMARY KEY, message_id uuid REFERENCES "
                "telegram_messages(id),\n"
                "                    revision text, pair_token text, direction text, "
                "entry_price numeric,\n"
                "                    stop_loss numeric, take_profits jsonb, "
                "UNIQUE(message_id, revision));\n"
                "                CREATE TABLE dispatches (\n"
                "                    id uuid PRIMARY KEY, source_type text, source_id "
                "uuid REFERENCES canonical_signals(id),\n"
                "                    revision text, exchange text, state text,\n"
                "                    UNIQUE(source_type, source_id, revision, "
                "exchange));\n"
                "                CREATE TABLE source_management_updates (\n"
                "                    id uuid PRIMARY KEY, source_message_id uuid "
                "REFERENCES telegram_messages(id),\n"
                "                    revision text, symbol text, action text, state "
                "text,\n"
                "                    UNIQUE(source_message_id, revision, symbol, "
                "action));\n"
                "                CREATE TABLE notifications_outbox (\n"
                "                    id uuid PRIMARY KEY, dedup_key text UNIQUE, "
                "payload jsonb,\n"
                "                    CONSTRAINT reject_bad_analysis CHECK "
                "((payload->>'source_message_id')::bigint <> 2));\n"
                "            "
            )
            from fatty_trader.storage.intake_schema import INTAKE_COVERAGE_SCHEMA_SQL

            conn.execute(INTAKE_COVERAGE_SCHEMA_SQL)
        yield connect
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_genuine_sql_failure_rolls_back_only_bad_message_and_siblings_commit_once(database, capsys):
    with database() as conn:
        for message_id in (1, 2, 3):
            conn.execute(
                (
                    "INSERT INTO telegram_messages\n"
                    "                (id, channel_id, message_id, revision_hash, raw_text, "
                    "entry_expires_at, ingestion_origin)\n"
                    "                VALUES (%s, 7, %s, %s, '#ETH LONG ENTRY: 100 TARGET: "
                    "110 STOPLOSS: 95', now() + interval '5 minutes', 'realtime')"
                ),
                (uuid4(), message_id, "a" * 64),
            )
    calls = []

    def unavailable(prompt):
        calls.append(prompt)
        return CodexRunResult(False, True, False, 1, "unavailable", "", "")

    assert process_received_batch(database, runner=unavailable, channel_ids=(7,)) == 3
    assert "CheckViolation" in capsys.readouterr().out
    with database() as conn:
        assert conn.execute(
            "SELECT message_id, intake_state FROM telegram_messages ORDER BY message_id"
        ).fetchall() == [(1, "ANALYZED"), (2, "FAILED"), (3, "ANALYZED")]
        assert conn.execute("SELECT count(*) FROM canonical_signals").fetchone() == (2,)
        assert conn.execute("SELECT count(*) FROM dispatches").fetchone() == (4,)
        assert conn.execute("SELECT count(*) FROM notifications_outbox").fetchone() == (2,)
        assert conn.execute(
            "SELECT count(*) FROM canonical_signals s JOIN telegram_messages m ON "
            "s.message_id=m.id WHERE m.message_id=2"
        ).fetchone() == (0,)
    assert process_received_batch(database, runner=unavailable, channel_ids=(7,)) == 0
    assert len(calls) == 3
    with database() as conn:
        assert conn.execute("SELECT count(*) FROM dispatches").fetchone() == (4,)
