"""The real packaged migration entry must install intake coverage and source expiry."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from test_postgres_migration_and_margin_admission import _connect, _migrate
from test_postgres_migration_and_margin_admission import postgres_schema as postgres_schema

from fatty_trader.storage import migrations


def test_actual_fresh_migrations_install_intake_coverage_and_expiry(postgres_schema):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    with _connect(dsn, schema) as c:
        assert (
            c.execute("SELECT to_regclass('telegram_catchup_coverage')").fetchone()[0] is not None
        )
        columns = {
            row[0]
            for row in c.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name='telegram_messages'",
                (schema,),
            ).fetchall()
        }
        assert {"ingestion_origin", "entry_expires_at", "entry_rejection_reason"} <= columns
        assert c.execute("SELECT version FROM schema_migrations WHERE version=21").fetchone() == (
            21,
        )


def test_actual_upgrade_21_seeds_min_expires_stale_and_replay_preserves_data(
    postgres_schema, monkeypatch
):
    dsn, schema = postgres_schema
    with monkeypatch.context() as patch:
        patch.setattr(
            migrations, "MIGRATIONS", [(v, sql) for v, sql in migrations.MIGRATIONS if v <= 20]
        )
        _migrate(dsn, schema)
    with _connect(dsn, schema) as c:
        for number, state in [(100, "RECEIVED"), (105, "ANALYZED")]:
            c.execute(
                "INSERT INTO telegram_messages "
                "(id,channel_id,message_id,revision_hash,raw_text,received_at,intake_state) "
                "VALUES (%s,7,%s,%s,'$BTC long sl 90',%s,%s)",
                (uuid4(), number, "a" * 64, datetime.now(UTC) - timedelta(days=2), state),
            )
    with _connect(dsn, schema) as c:
        assert migrations.apply_migrations(c.cursor()) == [
            version for version, _ in migrations.MIGRATIONS if version > 20
        ]
        assert c.execute(
            "SELECT channel_id,covered_message_id FROM telegram_catchup_coverage"
        ).fetchall() == [(7, 100)]
        assert c.execute(
            "SELECT message_id,intake_state,entry_rejection_reason,entry_expires_at<now() "
            "FROM telegram_messages ORDER BY message_id"
        ).fetchall() == [
            (100, "EXPIRED", "stale-source-message", True),
            (105, "ANALYZED", None, True),
        ]
        c.execute("UPDATE telegram_catchup_coverage SET covered_message_id=103 WHERE channel_id=7")
    with _connect(dsn, schema) as c:
        assert migrations.apply_migrations(c.cursor()) == []
        assert c.execute("SELECT covered_message_id FROM telegram_catchup_coverage").fetchone() == (
            103,
        )
        assert c.execute("SELECT count(*) FROM telegram_messages").fetchone() == (2,)
