"""Old source instructions cannot mutate a future position after downtime."""
# ruff: noqa: F401, F811

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from test_postgres_migration_and_margin_admission import _connect, _migrate, postgres_schema

from fatty_trader.storage.source_management import PostgresSourceManagementStore


def _management(connection, age, *, state="queued", claimed_close=False):
    message_id, update_id = uuid4(), uuid4()
    observed = datetime.now(UTC) - age
    revision = "a" * 64
    connection.execute(
        """INSERT INTO telegram_messages
        (id,channel_id,message_id,revision_hash,raw_text,received_at,
         intake_state,ingestion_origin,entry_expires_at)
        VALUES (%s,7,%s,%s,'$BTC close full position',%s,'ANALYZED','realtime',%s)""",
        (message_id, uuid4().int % 1000000000, revision, observed, observed + timedelta(minutes=5)),
    )
    connection.execute(
        """INSERT INTO source_management_updates
        (id,source_message_id,revision,symbol,action,state,updated_at)
        VALUES (%s,%s,%s,'BTCUSDT','CLOSE',%s,%s)""",
        (update_id, message_id, revision, state, observed),
    )
    if claimed_close:
        connection.execute(
            "INSERT INTO source_management_provider_intents(management_update_id,client_order_id) "
            "VALUES (%s,%s)",
            (update_id, f"source-management-{update_id.hex}-close"),
        )
    return update_id


def test_stale_unsent_management_retires_but_ambiguous_close_remains_for_readback(postgres_schema):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    with _connect(dsn, schema) as connection:
        stale = _management(connection, timedelta(days=2))
        pending = _management(
            connection, timedelta(days=1), state="reconciliation-pending", claimed_close=True
        )
        fresh = _management(connection, timedelta(seconds=1))
    store = PostgresSourceManagementStore(lambda: _connect(dsn, schema))
    assert not store.management_is_eligible(stale)
    assert not store.management_is_eligible(pending)
    assert store.management_is_eligible(fresh)
    claimed = store.claim("source-management")
    assert claimed.id == pending
    with _connect(dsn, schema) as connection:
        assert (
            connection.execute(
                "SELECT state FROM source_management_updates WHERE id=%s", (stale,)
            ).fetchone()[0]
            == "failed"
        )
    store.update_state(pending, "reconciliation-pending")
    assert store.claim("source-management").id == fresh


def test_edited_revision_is_not_new_management_permission(postgres_schema):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    with _connect(dsn, schema) as connection:
        update_id = _management(connection, timedelta(seconds=1))
        connection.execute(
            "UPDATE telegram_messages SET revision_hash=%s WHERE id="
            "(SELECT source_message_id FROM source_management_updates WHERE id=%s)",
            ("b" * 64, update_id),
        )
    store = PostgresSourceManagementStore(lambda: _connect(dsn, schema))
    assert not store.management_is_eligible(update_id)
