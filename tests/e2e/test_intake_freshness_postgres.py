"""Historical ENTERs are retained but cannot enter the current analyzer money lane."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from test_intake_coverage_postgres import CHANNEL, message, setup
from test_postgres_migration_and_margin_admission import postgres_schema as postgres_schema

from fatty_trader.analyzer.postgres_worker import process_received_batch
from fatty_trader.config.telegram import TelegramSettings
from fatty_trader.execution.bitget_dispatch_repository import PostgresBitgetDispatchRepository
from fatty_trader.intake.backfill import backfill_latest
from fatty_trader.intake.catchup import build_cursor_lookup, catch_up_missed
from fatty_trader.intake.persistence import PostgresRawMessageRepository
from fatty_trader.intake.telegram import TelegramForwarder, TelegramIntake
from fatty_trader.storage.intake_schema import INTAKE_COVERAGE_SCHEMA_SQL

ENV = {
    "TELEGRAM_API_ID": "1",
    "TELEGRAM_API_HASH": "offline",
    "TELEGRAM_SESSION": "offline",
    "TELEGRAM_SOURCE_CHANNELS": "@offline",
    "TELEGRAM_TARGET_CHAT_ID": "1",
}


class BaselineClient:
    def __init__(self, item):
        self.item = item

    async def start(self):
        pass

    async def disconnect(self):
        pass

    async def get_entity(self, channel):
        return CHANNEL

    async def iter_messages(self, entity, **kwargs):
        yield self.item


@pytest.mark.parametrize("text", ["$BTC long entry 100 sl 90", "$BTC long sl 90"])
@pytest.mark.parametrize("origin", ["catchup", "backfill"])
def test_stale_enter_retained_but_never_analyzed_or_dispatched(postgres_schema, text, origin):
    factory = setup(postgres_schema)
    repository = PostgresRawMessageRepository(factory)
    item = message(101, text)
    item.date = datetime.now(UTC) - timedelta(days=2)
    if origin == "backfill":
        asyncio.run(
            backfill_latest(
                ENV,
                client_factory=lambda s: BaselineClient(item),
                repository=repository,
                peer_id_of=lambda e: e,
            )
        )
    else:
        TelegramIntake(repository).ingest(channel_id=CHANNEL, message=message(100))
        with factory() as c:
            c.execute("UPDATE telegram_messages SET intake_state='ANALYZED'")
        client = BaselineClient(item)
        forwarder = TelegramForwarder(client, TelegramSettings.from_mapping(ENV), repository)
        asyncio.run(
            catch_up_missed(
                client=client,
                forwarder=forwarder,
                channels=("@offline",),
                cursor_lookup=build_cursor_lookup(factory),
                peer_id_of=lambda e: e,
            )
        )
    with factory() as c:
        row = c.execute(
            "SELECT raw_text,intake_state FROM telegram_messages WHERE message_id=101"
        ).fetchone()
        assert row == (text, "EXPIRED")
    calls = []

    def forbidden_runner(prompt):
        calls.append(prompt)
        raise AssertionError(
            "stale explicit/stop-only must never reach provider/market-price fallback"
        )

    assert process_received_batch(factory, runner=forbidden_runner) == 0
    assert calls == []
    assert PostgresBitgetDispatchRepository(factory).claim("offline-intake-proof", 30) is None
    with factory() as c:
        assert c.execute("SELECT count(*) FROM canonical_signals").fetchone() == (0,)
        assert c.execute("SELECT count(*) FROM dispatches").fetchone() == (0,)
        assert c.execute(
            "SELECT ingestion_origin,entry_rejection_reason "
            "FROM telegram_messages WHERE message_id=101"
        ).fetchone() == (origin, "stale-source-message")


@pytest.mark.parametrize("origin", ["catchup", "backfill"])
def test_recent_history_stays_eligible_for_current_analysis(postgres_schema, origin):
    from fatty_trader.analyzer.codex_runner import CodexRunResult

    factory = setup(postgres_schema)
    repository = PostgresRawMessageRepository(factory)
    item = message(101, "#BTC LONG ENTRY: 100 TARGET: 110 STOPLOSS: 90")
    client = BaselineClient(item)
    if origin == "backfill":
        asyncio.run(
            backfill_latest(
                ENV, client_factory=lambda s: client, repository=repository, peer_id_of=lambda e: e
            )
        )
    else:
        TelegramIntake(repository).ingest(channel_id=CHANNEL, message=message(100))
        with factory() as c:
            c.execute("UPDATE telegram_messages SET intake_state='ANALYZED'")
        forwarder = TelegramForwarder(client, TelegramSettings.from_mapping(ENV), repository)
        asyncio.run(
            catch_up_missed(
                client=client,
                forwarder=forwarder,
                channels=("@offline",),
                cursor_lookup=build_cursor_lookup(factory),
                peer_id_of=lambda e: e,
            )
        )
    with factory() as c:
        assert c.execute(
            "SELECT intake_state,ingestion_origin,entry_rejection_reason,entry_expires_at>now() "
            "FROM telegram_messages WHERE message_id=101"
        ).fetchone() == ("RECEIVED", origin, None, True)

    def unavailable(prompt):
        return CodexRunResult(False, True, False, 1, "offline", "", "")

    assert process_received_batch(factory, runner=unavailable) == 1
    with factory() as c:
        assert c.execute("SELECT count(*) FROM canonical_signals").fetchone() == (1,)
        assert c.execute("SELECT count(*) FROM dispatches").fetchone() == (2,)
    assert PostgresBitgetDispatchRepository(factory).claim("offline-recent-proof", 30) is not None


def test_proposed_upgrade_expires_legacy_stale_received_rows(postgres_schema):
    from uuid import uuid4

    factory = setup(postgres_schema)
    with factory() as c:
        c.execute(
            """INSERT INTO telegram_messages
            (id,channel_id,message_id,revision_hash,raw_text,received_at,intake_state)
            VALUES (%s,%s,99,%s,'$BTC long sl 90',%s,'RECEIVED')""",
            (uuid4(), CHANNEL, "a" * 64, datetime.now(UTC) - timedelta(days=2)),
        )
        c.execute(INTAKE_COVERAGE_SCHEMA_SQL)
        assert c.execute(
            "SELECT intake_state,entry_rejection_reason,entry_expires_at<now() "
            "FROM telegram_messages"
        ).fetchone() == ("EXPIRED", "stale-source-message", True)
