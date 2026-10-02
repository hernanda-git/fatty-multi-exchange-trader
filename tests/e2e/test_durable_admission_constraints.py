"""Additive constraint proofs against disposable PostgreSQL."""

from uuid import uuid4

import psycopg
import pytest
from test_durable_entry_admission import repo, request
from test_postgres_migration_and_margin_admission import (
    _connect,
    _migrate,
)
from test_postgres_migration_and_margin_admission import (
    postgres_schema as _postgres_schema,
)

from fatty_trader.storage.migrations import apply_migrations

postgres_schema = _postgres_schema


def test_owned_symbol_requires_environment(postgres_schema):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    admitted = repo(dsn, schema).reserve(**request(dsn, schema))
    assert admitted.accepted and admitted.reservation_id is not None
    with _connect(dsn, schema) as c:
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute(
                "UPDATE bitget_margin_reservations SET environment=NULL WHERE id=%s",
                (admitted.reservation_id,),
            )
        c.rollback()


def test_duplicate_ownership_cannot_be_hidden_as_migration_replay(postgres_schema):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    first = repo(dsn, schema).reserve(**request(dsn, schema))
    assert first.accepted and first.reservation_id is not None
    second = request(dsn, schema)
    with _connect(dsn, schema) as c:
        c.execute("DROP INDEX bitget_margin_reservations_symbol_owner")
        c.execute("DELETE FROM schema_migrations WHERE version=19")
        c.execute(
            """INSERT INTO bitget_margin_reservations
            (id,exchange,dispatch_id,client_order_id,balance_snapshot_id,
             planned_margin_usdt,symbol,environment,state,expires_at)
            SELECT %s,exchange,%s,%s,balance_snapshot_id,planned_margin_usdt,
                   symbol,environment,state,expires_at
            FROM bitget_margin_reservations WHERE id=%s""",
            (uuid4(), second["dispatch_id"], second["client_order_id"], first.reservation_id),
        )
    with _connect(dsn, schema) as c:
        with pytest.raises(psycopg.errors.UniqueViolation):
            apply_migrations(c.cursor())
        c.rollback()
        assert c.execute("SELECT count(*) FROM schema_migrations WHERE version=19").fetchone() == (
            0,
        )
        assert c.execute("SELECT count(*) FROM bitget_margin_reservations").fetchone() == (2,)
