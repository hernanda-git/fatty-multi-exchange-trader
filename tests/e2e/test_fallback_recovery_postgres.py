"""Disposable PostgreSQL proofs; no authenticated provider clients are constructed."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import pytest

from fatty_trader.execution import bitget_fallback_protection as fallback
from fatty_trader.storage.migrations import apply_migrations
from fatty_trader.storage.schema import apply_initial_schema


@pytest.fixture
def database(monkeypatch):
    import psycopg

    dsn = os.environ.get("FATTY_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("requires disposable FATTY_TEST_POSTGRES_DSN")
    schema = f"fatty_e2e_fallback_{uuid4().hex}"
    with psycopg.connect(dsn, autocommit=True) as conn:
        db, user, _ = conn.execute(
            "SELECT current_database(), current_user, inet_server_addr()"
        ).fetchone()
        assert (db, user) == ("fatty_test", "fatty_test")
        conn.execute(f'CREATE SCHEMA "{schema}"')

    def connect():
        return psycopg.connect(dsn, options=f"-c search_path={schema}")

    monkeypatch.setattr(fallback, "_psycopg_connect", connect)
    try:
        with connect() as conn:
            apply_initial_schema(conn.cursor())
            apply_migrations(conn.cursor())
            conn.execute(fallback._SCHEMA_SQL)
        yield connect
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.mark.asyncio
async def test_real_adapter_registration_durable_roundtrip_and_replacement_refusal(database):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
    from test_fallback_epoch_adapter import _protection_plan, fixture_client

    from fatty_trader.exchanges.bitget.async_execution import AsyncBitgetExecution
    from fatty_trader.exchanges.bitget.async_venue import AsyncBitgetVenue
    from fatty_trader.execution.protection import ProtectionState

    client, store, intent, requests = fixture_client()
    try:
        with database() as conn:
            conn.execute(
                "INSERT INTO live_order_intents "
                "(id, exchange, client_order_id, symbol, side, role, state, "
                "requested_qty, filled_qty, provider_order_id) "
                "VALUES (%s, 'bitget', %s, 'BTCUSDT', 'BUY', 'ENTRY', 'filled', 0.001, 0.001, %s)",
                (str(uuid4()), intent.client_oid, intent.provider_order_id),
            )
        adapter = AsyncBitgetExecution(
            client, AsyncBitgetVenue(client), environment="DEMO", fallback_protection_enabled=True
        )
        result = await adapter.protect_filled_position(intent, _protection_plan(), store)
        assert result.state is ProtectionState.DEGRADED
        assert result.reason == "native-protection-unsupported-fallback-registered-not-enforcing"
        entry = fallback.load_active()[0]
        assert entry["provider_position_epoch"] == "1000"
        assert entry["environment"] == "DEMO"
        assert entry["position_key"] == intent.client_oid
        replacement, _, _, replacement_requests = fixture_client(epoch="2000", fill_epoch="2000")
        try:
            await fallback.submit_fallback_close_async(
                replacement, entry, mark_price=Decimal("40000"), reason="sl_hit", environment="DEMO"
            )
            assert not any(r.url.path.endswith("place-order") for r in replacement_requests)
            with database() as conn:
                assert conn.execute(
                    "SELECT count(*) FROM live_order_intents WHERE role='CLOSE'"
                ).fetchone() == (0,)
        finally:
            await replacement.aclose()
    finally:
        await client.aclose()


def test_registration_round_trips_canonical_identity(database):
    fallback.register_fallback(
        "bitget",
        "BTCUSDT",
        "LONG",
        Decimal("100"),
        Decimal("95"),
        [Decimal("110")],
        Decimal("0.01"),
        "entry-owner",
        provider_position_epoch="1000",
        environment="DEMO",
    )
    entry = fallback.load_active()[0]
    assert entry["provider_position_epoch"] == "1000"
    assert entry["environment"] == "DEMO"
    with database() as conn:
        assert conn.execute(
            "SELECT version FROM schema_migrations WHERE version=22"
        ).fetchone() == (22,)
        assert apply_migrations(conn.cursor()) == []
        conn.execute(
            (
                "INSERT INTO live_order_intents\n"
                "            (id, exchange, client_order_id, symbol, side, role, "
                "state, requested_qty, filled_qty)\n"
                "            VALUES (%s, 'bitget', 'entry-owner', 'BTCUSDT', 'BUY', "
                "'ENTRY', 'filled', 0.01, 0.01)"
            ),
            (str(uuid4()),),
        )
    position = {
        "symbol": "BTCUSDT",
        "holdSide": "long",
        "cTime": "1000",
        "total": "0.01",
        "posMode": "one_way_mode",
        "marginMode": "isolated",
    }
    assert fallback._owned_quantity([position], entry) == Decimal("0.01")
    assert fallback._owned_quantity([{**position, "cTime": "2000"}], entry) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "environment,epoch", [("DEMO", "1000"), ("LIVE", "1000"), ("DEMO", "2000"), (None, "1000")]
)
async def test_loaded_identity_close_requires_exact_environment_and_epoch(
    database, environment, epoch
):
    fallback.register_fallback(
        "bitget",
        "BTCUSDT",
        "LONG",
        Decimal("100"),
        Decimal("95"),
        [],
        Decimal("0.01"),
        "entry-owner",
        provider_position_epoch="1000",
        environment="DEMO",
    )
    with database() as conn:
        conn.execute(
            (
                "INSERT INTO live_order_intents\n"
                "            (id, exchange, client_order_id, symbol, side, role, "
                "state, requested_qty, filled_qty)\n"
                "            VALUES (%s, 'bitget', 'entry-owner', 'BTCUSDT', 'BUY', "
                "'ENTRY', 'filled', 0.01, 0.01)"
            ),
            (str(uuid4()),),
        )
    entry = fallback.load_active()[0]

    class Client:
        calls = 0

        async def get_single_position(self, symbol):
            return [
                {
                    "symbol": "BTCUSDT",
                    "holdSide": "long",
                    "cTime": epoch,
                    "total": "0.01",
                    "posMode": "one_way_mode",
                    "marginMode": "isolated",
                }
            ]

        async def place_market_close(self, **kwargs):
            with database() as conn:
                assert conn.execute("SELECT state FROM fallback_protection").fetchone() == (
                    "closing",
                )
                assert conn.execute(
                    "SELECT state FROM live_order_intents WHERE role='CLOSE'"
                ).fetchone() == ("requested",)
            self.calls += 1
            raise TimeoutError("ambiguous POST")

        async def get_fills(self, symbol):
            return []

    client = Client()
    result = await fallback.submit_fallback_close_async(
        client, entry, mark_price=Decimal("90"), reason="sl_hit", environment=environment
    )
    matching = environment == "DEMO" and epoch == "1000"
    assert client.calls == int(matching)
    if matching:
        assert result["reason"] == "close-result-unknown"
        restarted = fallback.load_active()[0]
        await fallback.submit_fallback_close_async(
            client, restarted, mark_price=Decimal("90"), reason="sl_hit", environment="DEMO"
        )
        assert client.calls == 1
    else:
        with database() as conn:
            assert conn.execute(
                "SELECT count(*) FROM live_order_intents WHERE role='CLOSE'"
            ).fetchone() == (0,)


def test_migration22_upgrades_legacy_rows_without_inventing_identity(database):
    from fatty_trader.storage.migrations import apply_migrations

    psycopg = pytest.importorskip("psycopg")
    identity = seed(database)
    with database() as conn:
        conn.execute("ALTER TABLE fallback_protection DROP COLUMN provider_position_epoch")
        conn.execute("ALTER TABLE fallback_protection DROP COLUMN environment")
        conn.execute("DELETE FROM schema_migrations WHERE version=22")
        with conn.cursor() as cur:
            assert apply_migrations(cur) == [22]
            assert apply_migrations(cur) == []
        assert conn.execute(
            (
                "SELECT position_key, provider_position_epoch, environment FROM "
                "fallback_protection WHERE id=%s"
            ),
            (identity,),
        ).fetchone() == ("entry-owner", None, None)
    entry = fallback.load_active()[0]
    assert (
        fallback._owned_quantity(
            [{"symbol": "BTCUSDT", "holdSide": "long", "cTime": "1000", "total": "0.01"}], entry
        )
        is None
    )
    for column, value in (
        ("environment", "demo"),
        ("provider_position_epoch", "0"),
        ("provider_position_epoch", "1e3"),
    ):
        with pytest.raises(psycopg.errors.CheckViolation), database() as conn:
            conn.execute(
                f"UPDATE fallback_protection SET {column}=%s WHERE id=%s", (value, identity)
            )


def test_duplicate_registration_cannot_silently_replace_identity(database):
    args = (
        "bitget",
        "BTCUSDT",
        "LONG",
        Decimal("100"),
        Decimal("95"),
        [],
        Decimal("0.01"),
        "entry-owner",
    )
    fallback.register_fallback(*args, provider_position_epoch="1000", environment="DEMO")
    fallback.register_fallback(*args, provider_position_epoch="1000", environment="DEMO")
    for change in (
        {"provider_position_epoch": "2000", "environment": "DEMO"},
        {"provider_position_epoch": "1000", "environment": "LIVE"},
    ):
        with pytest.raises(ValueError, match="conflict"):
            fallback.register_fallback(*args, **change)
    assert len(fallback.load_active()) == 1
    assert fallback.load_active()[0]["provider_position_epoch"] == "1000"


@pytest.mark.parametrize(
    "epoch,environment,key",
    [
        ("0", "DEMO", "owner"),
        (" 1000", "DEMO", "owner"),
        ("1e3", "DEMO", "owner"),
        ("01000", "DEMO", "owner"),
        ("١٠٠٠", "DEMO", "owner"),
        ("1000", "demo", "owner"),
        ("1000", None, "owner"),
        (None, "DEMO", "owner"),
        ("1000", "DEMO", None),
    ],
)
def test_registration_refuses_partial_or_noncanonical_identity(database, epoch, environment, key):
    with pytest.raises(ValueError, match="identity"):
        fallback.register_fallback(
            "bitget",
            "BTCUSDT",
            "LONG",
            Decimal("100"),
            Decimal("95"),
            [],
            Decimal("0.01"),
            key,
            provider_position_epoch=epoch,
            environment=environment,
        )
    assert fallback.load_active() == []


def seed(connect):
    identity = str(uuid4())
    with connect() as conn:
        conn.execute(
            """INSERT INTO fallback_protection
            (id, exchange, symbol, direction, entry_price, stop_loss, quantity, position_key)
            VALUES (%s, 'bitget', 'BTCUSDT', 'LONG', 100, 95, 0.01, 'entry-owner')""",
            (identity,),
        )
    return identity


def test_atomic_claim_updates_lifecycle_with_one_concurrent_winner(database):
    identity = seed(database)
    oid = fallback._fallback_client_oid(identity)
    barrier = Barrier(2)

    def claim():
        barrier.wait()
        return fallback._ensure_close_intent(oid, "BTCUSDT", "SELL", Decimal("0.01"))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: claim(), range(2)))
    assert sorted(results) == [False, True]
    with database() as conn:
        assert conn.execute(
            "SELECT state, close_order_id FROM fallback_protection WHERE id=%s", (identity,)
        ).fetchone() == ("closing", oid)
        assert conn.execute(
            "SELECT count(*) FROM live_order_intents WHERE client_order_id=%s", (oid,)
        ).fetchone() == (1,)


@pytest.mark.asyncio
async def test_restart_partial_then_full_matched_fills_persists_real_accounting(database):
    identity = seed(database)
    oid = fallback._fallback_client_oid(identity)
    assert fallback._ensure_close_intent(oid, "BTCUSDT", "SELL", Decimal("0.01"))
    fallback._update_close_intent(oid, "unknown", "provider-close")
    entry = fallback.load_active()[0]

    class ReadOnlyEvidenceClient:
        fills = [
            {
                "tradeId": "fill-one",
                "clientOid": oid,
                "orderId": "provider-close",
                "symbol": "BTCUSDT",
                "side": "sell",
                "tradeSide": "close",
                "baseVolume": "0.004",
                "price": "93",
                "fee": "-0.02",
                "cTime": "1000",
            }
        ]

        async def get_fills(self, symbol):
            return {"fillList": self.fills}

    client = ReadOnlyEvidenceClient()
    assert await fallback._reconcile_close_evidence(client, entry, flat=False) is False
    with database() as conn:
        assert conn.execute(
            (
                "SELECT state, filled_qty, filled_price, fee FROM live_order_intents "
                "WHERE client_order_id=%s"
            ),
            (oid,),
        ).fetchone() == ("partially_filled", Decimal("0.004"), Decimal("93"), Decimal("0.02"))
        assert conn.execute("SELECT state, close_price FROM fallback_protection").fetchone() == (
            "closing",
            None,
        )
    # Restart with only the later page; durable ledger must retain the first fill.
    entry = fallback.load_active()[0]
    client.fills = [
        {
            "tradeId": "fill-two",
            "clientOid": oid,
            "orderId": "provider-close",
            "symbol": "BTCUSDT",
            "side": "sell",
            "tradeSide": "close",
            "baseVolume": "0.006",
            "price": "92",
            "fee": "-0.03",
            "cTime": "2000",
        }
    ]
    assert await fallback._reconcile_close_evidence(client, entry, flat=True) is True
    assert await fallback._reconcile_close_evidence(client, entry, flat=True) is True
    with database() as conn:
        assert conn.execute(
            (
                "SELECT state, filled_qty, filled_price, fee, provider_fill_ids FROM "
                "live_order_intents WHERE client_order_id=%s"
            ),
            (oid,),
        ).fetchone() == (
            "filled",
            Decimal("0.01"),
            Decimal("92.4"),
            Decimal("0.05"),
            ["fill-one", "fill-two"],
        )
        assert conn.execute("SELECT state, close_price FROM fallback_protection").fetchone() == (
            "triggered",
            Decimal("92.4"),
        )
        assert conn.execute("SELECT count(*), sum(quantity), sum(fee) FROM fills").fetchone() == (
            2,
            Decimal("0.01"),
            Decimal("0.05"),
        )


@pytest.mark.asyncio
async def test_monitor_restart_unknown_and_unsubmitted_claim_are_get_only(database):
    identity = seed(database)
    with database() as conn:
        conn.execute("UPDATE fallback_protection SET environment='DEMO' WHERE id=%s", (identity,))
    oid = fallback._fallback_client_oid(identity)
    assert fallback._ensure_close_intent(oid, "BTCUSDT", "SELL", Decimal("0.01"))

    class EvidenceOnlyClient:
        fills = []
        positions = [{"total": "0.01"}]
        reads = []

        async def get_single_position(self, symbol):
            self.reads.append("position")
            return self.positions

        async def get_fills(self, symbol):
            self.reads.append("fills")
            return {"fillList": self.fills}

        async def place_market_close(self, **kwargs):
            pytest.fail("restart must never repeat an ambiguous POST")

    client = EvidenceOnlyClient()
    assert await fallback.run_fallback_monitor_async(client, environment="DEMO") == []
    assert client.reads == ["position", "fills"]
    with database() as conn:
        assert conn.execute("SELECT state FROM live_order_intents").fetchone() == ("requested",)
        assert conn.execute("SELECT state FROM fallback_protection").fetchone() == ("closing",)
    fallback._update_close_intent(oid, "unknown", "provider-close")
    client.positions = []
    client.fills = [
        {
            "tradeId": "actual-fill",
            "clientOid": oid,
            "orderId": "provider-close",
            "symbol": "BTCUSDT",
            "side": "sell",
            "tradeSide": "close",
            "baseVolume": "0.01",
            "price": "93",
            "fee": "-0.02",
            "cTime": "1000",
        }
    ]
    assert await fallback.run_fallback_monitor_async(client, environment="DEMO") == []
    with database() as conn:
        assert conn.execute("SELECT state, filled_price FROM live_order_intents").fetchone() == (
            "filled",
            Decimal("93"),
        )
        assert conn.execute("SELECT state, close_price FROM fallback_protection").fetchone() == (
            "triggered",
            Decimal("93"),
        )
        assert conn.execute("SELECT count(*) FROM fills").fetchone() == (1,)
    assert await fallback.run_fallback_monitor_async(client, environment="DEMO") == []
    assert client.reads == ["position", "fills", "position", "fills"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"clientOid": "unrelated-entry"},
        {"orderId": "other-close"},
        {"symbol": "PUMPUSDT"},
        {"side": "buy"},
        {"tradeSide": "open"},
        {"tradeId": "status-derived:fake"},
        {"tradeId": ""},
        {"price": "NaN"},
        {"baseVolume": "-0.01"},
        {"cTime": None},
        {"fee": "malformed"},
        {"fee": None},
    ],
)
async def test_unrelated_or_malformed_fill_cannot_create_accounting(database, change):
    identity = seed(database)
    oid = fallback._fallback_client_oid(identity)
    assert fallback._ensure_close_intent(oid, "BTCUSDT", "SELL", Decimal("0.01"))
    fallback._update_close_intent(oid, "unknown", "provider-close")
    row = {
        "tradeId": "fill-one",
        "clientOid": oid,
        "orderId": "provider-close",
        "symbol": "BTCUSDT",
        "side": "sell",
        "tradeSide": "close",
        "baseVolume": "0.01",
        "price": "93",
        "fee": "-0.02",
        "cTime": "1000",
    }
    assert (
        fallback._persist_close_fills(fallback.load_active()[0], [{**row, **change}], flat=True)
        is False
    )
    with database() as conn:
        assert conn.execute("SELECT count(*) FROM fills").fetchone() == (0,)
        assert conn.execute("SELECT state, filled_price FROM live_order_intents").fetchone() == (
            "unknown",
            None,
        )
        assert conn.execute("SELECT state, close_price FROM fallback_protection").fetchone() == (
            "closing",
            None,
        )


@pytest.mark.asyncio
async def test_contradictory_duplicate_fill_rolls_back_accounting(database):
    identity = seed(database)
    oid = fallback._fallback_client_oid(identity)
    assert fallback._ensure_close_intent(oid, "BTCUSDT", "SELL", Decimal("0.01"))
    row = {
        "tradeId": "fill-one",
        "clientOid": oid,
        "orderId": "provider-close",
        "symbol": "BTCUSDT",
        "side": "sell",
        "tradeSide": "close",
        "baseVolume": "0.004",
        "price": "93",
        "fee": "-0.02",
        "cTime": "1000",
    }
    entry = fallback.load_active()[0]
    with pytest.raises(ValueError, match="contradict"):
        fallback._persist_close_fills(entry, [row, {**row, "price": "999"}], flat=False)
    with database() as conn:
        assert conn.execute("SELECT count(*) FROM fills").fetchone() == (0,)
    assert fallback._persist_close_fills(entry, [row], flat=False) is False
    with pytest.raises(ValueError, match="contradict"):
        fallback._persist_close_fills(entry, [{**row, "price": "999"}], flat=True)
    with database() as conn:
        assert conn.execute(
            "SELECT filled_qty, filled_price FROM live_order_intents"
        ).fetchone() == (Decimal("0.004"), Decimal("93"))
        assert conn.execute("SELECT count(*), max(price) FROM fills").fetchone() == (
            1,
            Decimal("93"),
        )


@pytest.mark.parametrize("entry_state", ["filled", "partially_filled"])
def test_owned_position_identity_requires_matching_epoch_and_entry(database, entry_state):
    identity = seed(database)
    with database() as conn:
        conn.execute(
            """INSERT INTO live_order_intents
            (id, exchange, client_order_id, symbol, side, role, state, requested_qty, filled_qty)
            VALUES (%s, 'bitget', 'entry-owner', 'BTCUSDT', 'BUY', 'ENTRY', %s, 0.01, 0.01)""",
            (str(uuid4()), entry_state),
        )
    entry = fallback.load_active()[0]
    assert str(entry["id"]) == identity
    # Explicit fixture identity; registration round-trip is proven separately.
    entry["provider_position_epoch"] = "1000"
    entry["environment"] = "DEMO"
    position = {
        "symbol": "BTCUSDT",
        "holdSide": "long",
        "cTime": "1000",
        "total": "0.01",
        "posMode": "one_way_mode",
        "marginMode": "isolated",
    }
    assert fallback._owned_quantity([position], entry) == Decimal("0.01")
    for change in (
        {"cTime": "2000"},
        {"holdSide": "short"},
        {"symbol": "PUMPUSDT"},
        {"total": "0.02"},
    ):
        assert fallback._owned_quantity([{**position, **change}], entry) is None
    assert fallback._owned_quantity([position], {**entry, "position_key": "old-entry"}) is None
    assert fallback._owned_quantity([position], {**entry, "provider_position_epoch": None}) is None


def test_claim_transaction_rolls_back_intent_if_lifecycle_write_crashes(database):
    identity = seed(database)
    oid = fallback._fallback_client_oid(identity)
    with database() as conn:
        conn.execute("""CREATE FUNCTION fail_closing() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN RAISE EXCEPTION 'injected lifecycle crash'; END $$""")
        conn.execute(
            "CREATE TRIGGER crash_closing BEFORE UPDATE ON fallback_protection FOR "
            "EACH ROW EXECUTE FUNCTION fail_closing()"
        )
    with pytest.raises(Exception, match="injected lifecycle crash"):
        fallback._ensure_close_intent(oid, "BTCUSDT", "SELL", Decimal("0.01"))
    with database() as conn:
        assert conn.execute("SELECT count(*) FROM live_order_intents").fetchone() == (0,)
        assert conn.execute("SELECT state FROM fallback_protection").fetchone() == ("active",)
        conn.execute("DROP TRIGGER crash_closing ON fallback_protection")
    assert fallback._ensure_close_intent(oid, "BTCUSDT", "SELL", Decimal("0.01")) is True
