"""Offline, stateful PostgreSQL intake regression proofs; no Telegram/provider calls."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from test_postgres_migration_and_margin_admission import _connect, _migrate
from test_postgres_migration_and_margin_admission import postgres_schema as postgres_schema

from fatty_trader.intake.catchup import build_cursor_lookup, catch_up_missed
from fatty_trader.intake.persistence import PostgresRawMessageRepository
from fatty_trader.intake.telegram import TelegramIntake
from fatty_trader.storage.intake_schema import INTAKE_COVERAGE_SCHEMA_SQL

CHANNEL = -1001252615519


def setup(postgres_schema):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    return lambda: _connect(dsn, schema)


def message(number, text="audit"):
    return SimpleNamespace(
        id=number, message=text, date=datetime.now(UTC), media=None, reply_to=None
    )


class History:
    def __init__(self, ids, push=None):
        self.ids = ids
        self.calls = []
        self.push = push

    async def get_entity(self, channel):
        return CHANNEL

    async def iter_messages(self, entity, *, min_id, reverse, limit):
        self.calls.append(min_id)
        for number in [n for n in self.ids if n > min_id][:limit]:
            if self.push:
                self.push()
                self.push = None
            yield message(number)


class Forwarder:
    def __init__(self, repository):
        self.intake = TelegramIntake(repository)

    async def handle_message(self, channel, item):
        self.intake.ingest(channel_id=channel, message=item)


def poll(factory, history, limit=2):
    return asyncio.run(
        catch_up_missed(
            client=history,
            forwarder=Forwarder(PostgresRawMessageRepository(factory)),
            channels=("@offline",),
            cursor_lookup=build_cursor_lookup(factory),
            peer_id_of=lambda e: e,
            per_run_limit=limit,
        )
    )


def test_resumed_realtime_does_not_skip_blind_window(postgres_schema):
    factory = setup(postgres_schema)
    intake = TelegramIntake(PostgresRawMessageRepository(factory))
    intake.ingest(channel_id=CHANNEL, message=message(100))
    # Existing baseline, then resumed push before the catch-up pass.
    with factory() as c:
        c.execute(
            "INSERT INTO telegram_catchup_coverage VALUES (%s,100) ON CONFLICT DO NOTHING",
            (CHANNEL,),
        )
    intake.ingest(channel_id=CHANNEL, message=message(105))
    history = History(
        [101, 103, 104, 105], push=lambda: intake.ingest(channel_id=CHANNEL, message=message(110))
    )
    # Deleted 102 is not a coverage hole; 110 arriving mid-page must not skip 104.
    assert poll(factory, history) == 2
    assert poll(factory, history) == 2  # new callable/connection proves restart persistence
    assert history.calls == [100, 103]
    with factory() as c:
        assert c.execute(
            "SELECT message_id FROM telegram_messages ORDER BY message_id"
        ).fetchall() == [(100,), (101,), (103,), (104,), (105,), (110,)]
        assert c.execute("SELECT covered_message_id FROM telegram_catchup_coverage").fetchone() == (
            105,
        )
    assert poll(factory, history) == 0


def test_cold_start_first_sighting_anchors_without_following_realtime_max(postgres_schema):
    factory = setup(postgres_schema)
    history = History([50, 100])
    assert poll(factory, history) == 0
    assert history.calls == []
    intake = TelegramIntake(PostgresRawMessageRepository(factory))
    intake.ingest(channel_id=CHANNEL, message=message(100))
    intake.ingest(channel_id=CHANNEL, message=message(105))
    assert build_cursor_lookup(factory)(CHANNEL) == 100


def test_failed_history_persistence_does_not_advance_coverage(postgres_schema):
    import pytest

    factory = setup(postgres_schema)
    TelegramIntake(PostgresRawMessageRepository(factory)).ingest(
        channel_id=CHANNEL, message=message(100)
    )

    class FailingForwarder:
        async def handle_message(self, channel, item):
            raise RuntimeError("offline persistence failed")

    with pytest.raises(RuntimeError, match="persistence failed"):
        asyncio.run(
            catch_up_missed(
                client=History([101, 103]),
                forwarder=FailingForwarder(),
                channels=("@offline",),
                cursor_lookup=build_cursor_lookup(factory),
                peer_id_of=lambda e: e,
            )
        )
    assert build_cursor_lookup(factory)(CHANNEL) == 100
    assert poll(factory, History([101, 103])) == 2
    assert build_cursor_lookup(factory)(CHANNEL) == 103


def test_upgrade_seed_is_conservative_and_replay_safe(postgres_schema):
    factory = setup(postgres_schema)
    intake = TelegramIntake(PostgresRawMessageRepository(factory))
    intake.ingest(channel_id=CHANNEL, message=message(100))
    intake.ingest(channel_id=CHANNEL, message=message(105))
    sql = INTAKE_COVERAGE_SCHEMA_SQL
    with factory() as c:
        c.execute("DELETE FROM telegram_catchup_coverage")
        c.execute(sql)
    assert build_cursor_lookup(factory)(CHANNEL) == 100
    assert poll(factory, History([101, 103, 104, 105]), limit=2) == 2
    with factory() as c:
        c.execute(sql)
    assert build_cursor_lookup(factory)(CHANNEL) == 103
