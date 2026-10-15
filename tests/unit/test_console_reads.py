from __future__ import annotations

import asyncio
import hashlib
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from fatty_trader.console.reads import (
    ReadError,
    ReadService,
    SnapshotCollector,
    _evidence,
    _filters,
    add_owned_residuals,
    configured_scope,
    exact_position_owner,
    native_position_owner,
    provider_rows,
    sanitize,
)

SCOPE = {"account_id": "configured-bitget", "venue": "bitget", "environment": "LIVE"}


class MemoryStore:
    def __init__(self):
        self.snapshots = {}
        self.calls = []
        self.rows = []

    def put_snapshot(self, kind, scope, data, error=None, observed_at=None):
        self.snapshots[kind] = {
            "id": kind,
            "data": data,
            "error": error,
            "observed_at": observed_at,
            "scope": scope,
        }

    def latest_snapshot(self, kind, scope):
        return self.snapshots.get(kind)

    def query(self, sql, params=()):
        self.calls.append((sql, params))
        return self.rows


class GetOnlyClient:
    async def get_all_positions(self):
        return {"data": []}

    async def get_pending_orders(self):
        return []

    async def get_contracts(self):
        return []

    async def get_fills(self):
        return {"fillList": []}

    async def get_account(self, symbol=None):
        return {"available": "12.123456789123456789", "usdtEquity": "15", "unrealizedPL": "-1"}

    async def get_clock_skew_ms(self):
        return 42

    async def _get(self, endpoint):
        assert endpoint == "/api/v2/spot/account/info"
        return {"userId": "123"}

    async def get_pending_plan_orders(self, symbol):
        return []

    async def get_ticker(self, symbol):
        return {"symbol": symbol, "markPrice": "1"}

    async def get_position_lever(self, symbol):
        return [{"keepMarginRate": "0.005", "startUnit": "0", "endUnit": "100000"}]

    def __getattr__(self, name):
        raise AssertionError(f"Mutation or unknown method touched: {name}")


@pytest.mark.parametrize(
    "payload",
    [{"data": {"total": 0}}, {"entrustedList": {}}, {"code": "43011", "data": []}, "bad", [1]],
)
def test_container_errors_never_become_empty_success(payload):
    with pytest.raises((ValueError, ArithmeticError)):
        provider_rows(payload, "orders")


def test_valid_empty_and_partial_fill_containers_are_distinct():
    assert provider_rows({"entrustedList": []}, "orders") == ([], True)
    assert provider_rows({"fillList": [], "endId": "next"}, "fills") == ([], False)
    with pytest.raises(ValueError):
        provider_rows({"entrustedList": None}, "orders")
    with pytest.raises((ValueError, ArithmeticError)):
        provider_rows([{"symbol": "BTCUSDT", "holdSide": "long", "total": "NaN"}], "positions")


def test_unicode_contract_catalog_preserves_ascii_markets_without_adopting_positions():
    catalog = [{"symbol": "BTCUSDT"}, {"symbol": "龙虾USDT"}]
    rows, complete = provider_rows(catalog, "contracts")
    assert rows == catalog
    assert complete is True
    with pytest.raises(ValueError):
        provider_rows([{"symbol": "龙虾USDT", "holdSide": "long", "total": "1"}], "positions")


@pytest.mark.parametrize("symbol", [None, "", "BTC\nUSDT", "<script>USDT"])
def test_invalid_contract_identity_never_publishes_complete_inventory(symbol):
    with pytest.raises(ValueError):
        provider_rows([{"symbol": "BTCUSDT"}, {"symbol": symbol}], "contracts")


def test_collector_is_get_only_and_keeps_sources_independent():
    store = MemoryStore()
    asyncio.run(
        SnapshotCollector(
            {"BITGET_MODE": "LIVE", "BITGET_API_SECRET": "never-store"}, store, GetOnlyClient()
        ).collect_once()
    )
    assert store.snapshots["account"]["data"]["values"]["available"] == "12.123456789123456789"
    assert store.snapshots["positions"]["data"]["items"] == []
    assert store.snapshots["positions"]["observed_at"] is not None
    assert store.snapshots["fills"]["data"]["complete"] is False
    assert store.snapshots["plan_orders"]["data"]["complete"] is False
    assert store.snapshots["clock"]["data"]["values"]["local_minus_server_ms"] == 42
    assert store.snapshots["account_identity"]["data"]["values"]["provider_account_id"] == "123"
    assert store.snapshots["stream"]["error"] == "stream_observation_unavailable"
    assert store.snapshots["effective_config"]["data"]["orders_enabled"] is None
    assert "never-store" not in str(store.snapshots)
    assert not any(sql.startswith(("UPDATE", "INSERT", "DELETE")) for sql, _ in store.calls)


def test_failed_provider_source_does_not_destroy_other_sources():
    class FailedAccount(GetOnlyClient):
        async def get_account(self, symbol=None):
            raise RuntimeError("api_secret=never-store")

    store = MemoryStore()
    asyncio.run(SnapshotCollector({"BITGET_MODE": "LIVE"}, store, FailedAccount()).collect_once())
    assert store.snapshots["account"]["data"] is None
    assert store.snapshots["account"]["observed_at"] is None
    assert store.snapshots["positions"]["data"]["items"] == []
    assert "never-store" not in str(store.snapshots)


def test_missing_provider_does_not_claim_flat_or_zero():
    service = ReadService(MemoryStore(), SCOPE)
    assert service._snapshot("positions") is None
    assert service._evidence[-1]["read_status"] == "unknown"
    assert service._evidence[-1]["complete"] is False
    assert service._evidence[-1]["observed_at"] is None


def test_stale_partial_evidence_retains_real_time():
    now = datetime.now(UTC)
    result = _evidence(
        "provider", "provider", now - timedelta(seconds=60), complete=False, stale_after=15, now=now
    )
    assert result["freshness"] == "stale"
    assert result["read_status"] == "partial"
    assert result["age_seconds"] == 60


def test_filters_are_allowlisted_and_scope_bound():
    assert (
        _filters("signals", {"symbol": "BTCUSDT", "has_media": True}, SCOPE)["has_media"] == "true"
    )
    for invalid in (
        {"sql": "DROP TABLE positions"},
        {"environment": "DEMO"},
        {"sort": "raw_text"},
        {"from": "2026-10-14"},
    ):
        with pytest.raises(ReadError):
            _filters("signals", invalid, SCOPE)


def test_secret_fields_and_inline_credentials_are_removed():
    value = sanitize(
        {
            "api_secret": "secret",
            "token_hash": "hash",
            "media_path": "/private",
            "nested": {"passphrase": "secret", "quantity": Decimal("0.123456789123456789")},
            "pair_token": "BTCUSDT",
            "text": "api_key=secret postgres://user:pass@host/db",
        }
    )
    assert value["pair_token"] == "BTCUSDT"
    assert value["nested"]["quantity"] == "0.123456789123456789"
    assert "secret" not in str(value)
    assert "private" not in str(value)


def ownership_fixture():
    position = {
        "symbol": "BTCUSDT",
        "holdSide": "long",
        "total": "1",
        "cTime": "123",
        "posMode": "one_way_mode",
        "marginMode": "isolated",
    }
    registration = {
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "quantity": Decimal("1"),
        "provider_position_epoch": "123",
        "environment": "LIVE",
        "position_key": "exact-root",
    }
    intent = {
        "id": "entry-id",
        "client_order_id": "exact-root",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "role": "ENTRY",
        "state": "filled",
        "filled_qty": Decimal("1"),
    }
    return position, registration, intent


def test_ownership_requires_epoch_environment_root_quantity_not_symbol():
    position, registration, intent = ownership_fixture()
    assert (
        exact_position_owner(position, [registration], [intent], "LIVE")["intent"]["id"]
        == "entry-id"
    )
    for changed in (
        {"provider_position_epoch": "124"},
        {"environment": "DEMO"},
        {"position_key": "other-root"},
        {"quantity": "2"},
    ):
        assert (
            exact_position_owner(position, [{**registration, **changed}], [intent], "LIVE") is None
        )
    assert (
        exact_position_owner(
            position, [registration], [{**intent, "baseline_id": "baseline"}], "LIVE"
        )
        is None
    )
    assert exact_position_owner(position, [registration, registration], [intent], "LIVE") is None


def test_snapshot_pagination_is_bound_to_filters_and_observation():
    service = ReadService(MemoryStore(), SCOPE)
    rows = [{"id": "a", "symbol": "BTCUSDT"}, {"id": "b", "symbol": "ETHUSDT"}]
    page = service._list_memory("positions", rows, None, {}, 1, None, {"revision": 1})
    assert page["has_more"] is True
    next_page = service._list_memory(
        "positions", rows, None, {}, 1, page["next_cursor"], {"revision": 1}
    )
    assert next_page["has_more"] is False
    with pytest.raises(ReadError):
        service._list_memory(
            "positions", rows, None, {"symbol": "BTCUSDT"}, 1, page["next_cursor"], {"revision": 1}
        )
    with pytest.raises(ReadError):
        service._list_memory("positions", rows, None, {}, 1, page["next_cursor"], {"revision": 2})


def test_scoped_sql_projection_has_real_domain_bindings():
    service = ReadService(MemoryStore(), SCOPE)
    sql, args = service._base("signals")
    assert "canonical_signals" in sql and "cs.revision=t.revision_hash" in sql
    assert "source_management_updates" in sql and "dispatches" in sql
    sql, args = service._base("approvals")
    assert "console_jobs" in sql and "j.scope=" in sql
    assert args
    assert "bitget_verified_close_bindings" in service._expression("fills", "ownership")
    assert "r.balance_snapshot_id=oi.balance_snapshot_id" in service._expression(
        "orders", "ownership"
    )


def test_media_opaque_identity_hash_and_path_containment(tmp_path):
    store = MemoryStore()
    image = b"\x89PNG\r\n\x1a\n" + b"fixture"
    file = tmp_path / "image.png"
    file.write_bytes(image)
    record = {
        "id": "message",
        "revision_hash": "revision",
        "has_media": True,
        "media_path": str(file),
        "media_sha256": hashlib.sha256(image).hexdigest(),
        "media_mime_type": "image/png",
        "media_size_bytes": len(image),
    }
    store.rows = [record]
    service = ReadService(store, SCOPE, tmp_path)
    opaque = service._media_reference(record)[0]["id"]
    assert str(file) not in str(service._media_reference(record))
    assert service.read("media", opaque, {"message_id": "message"})["data"]["content"] == image
    record["media_path"] = str(tmp_path.parent / "outside.png")
    with pytest.raises(ReadError):
        service.read("media", opaque, {"message_id": "message"})
    record["media_path"] = str(file)
    file.write_bytes(image + b"tampered")
    with pytest.raises(ReadError):
        service.read("media", opaque, {"message_id": "message"})


def test_report_delivery_checks_expiry_scope_and_excludes_backup():
    store = MemoryStore()
    store.rows = [
        {
            "result": {
                "format": "csv",
                "content": "symbol,quantity\nBTCUSDT,1\n",
                "expires_at": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
            }
        }
    ]
    result = ReadService(store, SCOPE).read("artifacts", "report-id")
    assert result["data"]["mime_type"] == "text/csv"
    assert "action='report.export'" in store.calls[-1][0]
    assert store.calls[-1][1][1]
    store.rows[0]["result"]["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    with pytest.raises(ReadError) as error:
        ReadService(store, SCOPE).read("artifacts", "report-id")
    assert error.value.status == 410


def test_no_credentials_no_provider_client_is_constructed():
    store = MemoryStore()
    asyncio.run(SnapshotCollector({"BITGET_MODE": "LIVE"}, store).collect_once())
    assert store.snapshots["account"]["error"] == "provider_credentials_unavailable"
    assert store.snapshots["positions"]["observed_at"] is None
    assert configured_scope({}) == SCOPE


def test_authenticated_account_mismatch_blocks_other_provider_observations():
    class BoundClient(GetOnlyClient):
        async def get_all_positions(self):
            raise AssertionError("Account mismatch must stop authenticated inventory")

    store = MemoryStore()
    asyncio.run(
        SnapshotCollector(
            {"CONSOLE_ACCOUNT_ID": "999", "BITGET_MODE": "LIVE"}, store, BoundClient()
        ).collect_once()
    )
    assert store.snapshots["account_identity"]["data"] is None
    assert store.snapshots["positions"]["error"] == "authenticated_account_binding_unavailable"
    assert store.snapshots["account"]["observed_at"] is None


class ProjectionCursor:
    def __init__(self, store):
        self.store = store
        self.result = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.store.cursor_closes += 1

    def execute(self, sql, params=()):
        self.store.calls.append((sql, params))
        if self.store.fail_domain and "FROM live_order_intents" in sql:
            raise RuntimeError("Database detail never escapes")
        if "AS event_cursor" in sql:
            self.result = [{"event_cursor": 17}]
        elif "AS record_count" in sql:
            self.result = [{"record_count": 0, "latest_update": None}]
        else:
            self.result = []

    def fetchall(self):
        return self.result


class ProjectionStore(MemoryStore):
    def __init__(self):
        super().__init__()
        self.connection_closes = 0
        self.cursor_closes = 0
        self.fail_domain = False

    @contextmanager
    def connection(self):
        store = self

        class Connection:
            def cursor(self, **kwargs):
                return ProjectionCursor(store)

        try:
            yield Connection()
        finally:
            self.connection_closes += 1


@pytest.mark.parametrize(
    "section",
    [
        "overview",
        "signals",
        "dispatches",
        "positions",
        "orders",
        "fills",
        "protection",
        "management",
        "recovery",
        "account",
        "services",
        "config",
        "notifications",
        "jobs",
        "approvals",
        "audit",
        "backups",
        "access",
        "releases",
        "account/pnl",
        "recovery/baselines",
        "config/history",
    ],
)
def test_all_screens_use_owned_transaction_and_truthful_missing_evidence(monkeypatch, section):
    monkeypatch.setattr("fatty_trader.console.reads.set_json_loads", lambda *args, **kwargs: None)
    store = ProjectionStore()
    result = ReadService(store, SCOPE).read(section)
    assert result["scope"] == SCOPE
    assert result["event_cursor"] == 17
    assert result["evidence"]
    assert store.connection_closes == store.cursor_closes == 1
    assert any(
        sql == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY" for sql, _ in store.calls
    )
    if section in {"positions", "protection", "services"}:
        assert result["data"]["items"] is None
        assert any(evidence["read_status"] == "unknown" for evidence in result["evidence"])


def test_independent_read_failure_rolls_back_savepoint_not_other_observations(monkeypatch):
    monkeypatch.setattr("fatty_trader.console.reads.set_json_loads", lambda *args, **kwargs: None)
    store = ProjectionStore()
    store.fail_domain = True
    result = ReadService(store, SCOPE).read("overview")
    assert result["event_cursor"] == 17
    assert result["data"]["ledger"] is None
    assert any(sql == "ROLLBACK TO SAVEPOINT console_read_source" for sql, _ in store.calls)
    assert any(
        evidence["error_code"] == "database_read_unavailable" for evidence in result["evidence"]
    )
    assert store.connection_closes == 1


def test_native_split_aggregate_ownership_needs_no_fallback_registration():
    position = {
        "symbol": "BTCUSDT",
        "holdSide": "long",
        "total": "4",
        "cTime": "123",
        "posMode": "one_way_mode",
        "marginMode": "isolated",
        "stopLossId": "sl-id",
        "takeProfitId": "tp-id",
    }
    root = {
        "id": "market",
        "client_order_id": "root",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "role": "ENTRY",
        "entry_leg": "market",
        "filled_qty": Decimal("1"),
        "remaining_owned_quantity": Decimal("4"),
        "environment": "LIVE",
    }
    plans = [
        {
            "symbol": "BTCUSDT",
            "holdSide": "long",
            "orderId": "sl-id",
            "clientOid": "root-sl",
            "planType": "pos_loss",
        },
        {
            "symbol": "BTCUSDT",
            "holdSide": "long",
            "orderId": "tp-id",
            "clientOid": "root-tp",
            "planType": "pos_profit",
        },
    ]
    owner = native_position_owner(position, plans, [root], "LIVE")
    assert owner is not None
    assert owner["quantity"] == "4"
    assert owner["provider_position_epoch"] == "123"
    assert owner["intent"]["filled_qty"] == Decimal("1")
    assert (
        native_position_owner(
            position, [{**plan, "clientOid": "external"} for plan in plans], [root], "LIVE"
        )
        is None
    )
    assert (
        native_position_owner(
            position, plans, [{**root, "remaining_owned_quantity": Decimal("3")}], "LIVE"
        )
        is None
    )
    assert (
        native_position_owner(position, plans, [{**root, "baseline_id": "baseline"}], "LIVE")
        is None
    )
    assert (
        native_position_owner({**position, "takeProfitId": "replacement"}, plans, [root], "LIVE")
        is None
    )
    assert native_position_owner(position, plans, [root], "DEMO") is None


def test_split_residual_delegates_canonical_domain_owner(monkeypatch):
    calls = []

    class ResidualCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            calls.append("closed")

    class ResidualConnection:
        def cursor(self, **kwargs):
            calls.append(kwargs)
            return ResidualCursor()

    class DomainLedger:
        def __init__(self, connection_factory):
            assert isinstance(connection_factory(), ResidualConnection)

        def remaining_owned_quantity(self, root, *, cursor):
            calls.append(root)
            return Decimal("4")

    monkeypatch.setattr("fatty_trader.console.reads.PostgresLiveIntentStore", DomainLedger)
    rows = add_owned_residuals(
        MemoryStore(),
        [{"client_order_id": "root", "filled_qty": Decimal("1")}],
        ResidualConnection(),
    )
    assert rows[0]["remaining_owned_quantity"] == Decimal("4")
    assert calls[-1] == "closed"


def test_worker_effective_config_uses_canonical_risk_keys_not_web_defaults():
    store = MemoryStore()
    environ = {
        "BITGET_MODE": "LIVE",
        "BITGET_APPROVAL_REFERENCE": "approved-ref",
        "BITGET_CANARY_MAX_ORDERS": "5",
        "BITGET_MAX_MARGIN_PER_TRADE_USDT": "1",
        "BITGET_MIN_LEVERAGE": "5",
        "BITGET_MAX_LEVERAGE": "20",
        "BITGET_ALLOCATION_PCT": "0.20",
        "BITGET_OPERATOR_MUTATIONS_ENABLED": "1",
    }
    asyncio.run(SnapshotCollector(environ, store, GetOnlyClient()).collect_once())
    snapshot = store.snapshots["effective_config"]
    for key, value in environ.items():
        assert snapshot["data"]["values"][key] == value
    assert snapshot["observed_at"] is not None
    assert snapshot["data"]["worker"] == "console-worker"
    assert snapshot["data"]["orders_enabled"] is None
    assert snapshot["data"]["values"]["BITGET_BALANCE_MAX_AGE_SECONDS"] is None


def test_native_owner_does_not_overwrite_previously_proved_epoch():
    position = {
        "symbol": "BTCUSDT",
        "holdSide": "long",
        "total": "4",
        "cTime": "124",
        "posMode": "one_way_mode",
        "marginMode": "isolated",
        "stopLossId": "sl-id",
        "takeProfitId": "tp-id",
    }
    root = {
        "client_order_id": "root",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "role": "ENTRY",
        "environment": "LIVE",
        "remaining_owned_quantity": "4",
        "recorded_position_epoch": "123",
    }
    plans = [
        {
            "symbol": "BTCUSDT",
            "holdSide": "long",
            "orderId": "sl-id",
            "clientOid": "root-sl",
            "planType": "pos_loss",
        },
        {
            "symbol": "BTCUSDT",
            "holdSide": "long",
            "orderId": "tp-id",
            "clientOid": "root-tp",
            "planType": "pos_profit",
        },
    ]
    assert native_position_owner(position, plans, [root], "LIVE") is None


@pytest.mark.parametrize("layout", ["generic", "leg_specific"])
def test_native_identity_response_variants_without_position_protection_ids(layout):
    position = {
        "symbol": "BTCUSDT",
        "holdSide": "long",
        "total": "4",
        "cTime": "123",
        "posMode": "one_way_mode",
        "marginMode": "isolated",
        "stopLossId": None,
        "takeProfitId": "",
    }
    root = {
        "client_order_id": "root",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "role": "ENTRY",
        "environment": "LIVE",
        "remaining_owned_quantity": "4",
    }
    plans = [
        {"symbol": "BTCUSDT", "holdSide": "long", "orderId": "sl-id", "planType": "pos_loss"},
        {"symbol": "BTCUSDT", "holdSide": "long", "orderId": "tp-id", "planType": "pos_profit"},
    ]
    if layout == "generic":
        plans[0]["clientOid"], plans[1]["clientOid"] = "root-sl", "root-tp"
    else:
        plans[0]["stopLossClientOid"], plans[1]["stopSurplusClientOid"] = "root-sl", "root-tp"
        for plan in plans:
            plan["planOrderId"] = plan.pop("orderId")
    owner = native_position_owner(position, plans, [root], "LIVE")
    assert owner is not None
    assert owner["intent"]["client_order_id"] == "root"
    assert (
        native_position_owner({**position, "stopLossId": "foreign"}, plans, [root], "LIVE") is None
    )
    assert native_position_owner(position, plans + [plans[0]], [root], "LIVE") is None


def test_proved_scoped_epoch_owns_unprotected_residual_without_claiming_protection():
    position = {
        "symbol": "BTCUSDT",
        "holdSide": "long",
        "total": "2",
        "cTime": "123",
        "posMode": "one_way_mode",
        "marginMode": "isolated",
    }
    root = {
        "client_order_id": "root",
        "symbol": "BTCUSDT",
        "side": "BUY",
        "role": "ENTRY",
        "environment": "LIVE",
        "remaining_owned_quantity": "2",
        "recorded_position_epoch": "123",
        "epoch_environment": "LIVE",
        "epoch_account_id": "12345",
    }
    owner = native_position_owner(position, [], [root], "LIVE", "12345")
    assert owner is not None
    assert owner["proof"] == "immutable-scoped-epoch-and-durable-residual"
    assert "protection_state" not in owner
    assert native_position_owner(position, [], [root], "LIVE", "different-account") is None
    assert native_position_owner({**position, "cTime": "124"}, [], [root], "LIVE", "12345") is None


def test_failed_refresh_retains_last_known_without_complete_zero():
    store = MemoryStore()
    old = datetime.now(UTC) - timedelta(seconds=60)
    store.snapshots["positions"] = {
        "id": "failed",
        "data": None,
        "error": "failed",
        "observed_at": None,
    }
    store.rows = [
        {
            "id": "good",
            "data": {"items": [], "complete": True, "observed_at": old.isoformat()},
            "observed_at": old,
            "error": None,
        }
    ]
    service = ReadService(store, SCOPE)
    observation = service._snapshot("positions")
    assert observation["items"] == [] and observation["last_known"] is True
    assert observation["complete"] is False
    evidence = service._evidence[-1]
    assert evidence["observed_at"] == old.isoformat()
    assert evidence["freshness"] == "stale" and evidence["complete"] is False
    assert evidence["error_code"] == "provider_read_unavailable"
    page = service._list_memory("positions", [], None, {}, 50, None, observation)
    assert page["total"] is None


def test_partial_inventory_never_reports_complete_collection_total():
    service = ReadService(MemoryStore(), SCOPE)
    assert (
        service._list_memory("positions", [], None, {}, 50, None, {"complete": False})["total"]
        is None
    )
    assert (
        service._list_memory("positions", [], None, {}, 50, None, {"complete": True})["total"] == 0
    )
    assert provider_rows({"entrustedList": [], "total": "1"}, "orders") == ([], False)
    assert provider_rows({"entrustedList": [], "total": "0"}, "orders") == ([], True)
    with pytest.raises(ValueError):
        provider_rows({"entrustedList": [], "total": -1}, "orders")


def test_signal_dispatch_filter_and_revision_are_scoped():
    store = MemoryStore()
    service = ReadService(store, SCOPE)
    service._table("signals", None, {"dispatch_state": "QUEUED"}, 50, None)
    sql = store.calls[-1][0]
    assert "d.exchange='bitget' AND d.state=%s" in sql
    revision_sql = next(sql for sql, params in store.calls if "AS record_count" in sql)
    assert "scoped.sort_time" in revision_sql and "FROM (SELECT" in revision_sql


def test_staged_entries_are_visible_in_recovery():
    store = MemoryStore()
    service = ReadService(store, SCOPE)
    service._read("recovery", None, {}, 50, None)
    assert any("state IN ('staged','requested'" in sql for sql, params in store.calls)
