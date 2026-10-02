"""Real-PostgreSQL proof that the protection kill-switch latch persists.

The unit suite exercises the latch against an in-memory double. That proves
the *call* happens, but not the thing that actually matters: that the latch
survives into `venue_kill_switches` and is therefore visible to the separate
the dispatcher process, which reads that same table.

Skipped unless FATTY_TEST_POSTGRES_DSN is set, matching the existing
real-PostgreSQL E2E proofs. Each test runs in a throwaway schema.
"""
# ruff: noqa: E402

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest

psycopg = pytest.importorskip("psycopg")

from fatty_trader.exchanges.bitget.websocket import WebSocketConnectionState
from fatty_trader.execution.bitget_protection_watchdog import (
    PROTECTION_STREAM_SCOPE,
    BitgetProtectionWatchdog,
)
from fatty_trader.storage.migrations import apply_migrations
from fatty_trader.storage.protection_capabilities import InMemoryProtectionCapabilityRepository
from fatty_trader.storage.reconciliation import PostgresReconciliationRepository
from fatty_trader.storage.schema import apply_initial_schema

_NOW = datetime(2026, 9, 29, 0, 0, tzinfo=UTC)


@pytest.fixture
def postgres_schema() -> Iterator[tuple[str, str]]:
    dsn = os.environ.get("FATTY_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set FATTY_TEST_POSTGRES_DSN to run real PostgreSQL E2E proofs")
    schema = f"fatty_e2e_{uuid4().hex}"
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')
    try:
        _migrate(dsn, schema)
        yield dsn, schema
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def _connect(dsn: str, schema: str):  # noqa: ANN202
    return psycopg.connect(dsn, options=f"-c search_path={schema}")


def _migrate(dsn: str, schema: str) -> None:
    with _connect(dsn, schema) as connection, connection.cursor() as cursor:
        apply_initial_schema(cursor)
        apply_migrations(cursor)


class _DeadSocket:
    """A socket that never connected — the retired-endpoint condition.

    Mirrors the shape the watchdog actually reads: `state` is a property and
    `check_freshness` is called synchronously. A method/async-def fixture here
    silently degrades to the fail-OPEN branch (`getattr(socket, "state")`
    returns a bound method, not an enum) and the tests would assert nothing.
    """

    state = WebSocketConnectionState.FAILED

    def check_freshness(self, symbol: str) -> bool:
        return False

    async def close(self) -> None:
        return None


def _watchdog(kill_switch: object) -> BitgetProtectionWatchdog:
    async def read_position(_: str) -> list[dict[str, str]]:
        return []

    return BitgetProtectionWatchdog(
        _DeadSocket(),
        InMemoryProtectionCapabilityRepository(),
        environment="LIVE",
        symbols=(),  # the steady state: nothing in the fallback table
        read_position=read_position,
        now=lambda: _NOW,
        kill_switch=kill_switch,
        stale_cycles_before_latch=3,
    )


def test_latch_persists_and_is_visible_to_another_process(
    postgres_schema: tuple[str, str],
) -> None:
    """The latch must land in venue_kill_switches, not merely be called.

    `monitor-bitget` and `dispatcher-bitget` are separate containers. An
    in-memory boolean cannot stop entries; only a row in this table can.
    """
    dsn, schema = postgres_schema

    # A second repository instance, standing in for the dispatcher process.
    watchdog_repository = PostgresReconciliationRepository(lambda: _connect(dsn, schema))
    watchdog = _watchdog(watchdog_repository)

    for _ in range(3):
        asyncio.run(watchdog.run_once())

    dispatcher_repository = PostgresReconciliationRepository(lambda: _connect(dsn, schema))
    assert dispatcher_repository.is_active(PROTECTION_STREAM_SCOPE), (
        "the latch must be visible cross-process, otherwise entries are not blocked"
    )


def test_latch_does_not_disarm_venue_wide_fallback_protection(
    postgres_schema: tuple[str, str],
) -> None:
    """Blocking entries must not switch off the fallback stop-loss path.

    Symbols that reject native SL/TP (43011) rely on the fallback monitor.
    A dead *ticker socket* says nothing about that monitor, so it must not
    be allowed to latch the venue-wide switch.
    """
    dsn, schema = postgres_schema

    watchdog_repository = PostgresReconciliationRepository(lambda: _connect(dsn, schema))
    watchdog = _watchdog(watchdog_repository)

    for _ in range(3):
        asyncio.run(watchdog.run_once())

    dispatcher_repository = PostgresReconciliationRepository(lambda: _connect(dsn, schema))
    assert dispatcher_repository.is_active(PROTECTION_STREAM_SCOPE)
    assert not dispatcher_repository.is_active("bitget"), (
        "latching the protection stream must not disarm fallback protection"
    )
