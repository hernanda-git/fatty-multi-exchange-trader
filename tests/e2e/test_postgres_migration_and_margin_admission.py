"""Real-PostgreSQL proofs for idempotent migrations and margin admission."""
# ruff: noqa: E402

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")

from fatty_trader.storage.balance_reservations import PostgresBitgetMarginReservationRepository
from fatty_trader.storage.migrations import MIGRATIONS, apply_migrations
from fatty_trader.storage.reconciliation import PostgresReconciliationRepository
from fatty_trader.storage.schema import apply_initial_schema


@pytest.fixture
def postgres_schema() -> tuple[str, str]:
    dsn = os.environ.get("FATTY_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set FATTY_TEST_POSTGRES_DSN to run real PostgreSQL E2E proofs")
    schema = f"fatty_e2e_{uuid4().hex}"
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')
    try:
        yield dsn, schema
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def _connect(dsn: str, schema: str):
    return psycopg.connect(dsn, options=f"-c search_path={schema}")


def _migrate(dsn: str, schema: str) -> list[int]:
    with _connect(dsn, schema) as connection:
        with connection.cursor() as cursor:
            apply_initial_schema(cursor)
            applied = apply_migrations(cursor)
        connection.commit()
    return applied


def test_postgres_migrations_are_replay_safe_and_preserve_written_data(
    postgres_schema: tuple[str, str],
) -> None:
    dsn, schema = postgres_schema

    first = _migrate(dsn, schema)
    assert first == [version for version, _ in MIGRATIONS]
    with _connect(dsn, schema) as connection:
        connection.execute(
            "INSERT INTO venue_kill_switches (scope, active, reason) VALUES (%s, %s, %s)",
            ("migration-e2e", True, "must-survive-replay"),
        )
        connection.commit()

    with _connect(dsn, schema) as connection:
        with connection.cursor() as cursor:
            second = apply_migrations(cursor)
        connection.commit()
        row = connection.execute(
            "SELECT active, reason FROM venue_kill_switches WHERE scope = %s", ("migration-e2e",)
        ).fetchone()
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()

    assert second == []
    assert row == (True, "must-survive-replay")
    assert versions == [(version,) for version, _ in MIGRATIONS]


def test_postgres_same_exchange_concurrent_admission_allows_one_then_release_allows_next(
    postgres_schema: tuple[str, str],
) -> None:
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    dispatch_ids = [uuid4() for _ in range(3)]
    with _connect(dsn, schema) as connection:
        for dispatch_id in dispatch_ids:
            connection.execute(
                """INSERT INTO dispatches (id, source_type, source_id, revision, exchange, state)
                VALUES (%s, 'e2e', %s, %s, 'bitget', 'QUEUED')""",
                (dispatch_id, uuid4(), "a" * 64),
            )
        connection.commit()

    def reserve(index: int):
        repository = PostgresBitgetMarginReservationRepository(lambda: _connect(dsn, schema))
        return repository.reserve(
            exchange="bitget",
            dispatch_id=dispatch_ids[index],
            client_order_id=f"e2e-bitget-{index}",
            total_balance=Decimal("100"),
            available_balance=Decimal("100"),
            equity=Decimal("100"),
            margin_coin="USDT",
            observed_at=datetime.now(UTC),
            planned_margin_usdt=Decimal("30"),
            headroom=Decimal("0.5"),
            ttl=timedelta(minutes=5),
            # Test cap above the planned margin: this proof is about serialized
            # admission, not the LIVE 1 USDT ceiling.
            max_margin_per_trade_usdt=Decimal("100"),
        )

    start = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [
            workers.submit(lambda index=index: (start.wait(), reserve(index))[1])
            for index in range(2)
        ]
        concurrent = [future.result(timeout=10) for future in futures]

    accepted = [admission for admission in concurrent if admission.accepted]
    rejected = [admission for admission in concurrent if not admission.accepted]
    assert len(accepted) == 1
    assert len(rejected) == 1
    assert rejected[0].reason == "insufficient-reserved-headroom"
    assert accepted[0].reservation_id is not None
    with _connect(dsn, schema) as connection:
        active_count = connection.execute(
            "SELECT COUNT(*) FROM bitget_margin_reservations WHERE state = 'reserved'"
        ).fetchone()
    assert active_count == (1,)

    PostgresBitgetMarginReservationRepository(lambda: _connect(dsn, schema)).resolve(
        accepted[0].reservation_id, "REJECTED"
    )
    later = reserve(2)

    assert later.accepted is True
    with _connect(dsn, schema) as connection:
        states = connection.execute(
            "SELECT state FROM bitget_margin_reservations ORDER BY client_order_id"
        ).fetchall()
    assert sorted(state for (state,) in states) == ["released", "reserved"]


def _insert_post_fill_mismatch(dsn: str, schema: str, created_at_expression: str) -> None:
    """Insert a persisted mismatch whose age relative to a release is deterministic."""
    with _connect(dsn, schema) as connection:
        connection.execute(
            f"""INSERT INTO bitget_post_fill_reconciliations
                (id, exchange, client_order_id, planned_leverage, planned_margin_usdt,
                 status, observed_at, created_at)
                VALUES (%s, 'bitget', %s, 20, 1, 'mismatch', CURRENT_TIMESTAMP,
                        {created_at_expression})""",
            (uuid4(), f"e2e-{uuid4()}"),
        )
        connection.commit()


def test_postgres_post_fill_mismatch_latches_only_until_the_next_release(
    postgres_schema: tuple[str, str],
) -> None:
    """A released kill switch must not relatch forever on an already-handled row.

    The monitor latches on ``has_unhandled_post_fill_mismatch``. If that predicate
    counted every historical mismatch instead of only the ones newer than the last
    release, an operator could never clear the switch.
    """
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    repository = PostgresReconciliationRepository(lambda: _connect(dsn, schema))

    assert repository.has_unhandled_post_fill_mismatch("bitget") is False

    _insert_post_fill_mismatch(dsn, schema, "CURRENT_TIMESTAMP - INTERVAL '1 hour'")
    assert repository.has_unhandled_post_fill_mismatch("bitget") is True

    repository.latch_kill_switch("bitget", "post-fill-margin-or-leverage-mismatch")
    assert repository.kill_switch_active("bitget") is True
    repository.release_kill_switch("bitget", "e2e-owner-approved-release")

    # Same row, now older than the release: handled, so it must not latch again.
    assert repository.has_unhandled_post_fill_mismatch("bitget") is False

    _insert_post_fill_mismatch(dsn, schema, "CURRENT_TIMESTAMP + INTERVAL '1 hour'")
    assert repository.has_unhandled_post_fill_mismatch("bitget") is True


def test_postgres_bitget_kill_switch_latch_lands_and_blocks_entry_admission(
    postgres_schema: tuple[str, str],
) -> None:
    """A real Bitget latch must reach ``active = TRUE`` on a migrated schema.

    Migration 16 shipped a CHECK pinning ``scope = 'bitget'`` to ``active = FALSE``.
    That makes the fail-closed latch impossible: ``latch_kill_switch()`` raises
    CheckViolation and the LIVE monitor dies rather than blocking entries. This
    test fails loudly if any migration re-introduces such a constraint.
    """
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    repository = PostgresReconciliationRepository(lambda: _connect(dsn, schema))

    assert repository.kill_switch_active("bitget") is False
    repository.latch_kill_switch("bitget", "e2e-post-fill-mismatch")

    assert repository.kill_switch_active("bitget") is True
    with _connect(dsn, schema) as connection:
        row = connection.execute(
            "SELECT active, reason FROM venue_kill_switches WHERE scope = 'bitget'"
        ).fetchone()
        alert_only = connection.execute(
            """SELECT conname FROM pg_constraint
               WHERE conrelid = 'venue_kill_switches'::regclass
                 AND conname = 'bitget_kill_switch_alert_only'"""
        ).fetchall()

    assert row == (True, "e2e-post-fill-mismatch")
    assert alert_only == []

    repository.release_kill_switch("bitget", "e2e-owner-approved-release")
    assert repository.kill_switch_active("bitget") is False
