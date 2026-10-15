"""Operator-to-monitor close lifecycle against the isolated PostgreSQL lane."""

from decimal import Decimal

import pytest
from test_postgres_migration_and_margin_admission import _connect, _migrate
from test_postgres_migration_and_margin_admission import postgres_schema as postgres_schema
from test_verified_close_release import owned_close

from fatty_trader.execution.bitget_monitor import BitgetMonitor
from fatty_trader.operator.bitget_gateway import BitgetOperatorGateway
from fatty_trader.storage.live_intents import PostgresLiveIntentStore
from fatty_trader.storage.reconciliation import InMemoryReconciliationRepository


class Client:
    environment = "DEMO"

    def __init__(self):
        self.positions = [
            {"symbol": "BTCUSDT", "holdSide": "long", "total": "1", "openPriceAvg": "100"}
        ]
        self.fills = []
        self.posts = 0

    async def get_all_positions(self):
        return self.positions

    async def place_market_close(self, **kwargs):
        self.posts += 1
        self.positions = []
        self.fills = [
            dict(
                clientOid=kwargs["client_oid"],
                orderId="operator-order",
                fillId="operator-fill",
                symbol="BTCUSDT",
                side="sell",
                tradeSide="close",
                quantity="1",
                price="101",
                fee=".1",
                realizedPnl="2",
            )
        ]
        return {"orderId": "operator-order"}

    async def get_fills(self, symbol=None):
        return self.fills

    async def get_pending_orders(self):
        return []

    async def get_clock_skew_ms(self):
        return 0


def setup_owner(postgres_schema):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    repository, admission, evidence = owned_close(dsn, schema)
    # This lifecycle fixture starts with unrelated historical close evidence,
    # not an already bound exit that consumed the entire owner's entry.
    with _connect(dsn, schema) as connection:
        connection.execute(
            "DELETE FROM bitget_verified_close_bindings WHERE close_client_order_id=%s",
            (evidence["close_client_order_id"],),
        )
    # The fixture's separate historical close must never be selected by symbol.
    store = PostgresLiveIntentStore(lambda: _connect(dsn, schema))
    return dsn, schema, repository, admission, evidence, store


def test_upgrade_adds_binding_without_admitting_historical_closes(postgres_schema, monkeypatch):
    import fatty_trader.storage.migrations as migrations

    dsn, schema = postgres_schema
    all_migrations = migrations.MIGRATIONS
    monkeypatch.setattr(
        migrations, "MIGRATIONS", [(v, sql) for v, sql in all_migrations if v <= 22]
    )
    _migrate(dsn, schema)
    with _connect(dsn, schema) as c:
        c.execute(
            """INSERT INTO live_order_intents
            (id,client_order_id,exchange,symbol,side,role,state,requested_qty,filled_qty)
            VALUES (gen_random_uuid(),'historical-close','bitget','BTCUSDT',\
'SELL','CLOSE','filled',1,1)"""
        )
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations)
    _migrate(dsn, schema)
    assert _migrate(dsn, schema) == []
    with _connect(dsn, schema) as c:
        assert c.execute("SELECT count(*) FROM bitget_verified_close_bindings").fetchone()[0] == 0
        assert (
            c.execute(
                "SELECT state FROM live_order_intents WHERE client_order_id='historical-close'"
            ).fetchone()[0]
            == "filled"
        )
        with pytest.raises(Exception, match="check constraint"):
            c.execute(
                "UPDATE live_order_intents SET margin_reservation_id=gen_random_uuid() "
                "WHERE client_order_id='historical-close'"
            )
        c.rollback()


def test_unbound_authenticated_close_cannot_release_an_owner(postgres_schema):
    dsn, schema, repository, admission, evidence, store = setup_owner(postgres_schema)
    with _connect(dsn, schema) as c:
        c.execute("DELETE FROM bitget_verified_close_bindings")
    assert not repository.release_verified_close(**evidence).accepted
    with _connect(dsn, schema) as c:
        assert c.execute("SELECT state FROM bitget_margin_reservations").fetchone() == ("consumed",)


def test_position_read_happens_after_durable_fill_reconciliation(postgres_schema):
    dsn, schema, repository, admission, evidence, store = setup_owner(postgres_schema)
    client = Client()
    BitgetOperatorGateway(
        client, store, client_oid_factory=lambda: "operator-new-close"
    ).close_position("BTCUSDT")
    original = store.update

    def update(record):
        original(record)
        client.positions = [{"symbol": "BTCUSDT", "holdSide": "short", "total": "1"}]

    store.update = update
    import asyncio

    monitor = BitgetMonitor(
        client,
        InMemoryReconciliationRepository(),
        live_intent_store=store,
        enforce_kill_switch=False,
    )
    asyncio.run(monitor.run_once())
    with _connect(dsn, schema) as c:
        assert c.execute("SELECT state FROM bitget_margin_reservations").fetchone() == ("consumed",)


@pytest.mark.parametrize(
    "fault",
    [
        "missing-fill",
        "partial",
        "wrong-oid",
        "wrong-order",
        "wrong-symbol",
        "wrong-side",
        "wrong-trade-side",
        "synthetic",
        "missing-fee",
        "missing-pnl",
        "duplicate",
        "nan-quantity",
        "nan-fee",
        "stale-fill",
        "open-long",
        "open-short",
        "malformed-positions",
        "malformed-row",
        "negative-position",
        "nan-position",
        "provider-failure",
        "environment-mismatch",
        "unknown-without-fills",
        "rejected",
        "cancelled",
        "missing-binding",
    ],
)
def test_uncertain_close_never_releases(postgres_schema, fault):
    dsn, schema, repository, admission, evidence, store = setup_owner(postgres_schema)
    client = Client()
    BitgetOperatorGateway(
        client, store, client_oid_factory=lambda: "operator-new-close"
    ).close_position("BTCUSDT")
    f = client.fills[0]
    fields = {
        "partial": ("quantity", ".5"),
        "wrong-oid": ("clientOid", "old-close"),
        "wrong-order": ("orderId", "other"),
        "wrong-symbol": ("symbol", "ETHUSDT"),
        "wrong-side": ("side", "buy"),
        "wrong-trade-side": ("tradeSide", "open"),
        "synthetic": ("fillId", "status-derived:operator-new-close"),
        "nan-quantity": ("quantity", "NaN"),
        "nan-fee": ("fee", "NaN"),
        "stale-fill": ("cTime", "1"),
    }
    if fault in fields:
        k, v = fields[fault]
        f[k] = v
    elif fault in {"missing-fill", "unknown-without-fills"}:
        client.fills = []
    elif fault == "missing-fee":
        del f["fee"]
    elif fault == "missing-pnl":
        del f["realizedPnl"]
    elif fault == "duplicate":
        client.fills.append(dict(f))
    elif fault.startswith("open-"):
        client.positions = [{"symbol": "BTCUSDT", "holdSide": fault[5:], "total": "1"}]
    elif fault == "malformed-positions":
        client.positions = {"positions": []}
    elif fault == "malformed-row":
        client.positions = [{"symbol": "BTCUSDT"}]
    elif fault in {"negative-position", "nan-position"}:
        client.positions = [
            {
                "symbol": "BTCUSDT",
                "holdSide": "long",
                "total": "-1" if fault == "negative-position" else "NaN",
            }
        ]
    elif fault == "provider-failure":

        async def unavailable(*args, **kwargs):
            raise RuntimeError("offline provider failure")

        client.get_fills = unavailable
    elif fault == "environment-mismatch":
        client.environment = "LIVE"  # label mismatch only; never a LIVE provider call
    with _connect(dsn, schema) as c:
        if fault in {"unknown-without-fills", "rejected", "cancelled"}:
            state = "unknown" if fault == "unknown-without-fills" else fault
            c.execute(
                "UPDATE live_order_intents SET state=%s WHERE client_order_id='operator-new-close'",
                (state,),
            )
        elif fault == "missing-binding":
            c.execute("DELETE FROM bitget_verified_close_bindings")
    import asyncio

    asyncio.run(
        BitgetMonitor(
            client,
            InMemoryReconciliationRepository(),
            live_intent_store=store,
            enforce_kill_switch=False,
        ).run_once()
    )
    with _connect(dsn, schema) as c:
        assert c.execute("SELECT state FROM bitget_margin_reservations").fetchone() == ("consumed",)
    assert client.posts == 1


@pytest.mark.parametrize("native", [False, True])
def test_operator_close_releases_only_after_monitor_verifies_real_fills(postgres_schema, native):
    dsn, schema, repository, admission, evidence, store = setup_owner(postgres_schema)
    client = Client()
    gateway = BitgetOperatorGateway(client, store, client_oid_factory=lambda: "operator-new-close")
    gateway.close_position("BTCUSDT")
    if native:
        f = client.fills[0]
        f["tradeId"] = f.pop("fillId")
        f["baseVolume"] = f.pop("quantity")
        f["profit"] = f.pop("realizedPnl")
        f["feeDetail"] = [{"feeCoin": "USDT", "totalFee": "-.1"}]
        del f["fee"]
        client.fills = {"fillList": [f], "endId": "operator-fill"}
    with _connect(dsn, schema) as c:
        assert c.execute("SELECT state FROM bitget_margin_reservations").fetchone() == ("consumed",)
    monitor = BitgetMonitor(
        client,
        InMemoryReconciliationRepository(),
        live_intent_store=store,
        enforce_kill_switch=False,
    )
    import asyncio

    asyncio.run(monitor.run_once())
    with _connect(dsn, schema) as c:
        assert c.execute(
            "SELECT state,resolution_reason FROM bitget_margin_reservations"
        ).fetchone() == ("released", "verified-close:operator-new-close")
        assert c.execute(
            "SELECT quantity,fee,realized_pnl FROM fills WHERE provider_fill_id='operator-fill'"
        ).fetchone() == (Decimal(1), Decimal(".1"), Decimal(2))
    assert client.posts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["candidates", "get", "update", "release"])
async def test_close_reconciliation_storage_keeps_shared_clock_responsive(
    postgres_schema, monkeypatch, operation
):
    import asyncio
    import threading
    import time

    from fatty_trader.exchanges.bitget.client import BitgetRestClient

    dsn, schema, _, _, _, store = setup_owner(postgres_schema)
    client = Client()
    gateway = BitgetOperatorGateway(client, store, client_oid_factory=lambda: "operator-new-close")
    await asyncio.to_thread(gateway.close_position, "BTCUSDT")
    lifecycle = store.verified_close_lifecycle
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    entered = asyncio.Event()
    release = threading.Event()
    request_started = asyncio.Event()
    respond = asyncio.Event()
    if operation == "candidates":
        target, method = lifecycle, "_read_close_candidates"
    elif operation == "release":
        target, method = lifecycle._reservations, "release_verified_close"
    else:
        target, method = store, operation
    original = getattr(target, method)

    def block_store(*args, **kwargs):
        assert threading.get_ident() != loop_thread, "storage must run outside the event loop"
        loop.call_soon_threadsafe(entered.set)
        release.wait()
        return original(*args, **kwargs)

    monkeypatch.setattr(target, method, block_store)

    async def time_response(*args, **kwargs):
        request_started.set()
        await respond.wait()
        return {"serverTime": int(time.time() * 1000)}

    clock_client = BitgetRestClient("", "", "")
    monkeypatch.setattr(clock_client, "_request", time_response)
    clock_task = asyncio.create_task(clock_client.get_clock_skew_ms())
    reconciliation_task = None
    entered_task = None
    try:
        await asyncio.wait_for(request_started.wait(), timeout=1)
        reconciliation_task = asyncio.create_task(lifecycle.reconcile(client))
        entered_task = asyncio.create_task(entered.wait())
        done, _ = await asyncio.wait(
            (entered_task, reconciliation_task), return_when=asyncio.FIRST_COMPLETED
        )
        if reconciliation_task in done:
            await reconciliation_task
        assert entered.is_set(), "reconciliation must reach the selected storage operation"
        respond.set()
        await asyncio.wait_for(clock_task, timeout=1)
        assert not reconciliation_task.done(), "storage cannot starve clock/socket I/O"
    finally:
        release.set()
        respond.set()
        if entered_task is not None:
            entered_task.cancel()
        await asyncio.gather(
            clock_task,
            *([reconciliation_task] if reconciliation_task else []),
            *([entered_task] if entered_task else []),
            return_exceptions=True,
        )
        await clock_client.aclose()
    with _connect(dsn, schema) as c:
        assert c.execute("SELECT state FROM bitget_margin_reservations").fetchone() == ("released",)
    assert client.posts == 1


@pytest.mark.asyncio
async def test_close_candidate_query_owns_connection_and_preserves_sql_failure():
    import threading

    from fatty_trader.storage.verified_closes import PostgresVerifiedCloseLifecycle

    loop_thread = threading.get_ident()
    events = []
    failure = RuntimeError("query failure")

    class Connection:
        def __init__(self):
            assert threading.get_ident() != loop_thread
            self.owner = threading.get_ident()
            events.append("connected")

        def cursor(self):
            assert threading.get_ident() == self.owner
            return self

        def execute(self, statement, params):
            assert threading.get_ident() == self.owner
            events.append("query")
            raise failure

        def close(self):
            assert threading.get_ident() == self.owner
            events.append("closed")

    lifecycle = PostgresVerifiedCloseLifecycle(Connection, None)
    with pytest.raises(RuntimeError) as raised:
        await lifecycle.reconcile(Client())
    assert raised.value is failure
    assert events == ["connected", "query", "closed"]


@pytest.mark.asyncio
async def test_cancelled_close_query_drains_before_closing_connection():
    import asyncio
    import threading

    from fatty_trader.storage.verified_closes import PostgresVerifiedCloseLifecycle

    entered = threading.Event()
    release = threading.Event()
    events = []

    class Connection:
        def cursor(self):
            return self

        def execute(self, statement, params):
            events.append("query")
            entered.set()
            release.wait(timeout=2)

        def fetchall(self):
            events.append("read")
            return []

        def close(self):
            events.append("closed")

    lifecycle = PostgresVerifiedCloseLifecycle(Connection, None)
    operation = asyncio.create_task(lifecycle.reconcile(Client()))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        operation.cancel()
        await asyncio.sleep(0)
        operation.cancel()
        await asyncio.sleep(0)
        assert not operation.done()
        assert events == ["query"]
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert events == ["query", "read", "closed"]
    finally:
        release.set()
        await asyncio.gather(operation, return_exceptions=True)
