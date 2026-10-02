"""Startup constructor uses real repositories; provider writes are forbidden."""

# ruff: noqa: F401, F811
from decimal import Decimal

import pytest
from test_postgres_migration_and_margin_admission import _connect, _migrate, postgres_schema

from fatty_trader import service
from fatty_trader.exchanges.bitget.live import LiveIntentRecord
from fatty_trader.storage.live_intents import PostgresLiveIntentStore


@pytest.mark.asyncio
async def test_service_graph_recovers_real_postgres_without_entry_replay(
    postgres_schema, monkeypatch
):
    import psycopg

    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    # Capture connect before replacement to avoid recursion.
    real_connect = psycopg.connect

    def scoped_connect(*args, **kwargs):
        return real_connect(dsn, options=f"-c search_path={schema}", **kwargs)

    monkeypatch.setattr(psycopg, "connect", scoped_connect)
    store = PostgresLiveIntentStore(scoped_connect)
    legacy = LiveIntentRecord(
        "bitget", "legacy-orphan", "BTCUSDT", "BUY", requested_qty=Decimal("1")
    )
    store.save(legacy)

    class Provider:
        async def get_all_positions(self):
            return []

        async def get_pending_orders(self):
            return []

        def __getattr__(self, name):
            raise AssertionError(f"no provider requests for empty owned recovery: {name}")

    runtime = service.build_bitget_execution_runtime(
        dict(
            TRADER_MODE="DEMO",
            BITGET_MODE="DEMO",
            BITGET_EXECUTION_ENABLED="1",
            BITGET_API_KEY="fake",
            BITGET_API_SECRET="fake",
            BITGET_API_PASSPHRASE="fake",
            BITGET_CANARY_MAX_ORDERS="1",
            BITGET_CANARY_SYMBOL="BTCUSDT",
            BITGET_APPROVAL_REFERENCE="offline",
            BITGET_MAX_CLOCK_SKEW_MS="5000",
            BITGET_MAX_MARGIN_PER_TRADE_USDT="1",
        ),
        client_factory=lambda *args, **kwargs: Provider(),
        intent_store_factory=lambda: store,
    )
    assert runtime is not None
    assert runtime.execution.recovery_ready is False
    assert await runtime.execution.recover_entry_lifecycles() == 0
    assert runtime.execution.recovery_ready is False
    assert "orphan-entry-intent" in runtime.execution.recovery_issues
    # Durable candidate verdicts alone must not admit an orphan inventory.
    assert store.get("legacy-orphan") is not None
    assert runtime.execution._dispatch_repository.recovery_candidates() == []
