from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from fatty_trader.console.trading import ACTION_SCHEMAS, TradingHandlers, _SnapshotClient
from fatty_trader.execution.bitget_dispatch_repository import (
    BitgetDispatch,
)
from fatty_trader.service import _bitget_dispatch_preflight

SCOPE = {"account_id": "123", "venue": "bitget", "environment": "LIVE"}
CONFIG = {
    "TRADER_MODE": "LIVE",
    "BITGET_MODE": "LIVE",
    "BITGET_EXECUTION_ENABLED": "1",
    "BITGET_OPERATOR_MUTATIONS_ENABLED": "1",
    "BITGET_APPROVAL_REFERENCE": "approved",
    "BITGET_CANARY_MAX_ORDERS": "2",
    "BITGET_MAX_MARGIN_PER_TRADE_USDT": "1",
}


class SnapshotStore:
    def __init__(self):
        self.when = datetime.now(UTC)
        self.paused = False

    def latest_snapshot(self, kind, scope):
        values = {"effective_config": CONFIG, "account_identity": {"provider_account_id": "123"}}
        return {
            "data": {"values": values.get(kind, {}), "complete": kind != "effective_config"},
            "observed_at": self.when,
        }

    def one(self, sql, params=()):
        return {"active": self.paused} if "console_admission_pauses" in sql else None


def test_web_uses_worker_effective_gates_without_credentials():
    store = SnapshotStore()
    handler = TradingHandlers({"CONSOLE_ACCOUNT_ID": "123"}, store)
    assert handler._runtime is None
    gates = handler.capability("trade.open", SCOPE)["gates"]
    assert all(g["verdict"] == "pass" for g in gates)
    assert handler._runtime is None
    store.paused = True
    assert "console-admission" in [
        r["code"] for r in handler.capability("trade.open", SCOPE)["reasons"]
    ]


def test_missing_or_stale_identity_never_adopts_account():
    store = SnapshotStore()
    store.when -= timedelta(seconds=6)
    with pytest.raises(ValueError, match="stale"):
        _SnapshotClient(store, SCOPE)


def test_manual_entry_provenance_has_no_telegram_receipt():
    handler = TradingHandlers({}, SnapshotStore())
    dispatch = handler._dispatch(
        uuid4(),
        {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "order_type": "LIMIT",
            "stop_loss": "9",
            "take_profit": "11",
            "margin": "0.5",
            "leverage": 10,
        },
        Decimal("10"),
    )
    assert dispatch.source_channel_id is None and dispatch.source_received_at is None
    assert dispatch.manual_margin == Decimal("0.5") and dispatch.manual_leverage == 10
    assert dispatch.order_type == "LIMIT"


@pytest.mark.asyncio
async def test_preview_sizing_is_get_only_and_reserves_nothing():
    from test_bitget_service_admission_wiring import Venue

    venue = Venue()
    original = venue.preflight
    reads = []

    async def readonly(symbol, *, read_only=False):
        assert read_only
        reads.append(symbol)
        return await original(symbol)

    venue.preflight = readonly
    dispatch = BitgetDispatch(
        uuid4(),
        "QUEUED",
        "console",
        1,
        "PENDLEUSDT",
        "LONG",
        Decimal("2.32"),
        Decimal("2.30"),
        (Decimal("2.40"),),
        order_type="LIMIT",
    )
    result = await _bitget_dispatch_preflight(venue, CONFIG, preview_only=True)(dispatch)
    assert reads == ["PENDLEUSDT"]
    assert Decimal(result["margin_usdt"]) <= 1
    assert Decimal(result["quantity"]) > 0
    assert result["entry_price"] == "2.32"


def test_typed_actions_have_real_adapters_and_no_account_wide_overload():
    assert "trade.open" in ACTION_SCHEMAS
    assert "protection.repair_native" in ACTION_SCHEMAS
    assert "protection.update_fallback" in ACTION_SCHEMAS
    assert "intent.reconcile" in ACTION_SCHEMAS
    assert not {"position.close_all", "order.cancel_all"} & ACTION_SCHEMAS.keys()


def test_pending_cancel_preview_and_version_exclude_every_baseline_representation():
    class Store(SnapshotStore):
        def query(self, sql, params):
            assert "JOIN bitget_margin_reservations" in sql
            assert "JOIN dispatches" in sql
            for kind in ("intent", "reservation", "dispatch"):
                assert f"b.record_kind='{kind}'" in sql
            assert params[:2] == ("BTCUSDT", "LIVE")
            return []

    handler = TradingHandlers({"CONSOLE_ACCOUNT_ID": "123"}, Store())
    assert handler._pending_owned_entries("BTCUSDT", SCOPE) == []
    assert handler.current_resource_version("entry.cancel_owned", {"symbol": "BTCUSDT"}, [], SCOPE)


def test_cancel_post_claim_checks_authority_inside_transaction_before_claim():
    from contextlib import contextmanager

    class Store(SnapshotStore):
        def __init__(self):
            super().__init__()
            self.events = []
            self.cancelled = True

        @contextmanager
        def connection(self):
            self.events.append("begin")
            yield self
            self.events.append("commit")

        def cursor(self, connection):
            return self

        def effect_authority(self, cursor, job):
            assert cursor is self
            self.events.append("authority")
            if self.cancelled:
                raise ValueError("cancelled")

        def execute(self, sql, params):
            self.events.append("claim")
            for kind in ("intent", "reservation", "dispatch"):
                assert f"b.record_kind='{kind}'" in sql

        def fetchone(self):
            return {"client_order_id": "owned"}

    store = Store()
    handler = TradingHandlers({**CONFIG, "CONSOLE_ACCOUNT_ID": "123"}, store)
    handler._active_job = {"scope": SCOPE}
    with pytest.raises(ValueError, match="cancelled"):
        handler._claim_owned_cancel_post("owned")
    assert store.events == ["begin", "authority"]
    store.events.clear()
    store.cancelled = False
    assert handler._claim_owned_cancel_post("owned")
    assert store.events == ["begin", "authority", "claim", "commit"]


@pytest.mark.asyncio
async def test_protection_claim_is_not_revoked_by_later_job_cancellation():
    from test_bitget_native_protection import NativeProtectionClient, _intent, _plan

    from fatty_trader.exchanges.bitget.live import InMemoryLiveIntentStore

    class Client(NativeProtectionClient):
        async def modify_position_stop_loss(self, **kwargs):
            self.called = kwargs

        async def get_contracts(self):
            return [
                {
                    "symbol": "BTCUSDT",
                    "pricePlace": "2",
                    "priceEndStep": "1",
                    "sizeMultiplier": "0.001",
                    "minTradeNum": "0.001",
                    "maxOrderQty": "100",
                    "maxMarketOrderQty": "100",
                    "minTradeUSDT": "5",
                    "maxLever": "50",
                    "contractValue": "1",
                }
            ]

    client = Client()
    root = _intent()
    root.planned_stop_loss = Decimal("49000")
    root.planned_take_profits = (Decimal("51000"),)
    store = InMemoryLiveIntentStore()
    store.save(root)
    handler = TradingHandlers({}, SnapshotStore())
    handler._intents = store
    handler._runtime = SimpleNamespace(client=client)
    claims = []

    def claim(intent, **kwargs):
        claims.append(intent.client_oid)
        return store.claim(intent)

    handler._claim_intent = claim

    async def readback(oid):
        return {"confirmed": True}

    handler._protection_readback = readback
    await handler._protect(
        root,
        {
            "quantity": _plan().quantity,
            "row": {"holdSide": "long", "stopLossId": "native-plan", "takeProfitId": "native-plan"},
        },
        "protection.set_sl",
        "49000",
        "console-child",
    )
    assert claims == ["console-child"]
    assert client.called["client_oid"] == root.client_oid + "-sl"
