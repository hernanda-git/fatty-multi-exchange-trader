"""Real PostgreSQL retention proofs in disposable migrated schemas only."""

# ruff: noqa: F401, F811
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from psycopg import sql
from psycopg.types.json import Jsonb
from test_postgres_migration_and_margin_admission import _connect, _migrate, postgres_schema

from fatty_trader.console.reads import ReadService
from fatty_trader.console.store import ConsoleStore, TelemetryRetention

SCOPE = {"account_id": "retention-fixture", "venue": "bitget", "environment": "LIVE"}


@pytest.fixture
def telemetry_db(postgres_schema):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    return ConsoleStore(lambda: _connect(dsn, schema)), dsn, schema


def snapshot(store, kind, scope, days, *, error=False, observed=True, snapshot_id=None):
    snapshot_id = snapshot_id or str(uuid4())
    store.execute(
        """INSERT INTO console_snapshots
           (id,kind,account_id,environment,scope,data,error,observed_at,collected_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,now()-%s*interval '1 day')""",
        (
            snapshot_id,
            kind,
            scope["account_id"],
            scope["environment"],
            Jsonb(scope),
            None if error else Jsonb({"complete": True, "items": [{"symbol": "BTCUSDT"}]}),
            Jsonb({"code": "provider_unavailable"}) if error else None,
            datetime(2026, 1, 1, tzinfo=UTC) if observed else None,
            days,
        ),
    )
    return snapshot_id


def event(store, scope, hours):
    return store.one(
        """INSERT INTO console_event_outbox
           (event,scope,resource_type,resource_id,resource_version,created_at)
           VALUES ('read_model_changed',%s,'fixture','fixture','1',
                   now()-%s*interval '1 hour') RETURNING id""",
        (Jsonb(scope), hours),
    )["id"]


def durable_rows(store):
    """Fingerprint every migrated non-telemetry table, not a handpicked subset."""
    with store.connection() as connection:
        cursor = store.cursor(connection)
        cursor.execute(
            """SELECT table_name FROM information_schema.tables
               WHERE table_schema=current_schema() AND table_type='BASE TABLE'
                 AND table_name NOT IN ('console_snapshots','console_event_outbox')
               ORDER BY table_name"""
        )
        names = [row["table_name"] for row in cursor.fetchall()]
        result = {}
        for name in names:
            cursor.execute(
                sql.SQL("SELECT to_jsonb(t)::text AS record FROM {} t ORDER BY 1").format(
                    sql.Identifier(name)
                )
            )
            result[name] = [row["record"] for row in cursor.fetchall()]
        return result


def drain(store, retention):
    totals = {"snapshots_deleted": 0, "events_deleted": 0}
    for _ in range(100):
        result = store.prune_telemetry(retention)
        assert result["status"] == "completed"
        for field in totals:
            assert 0 <= result[field] <= retention.batch_size
            totals[field] += result[field]
        if result["snapshots_deleted"] == result["events_deleted"] == 0:
            return totals
    pytest.fail("bounded fixture cleanup failed to converge")


def test_preserves_latest_and_latest_good_per_kind_account_environment(telemetry_db):
    store, *_ = telemetry_db
    groups = [
        ("positions", SCOPE),
        ("orders", SCOPE),
        ("positions", {**SCOPE, "environment": "DEMO"}),
        ("positions", {**SCOPE, "account_id": "other-account"}),
    ]
    expected = set()
    for index, (kind, scope) in enumerate(groups):
        snapshot(store, kind, scope, 40)
        good = snapshot(store, kind, scope, 30)
        snapshot(store, kind, scope, 20, observed=False)
        snapshot(store, kind, scope, 15, error=True)
        latest = snapshot(store, kind, scope, 10, error=True)
        if index == 0:
            latest = snapshot(store, kind, scope, 1, error=True)
        expected.update((good, latest))
    store.audit("fixture.retention", "historical_evidence", scope=SCOPE)
    store.execute(
        "INSERT INTO venue_kill_switches(scope,active,reason) VALUES(%s,true,%s)",
        ("retention-fixture", "must not clear this latch"),
    )
    before = durable_rows(store)
    totals = drain(store, TelemetryRetention(batch_size=2))
    assert totals["snapshots_deleted"] == 13
    assert {row["id"] for row in store.query("SELECT id FROM console_snapshots")} == expected
    assert durable_rows(store) == before
    for kind, scope in groups:
        latest = store.latest_snapshot(kind, scope)
        assert latest["error"]["code"] == "provider_unavailable"
        view = ReadService(store, scope)._snapshot(kind)
        assert view["last_known"] is True
        assert view["complete"] is False
        assert view["items"] == [{"symbol": "BTCUSDT"}]


def test_timestamp_ties_use_same_id_order_as_current_views(telemetry_db):
    store, *_ = telemetry_db
    with store.connection() as connection:
        cursor = store.cursor(connection)
        for snapshot_id in ("a", "b", "c"):
            cursor.execute(
                """INSERT INTO console_snapshots
                   (id,kind,account_id,environment,scope,data,observed_at,collected_at)
                   VALUES (%s,'positions',%s,%s,%s,%s,now(),now()-interval '10 days')""",
                (snapshot_id, SCOPE["account_id"], SCOPE["environment"], Jsonb(SCOPE), Jsonb({})),
            )
    drain(store, TelemetryRetention(batch_size=1))
    assert store.query("SELECT id FROM console_snapshots") == [{"id": "c"}]
    assert store.latest_snapshot("positions", SCOPE)["id"] == "c"


def test_snapshot_age_boundary_keeps_all_unexpired_samples(telemetry_db):
    store, *_ = telemetry_db
    good = snapshot(store, "positions", SCOPE, 30)
    expired = snapshot(store, "positions", SCOPE, 7.001, error=True)
    unexpired = snapshot(store, "positions", SCOPE, 6.999, error=True)
    latest = snapshot(store, "positions", SCOPE, 0, error=True)
    totals = drain(store, TelemetryRetention(batch_size=1))
    assert totals["snapshots_deleted"] == 1
    remaining = {row["id"] for row in store.query("SELECT id FROM console_snapshots")}
    assert remaining == {good, unexpired, latest}
    assert expired not in remaining


def test_outbox_48h_retention_keeps_24h_replay_and_expired_scope_highwater(telemetry_db):
    store, *_ = telemetry_db
    old = [event(store, SCOPE, hours) for hours in (90, 80, 70)]
    retained_expired = event(store, SCOPE, 25)
    first_recent = event(store, SCOPE, 23.9)
    newest = event(store, SCOPE, 1)
    expired_scope = {**SCOPE, "account_id": "expired-scope"}
    expired_old = event(store, expired_scope, 90)
    expired_last = event(store, expired_scope, 80)
    unscoped = event(store, {}, 90)
    before = store.event_bounds(SCOPE)
    assert before == {"first": first_recent, "last": newest}
    totals = drain(store, TelemetryRetention(batch_size=2))
    assert totals["events_deleted"] == len(old) + 2
    remaining = {row["id"] for row in store.query("SELECT id FROM console_event_outbox")}
    assert remaining == {retained_expired, first_recent, newest, expired_last}
    assert expired_old not in remaining and unscoped not in remaining
    assert store.event_bounds(SCOPE) == before
    assert [row["id"] for row in store.events(first_recent - 1, SCOPE)] == [first_recent, newest]
    assert store.event_bounds(expired_scope) == {"first": expired_last + 1, "last": expired_last}
    assert store.events(0, expired_scope) == []
    assert store.event_bounds({**SCOPE, "account_id": "never-seen"}) == {
        "first": None,
        "last": None,
    }
    # This is the existing SSE reset predicate; all-expired history remains detectable.
    expired_bounds = store.event_bounds(expired_scope)
    assert expired_old - 1 < expired_bounds["first"] - 1


def test_minimum_outbox_retention_never_deletes_valid_replay_events(telemetry_db):
    store, *_ = telemetry_db
    event(store, SCOPE, 25)
    first = event(store, SCOPE, 23.99)
    last = event(store, SCOPE, 0)
    drain(store, TelemetryRetention(event_hours=24, batch_size=1))
    assert [row["id"] for row in store.events(0, SCOPE)] == [first, last]
    assert store.event_bounds(SCOPE) == {"first": first, "last": last}


def test_bounds_match_exact_min_id_even_for_nonmonotonic_event_timestamps(telemetry_db):
    store, *_ = telemetry_db
    first = event(store, SCOPE, 1)
    event(store, SCOPE, 90)
    last = event(store, SCOPE, 2)
    assert store.event_bounds(SCOPE) == {"first": first, "last": last}
    drain(store, TelemetryRetention())
    assert store.event_bounds(SCOPE) == {"first": first, "last": last}


def test_cleanup_nonblocking_global_fence_and_locked_rows(telemetry_db):
    store, dsn, schema = telemetry_db
    oldest = snapshot(store, "positions", SCOPE, 30)
    snapshot(store, "positions", SCOPE, 20)
    snapshot(store, "positions", SCOPE, 10)
    with _connect(dsn, schema) as connection:
        connection.execute("SELECT pg_advisory_xact_lock(1129270867,1413827653)")
        busy = store.prune_telemetry(TelemetryRetention(batch_size=1))
        assert busy["status"] == "busy" and busy["snapshots_deleted"] == 0
    with _connect(dsn, schema) as connection:
        connection.execute("SELECT id FROM console_snapshots WHERE id=%s FOR UPDATE", (oldest,))
        result = store.prune_telemetry(TelemetryRetention(batch_size=1))
        assert result["snapshots_deleted"] == 1
        assert store.one("SELECT id FROM console_snapshots WHERE id=%s", (oldest,)) is not None
    assert drain(store, TelemetryRetention(batch_size=1))["snapshots_deleted"] == 1
