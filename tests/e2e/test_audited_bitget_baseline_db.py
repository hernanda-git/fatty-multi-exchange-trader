"""Exact historical retirement on disposable PostgreSQL; fake GET-only provider."""
# ruff: noqa: F401, F811

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from test_entry_lifecycle_recovery_db import _owned_entry
from test_postgres_migration_and_margin_admission import _connect, _migrate, postgres_schema

from fatty_trader.execution.bitget_dispatch_repository import PostgresBitgetDispatchRepository
from fatty_trader.storage.bitget_baseline import (
    BaselineRefused,
    PostgresBitgetBaseline,
    collect_flat_evidence,
)


class GetOnlyProvider:
    environment = "LIVE"

    def __init__(self, defect=None):
        self.defect = defect
        self.calls = []
        self.inventory_round = 0

    async def _get(self, path, params=None):
        self.calls.append((path, params))
        if path == "/api/v2/public/time":
            self.inventory_round += 1
            return {"serverTime": str(int(datetime.now(UTC).timestamp() * 1000))}
        if path == "/api/v2/spot/account/info":
            return {"userId": "999" if self.defect == "identity" else "123"}
        if path.endswith("all-position"):
            if self.defect == "position" or (
                self.defect == "second-position" and self.inventory_round >= 3
            ):
                return [{"symbol": "BTCUSDT", "total": "1"}]
            if self.defect == "malformed-position":
                return [{"symbol": "BTCUSDT", "total": "NaN"}]
            return []
        if params.get("productType") == "COIN-FUTURES" and params.get("planType") == "track_plan":
            if self.defect == "track-plan":
                return {"entrustedList": [{"orderId": "active"}], "endId": "active"}
            if self.defect == "null-list":
                return {"entrustedList": None, "endId": ""}
            if self.defect == "incomplete":
                return {"entrustedList": [], "endId": "more-pages"}
            if self.defect == "missing-cursor":
                return {"entrustedList": []}
        if self.defect == "paired-null":
            return {"entrustedList": None, "endId": None}
        return {"entrustedList": [], "endId": ""}


def setup_history(postgres_schema):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    dispatch, intent, store = _owned_entry(dsn, schema, "FILLED")
    with _connect(dsn, schema) as conn:
        conn.execute(
            "UPDATE dispatches SET created_at=now()-interval '7 days', "
            "terminal_reason='recovery-missing-protection:owned-flat-close-fill-unproven'"
        )
        conn.execute(
            "UPDATE live_order_intents SET created_at=now()-interval '7 days', "
            "fee=0.123456789123456789123456789"
        )
        conn.execute("UPDATE bitget_margin_reservations SET created_at=now()-interval '7 days'")
        conn.execute(
            "INSERT INTO fills(id,exchange,client_order_id,provider_fill_id,symbol,"
            "price,quantity,fee,realized_pnl) VALUES (%s,'bitget',%s,'original-fill',"
            "'BTCUSDT',100,2,0.2,-3)",
            (uuid4(), intent.client_oid),
        )

    def factory():
        return _connect(dsn, schema)

    return (
        dsn,
        schema,
        dispatch,
        PostgresBitgetBaseline(factory),
        PostgresBitgetDispatchRepository(factory),
    )


def arguments():
    return {
        "expected_account_id": "123",
        "environment": "LIVE",
        "historical_before": datetime.now(UTC) - timedelta(days=1),
        "approval_reference": "explicit-owner-approval",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_shape", [None, "paired-null"])
async def test_baseline_preserves_fill_and_uncertainty_releases_only_exact_reservation(
    postgres_schema, empty_shape
):
    dsn, schema, dispatch, baseline, repo = setup_history(postgres_schema)
    provider = GetOnlyProvider(empty_shape)
    args = arguments()
    with _connect(dsn, schema) as conn:
        prior_intent = conn.execute("SELECT to_jsonb(i) FROM live_order_intents i").fetchone()[0]
        prior_fill = conn.execute("SELECT to_jsonb(f) FROM fills f").fetchone()[0]
        prior_dispatch = conn.execute("SELECT to_jsonb(d) FROM dispatches d").fetchone()[0]
    dry = await baseline.run(provider, **args)
    assert dry["counts"] == {"intent": 1, "dispatch": 1, "reservation": 1}
    with _connect(dsn, schema) as conn:
        assert conn.execute("SELECT count(*) FROM bitget_audited_baselines").fetchone()[0] == 0
        assert (
            conn.execute("SELECT state FROM bitget_margin_reservations").fetchone()[0] == "consumed"
        )
    applied = await baseline.run(
        provider, **args, apply=True, expected_digest=dry["candidate_digest"]
    )
    assert applied["applied"] is True
    assert provider.inventory_round == 3  # dry run + prelock + locked fresh inventory
    assert repo.recovery_candidates() == []
    assert repo.inventory_issues("LIVE") == []
    assert repo.unresolved_protection_issues() == []
    assert repo.baseline_binding_issues("123", "LIVE") == []
    assert repo.baseline_binding_issues("999", "LIVE")
    assert repo.baseline_binding_issues("123", "DEMO")
    assert repo.baseline_binding_issues(None, "LIVE")
    assert repo.baseline_binding_issues("123", None)
    assert not repo.entry_source_eligible(dispatch.id)
    with _connect(dsn, schema) as conn:
        assert (
            conn.execute("SELECT to_jsonb(i) FROM live_order_intents i").fetchone()[0]
            == prior_intent
        )
        assert conn.execute("SELECT to_jsonb(f) FROM fills f").fetchone()[0] == prior_fill
        assert conn.execute("SELECT to_jsonb(d) FROM dispatches d").fetchone()[0] == prior_dispatch
        state, reason = conn.execute(
            "SELECT state,resolution_reason FROM bitget_margin_reservations"
        ).fetchone()
        assert state == "released"
        assert "unresolved-history" in reason
        snapshot = conn.execute(
            "SELECT prior_snapshot FROM bitget_baseline_records WHERE record_kind='reservation'"
        ).fetchone()[0]
        assert snapshot["state"] == "consumed"
        assert (
            conn.execute(
                "SELECT prior_snapshot->>'fee' FROM bitget_baseline_records "
                "WHERE record_kind='intent'"
            ).fetchone()[0]
            == "0.123456789123456789123456789"
        )
        reservation_id = conn.execute("SELECT id FROM bitget_margin_reservations").fetchone()[0]
        conn.execute(
            "INSERT INTO live_order_intents(id,exchange,client_order_id,symbol,side,"
            "role,state,requested_qty) VALUES (%s,'bitget','new-unresolved','ETHUSDT',"
            "'BUY','ENTRY','unknown',1)",
            (uuid4(),),
        )
    from fatty_trader.storage.balance_reservations import PostgresBitgetMarginReservationRepository

    margins = PostgresBitgetMarginReservationRepository(lambda: _connect(dsn, schema))
    margins.resolve(reservation_id, "FILLED")
    with _connect(dsn, schema) as conn:
        assert conn.execute(
            "SELECT state,resolution_reason FROM bitget_margin_reservations"
        ).fetchone() == (state, reason)
    assert repo.inventory_issues("LIVE") == ["orphan-entry-intent"]
    with pytest.raises(BaselineRefused, match="outside approved historical cutoff"):
        await baseline.run(provider, **args)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect",
    [
        "position",
        "malformed-position",
        "track-plan",
        "null-list",
        "incomplete",
        "missing-cursor",
        "identity",
    ],
)
async def test_baseline_fails_closed_on_provider_inventory(postgres_schema, defect):
    dsn, schema, _, baseline, _ = setup_history(postgres_schema)
    with pytest.raises(BaselineRefused):
        await baseline.run(GetOnlyProvider(defect), **arguments())
    with _connect(dsn, schema) as conn:
        assert conn.execute("SELECT count(*) FROM bitget_baseline_records").fetchone()[0] == 0
        assert (
            conn.execute("SELECT state FROM bitget_margin_reservations").fetchone()[0] == "consumed"
        )


@pytest.mark.asyncio
async def test_baseline_rechecks_provider_inside_locked_apply_transaction(postgres_schema):
    dsn, schema, _, baseline, _ = setup_history(postgres_schema)
    provider = GetOnlyProvider("second-position")
    args = arguments()
    dry = await baseline.run(provider, **args)
    with pytest.raises(BaselineRefused, match="position exposure"):
        await baseline.run(provider, **args, apply=True, expected_digest=dry["candidate_digest"])
    with _connect(dsn, schema) as conn:
        assert conn.execute("SELECT count(*) FROM bitget_audited_baselines").fetchone()[0] == 0
        assert (
            conn.execute("SELECT state FROM bitget_margin_reservations").fetchone()[0] == "consumed"
        )


@pytest.mark.asyncio
async def test_baseline_refuses_database_drift_and_exact_cutoff(postgres_schema):
    dsn, schema, _, baseline, _ = setup_history(postgres_schema)
    args = arguments()
    dry = await baseline.run(GetOnlyProvider(), **args)
    with _connect(dsn, schema) as conn:
        conn.execute("UPDATE live_order_intents SET fee=0.75")
    with pytest.raises(BaselineRefused, match="changed since reviewed"):
        await baseline.run(
            GetOnlyProvider(), **args, apply=True, expected_digest=dry["candidate_digest"]
        )
    with _connect(dsn, schema) as conn:
        conn.execute("UPDATE live_order_intents SET created_at=%s", (args["historical_before"],))
    with pytest.raises(BaselineRefused, match="outside approved historical cutoff"):
        await baseline.run(GetOnlyProvider(), **args)


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe", ["lease", "local-position", "non-entry"])
async def test_baseline_refuses_active_local_risk(postgres_schema, unsafe):
    dsn, schema, _, baseline, _ = setup_history(postgres_schema)
    with _connect(dsn, schema) as conn:
        if unsafe == "lease":
            conn.execute(
                "UPDATE dispatches SET claimed_by='running',lease_until=now()+interval '5 minutes'"
            )
        elif unsafe == "local-position":
            conn.execute(
                "INSERT INTO positions(id,exchange,symbol,direction,quantity,protection_state) "
                "VALUES (%s,'bitget','BTCUSDT','LONG',1,'PENDING')",
                (uuid4(),),
            )
        else:
            conn.execute(
                "INSERT INTO live_order_intents("
                "id,exchange,client_order_id,symbol,side,role,state,requested_qty) "
                "VALUES (%s,'bitget','unknown-stop','BTCUSDT','SELL','SL','unknown',1)",
                (uuid4(),),
            )
    with pytest.raises(BaselineRefused, match="active local"):
        await baseline.run(GetOnlyProvider(), **arguments())


@pytest.mark.asyncio
async def test_baseline_audit_is_immutable(postgres_schema):
    dsn, schema, _, baseline, _ = setup_history(postgres_schema)
    args = arguments()
    dry = await baseline.run(GetOnlyProvider(), **args)
    await baseline.run(
        GetOnlyProvider(), **args, apply=True, expected_digest=dry["candidate_digest"]
    )
    import psycopg

    for sql in (
        "UPDATE bitget_audited_baselines SET approval_reference='changed'",
        "DELETE FROM bitget_baseline_records",
        "TRUNCATE bitget_baseline_records",
    ):
        with (
            pytest.raises(psycopg.errors.CheckViolation, match="immutable audited baseline"),
            _connect(dsn, schema) as conn,
        ):
            conn.execute(sql)


@pytest.mark.asyncio
async def test_flat_evidence_inventories_all_products_and_pending_families():
    provider = GetOnlyProvider()
    evidence = await collect_flat_evidence(provider, "123", "LIVE")
    assert set(evidence["inventory"]) == {"USDT-FUTURES", "USDC-FUTURES", "COIN-FUTURES"}
    assert all(
        set(product["pending"]) == {"ordinary", "normal_plan", "profit_loss", "track_plan"}
        for product in evidence["inventory"].values()
    )
    provider.environment = "DEMO"
    with pytest.raises(BaselineRefused, match="environment mismatch"):
        await collect_flat_evidence(provider, "123", "LIVE")


@pytest.mark.asyncio
async def test_baseline_does_not_replay_old_queue_and_permits_future_fresh_claim(postgres_schema):
    from source_freshness_fixtures import eligible_dispatch_source

    dsn, schema, _, baseline, repo = setup_history(postgres_schema)
    args = arguments()
    dry = await baseline.run(GetOnlyProvider(), **args)
    await baseline.run(
        GetOnlyProvider(), **args, apply=True, expected_digest=dry["candidate_digest"]
    )
    stale_id, fresh_id = uuid4(), uuid4()
    with _connect(dsn, schema) as conn:
        for dispatch_id in (stale_id, fresh_id):
            conn.execute(
                "INSERT INTO dispatches(id,source_type,source_id,revision,exchange,state) "
                "VALUES (%s,'e2e',%s,%s,'bitget','QUEUED')",
                (dispatch_id, uuid4(), "b" * 64),
            )
            eligible_dispatch_source(conn, dispatch_id)
        conn.execute(
            "UPDATE telegram_messages SET received_at=now()-interval '1 day', "
            "entry_expires_at=now()-interval '23 hours' WHERE id=("
            "SELECT s.message_id FROM canonical_signals s JOIN dispatches d "
            "ON d.source_id=s.id WHERE d.id=%s)",
            (stale_id,),
        )
    claimed = repo.claim("future-worker", 30)
    assert claimed is not None and claimed.id == fresh_id
    with _connect(dsn, schema) as conn:
        state, attempts = conn.execute(
            "SELECT state,attempts FROM dispatches WHERE id=%s", (stale_id,)
        ).fetchone()
        assert state == "EXPIRED" and attempts == 0
