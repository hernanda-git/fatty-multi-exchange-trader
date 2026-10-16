"""Disposable PostgreSQL proofs of actual runtime paths under restricted authority."""

# ruff: noqa: F401, F811
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from test_console_jobs_postgres import console_db, prepared
from test_durable_entry_admission import request as admission_request
from test_postgres_migration_and_margin_admission import _connect, _migrate, postgres_schema

from fatty_trader.console.store import ConsoleStore
from fatty_trader.execution import bitget_fallback_protection as fallback
from fatty_trader.intake.persistence import PostgresRawMessageRepository, RawTelegramMessage
from fatty_trader.notifications import PostgresNotificationOutbox
from fatty_trader.storage.balance_reservations import PostgresBitgetMarginReservationRepository
from fatty_trader.storage.migrations import apply_migrations
from fatty_trader.storage.worker_privileges import WORKER_PRIVILEGES_SQL


def runtime_connection(dsn, schema, role="fatty_worker"):
    connection = _connect(dsn, schema)
    # SESSION authorization, not SET ROLE: a superuser session_user could otherwise
    # SET ROLE back to admin and make an escalation-denial test meaningless.
    connection.execute(sql.SQL("SET SESSION AUTHORIZATION {}").format(sql.Identifier(role)))
    connection.commit()
    return connection


@pytest.fixture
def worker_db(postgres_schema):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    return dsn, schema, lambda: runtime_connection(dsn, schema)


def test_worker_cannot_administer_roles_schema_or_migration_history(worker_db):
    dsn, schema, factory = worker_db
    with _connect(dsn, schema) as admin:
        admin_name = admin.info.user
    forbidden = [
        "CREATE TABLE runtime_ddl_forbidden(id integer)",
        "CREATE TEMP TABLE runtime_temp_forbidden(id integer)",
        "CREATE SCHEMA runtime_schema_forbidden",
        "ALTER TABLE telegram_messages ADD COLUMN runtime_ddl_forbidden integer",
        "TRUNCATE telegram_messages",
        "UPDATE schema_migrations SET version=version",
        "CREATE ROLE runtime_role_forbidden",
        "ALTER ROLE fatty_worker SUPERUSER",
        sql.SQL("SET ROLE {}").format(sql.Identifier(admin_name)),
    ]
    with factory() as connection:
        assert connection.execute("SELECT current_user,session_user").fetchone() == (
            "fatty_worker",
            "fatty_worker",
        )
        assert (
            connection.execute(
                "SELECT rolsuper,rolcreatedb,rolcreaterole,rolinherit,rolreplication,rolbypassrls "
                "FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
            == (False,) * 6
        )
        for statement in forbidden:
            with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
                connection.execute(statement)
        with pytest.raises(psycopg.errors.InsufficientPrivilege), connection.transaction():
            connection.execute(WORKER_PRIVILEGES_SQL)


def test_real_runtime_intake_fallback_snapshots_audit_and_notification_paths(
    worker_db, monkeypatch
):
    _, _, factory = worker_db
    stamp = datetime.now(UTC)
    raw = RawTelegramMessage(
        channel_id=-1001234567890,
        message_id=1,
        revision_hash="a" * 64,
        raw_text="isolated privilege fixture, never a trading instruction",
        received_at=stamp,
        entry_expires_at=stamp + timedelta(minutes=5),
    )
    repository = PostgresRawMessageRepository(factory)
    repository.save_if_new(raw)
    repository.save_if_new(raw)
    monkeypatch.setattr(fallback, "_psycopg_connect", factory)
    fallback.register_fallback(
        "bitget",
        "BTCUSDT",
        "LONG",
        Decimal("100"),
        Decimal("90"),
        [Decimal("110")],
        Decimal("1"),
        "fixture-owner",
        provider_position_epoch="123456",
        environment="DEMO",
    )
    store = ConsoleStore(factory)
    scope = {"account_id": "fixture-only", "environment": "DEMO", "venue": "bitget"}
    snapshot = store.put_snapshot("positions", scope, [], observed_at=stamp)
    assert store.latest_snapshot("positions", scope)["id"] == snapshot["id"]
    assert store.audit("fixture.privileges", "permitted", scope=scope) > 0
    with factory() as connection:
        assert connection.execute("SELECT count(*) FROM telegram_messages").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM fallback_protection").fetchone() == (1,)
        connection.execute(
            "INSERT INTO notifications_outbox(id,dedup_key,payload) VALUES(%s,%s,'{}')",
            (uuid4(), "least-privilege-notification"),
        )
    outbox = PostgresNotificationOutbox(factory)
    notification = outbox.claim("fixture-worker", 30)
    assert notification is not None
    assert outbox.mark_sent(notification.id, notification.claim_token)


def test_actual_margin_admission_and_resolution_need_no_admin_authority(worker_db):
    dsn, schema, factory = worker_db
    request = admission_request(dsn, schema)
    repository = PostgresBitgetMarginReservationRepository(factory, console_enabled=True)
    result = repository.reserve(**request)
    assert result.accepted and result.reservation_id is not None
    repository.resolve(result.reservation_id, "FILLED")
    with factory() as connection:
        assert connection.execute(
            "SELECT state FROM bitget_margin_reservations WHERE id=%s", (result.reservation_id,)
        ).fetchone() == ("consumed",)


@pytest.mark.asyncio
async def test_real_console_enqueue_claim_and_finish_use_runtime_authority(console_db):
    store, service, _, dsn, schema = console_db
    store._connection_factory = lambda: runtime_connection(dsn, schema)
    _, request = await prepared(console_db)
    job = service.enqueue(request, console_db[2][0], str(uuid4()))
    claimed = store.claim_job("fixture-worker", ["position.close"])
    assert claimed["id"] == job["id"]
    store.begin_running(claimed)
    finished = store.finish_job(claimed, "succeeded", {"fixture": "no provider calls"})
    assert finished["state"] == "succeeded"


def test_admin_default_privileges_cover_future_tables_but_web_stays_isolated(worker_db):
    dsn, schema, factory = worker_db
    with _connect(dsn, schema) as admin:
        admin.execute(WORKER_PRIVILEGES_SQL)
        assert apply_migrations(admin.cursor()) == []
        admin.execute("CREATE TABLE future_runtime_fixture(id bigserial PRIMARY KEY, value text)")
    with factory() as connection:
        assert connection.execute(
            "INSERT INTO future_runtime_fixture(value) VALUES('runtime') RETURNING id"
        ).fetchone() == (1,)
        connection.execute("UPDATE future_runtime_fixture SET value='updated'")
        connection.execute("DELETE FROM future_runtime_fixture")
    with runtime_connection(dsn, schema, "fatty_console_web") as web:
        assert web.execute("SELECT count(*) FROM telegram_messages").fetchone() == (0,)
        with pytest.raises(psycopg.errors.InsufficientPrivilege), web.transaction():
            web.execute("DELETE FROM telegram_messages")
