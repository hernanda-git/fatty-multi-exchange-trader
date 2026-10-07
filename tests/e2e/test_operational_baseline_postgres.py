"""Real socket PostgreSQL tests: operational baseline never rewrites history."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from test_postgres_migration_and_margin_admission import (
    _connect,
    _migrate,
    postgres_schema,  # noqa: F401
)


@pytest.fixture
def ledger(postgres_schema):  # noqa: F811
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    entry_ids = tuple(uuid4() for _ in range(17))
    reservation_ids = tuple(uuid4() for _ in range(4))
    dispatch_ids = tuple(uuid4() for _ in range(4))
    with _connect(dsn, schema) as c:
        assert c.execute(
            "SELECT current_database(), current_user, inet_server_addr()"
        ).fetchone() == ("fatty_test", "fatty_test", None)
        snapshot = uuid4()
        c.execute(
            "INSERT INTO balance_snapshots VALUES (%s,'bitget',10,10,10,'USDT',now())", (snapshot,)
        )
        for n, entry_id in enumerate(entry_ids):
            c.execute(
                """INSERT INTO live_order_intents
                (id,exchange,client_order_id,symbol,side,role,state,requested_qty,filled_qty)
                VALUES (%s,'bitget',%s,%s,'BUY','ENTRY','filled',1,1)""",
                (entry_id, f"old-{n}", f"OLD{n}USDT"),
            )
        for n, (reservation, dispatch) in enumerate(
            zip(reservation_ids, dispatch_ids, strict=True)
        ):
            c.execute(
                """INSERT INTO dispatches
                (id,source_type,source_id,revision,exchange,state,terminal_reason)
                VALUES (%s,'fixture',%s,%s,'bitget','UNPROTECTED',
                'recovery-missing-protection:owned-flat-close-fill-unproven')""",
                (dispatch, uuid4(), "a" * 64),
            )
            c.execute(
                """INSERT INTO bitget_margin_reservations
                (id,exchange,dispatch_id,client_order_id,balance_snapshot_id,
                 planned_margin_usdt,state,expires_at)
                VALUES (%s,'bitget',%s,%s,%s,1,'consumed',now())""",
                (reservation, dispatch, f"old-{n}", snapshot),
            )
        c.execute("INSERT INTO venue_kill_switches(scope,active) VALUES ('global',false)")
    return dsn, schema, entry_ids, reservation_ids, dispatch_ids


def prepare(ledger):
    from fatty_trader.storage.operational_baseline import (
        BaselineContext,
        PostgresOperationalBaselineRepository,
    )

    dsn, schema, entries, reservations, _ = ledger
    repo = PostgresOperationalBaselineRepository(lambda: _connect(dsn, schema))
    baseline = repo.prepare(
        context=BaselineContext("authenticated-uid-7", "a" * 64),
        approval_reference="owner-approval-1",
        entry_ids=entries,
        reservation_ids=reservations,
    )
    return repo, baseline


def test_prepared_receipt_excludes_nothing_and_is_immutable(ledger):
    dsn, schema, entries, reservations, _ = ledger
    _, baseline = prepare(ledger)
    with _connect(dsn, schema) as c:
        receipt = c.execute(
            "SELECT account_identity,approval_reference,history_status FROM "
            "bitget_operational_baselines WHERE id=%s",
            (baseline,),
        ).fetchone()
        assert receipt == ("authenticated-uid-7", "owner-approval-1", "UNVERIFIED")
        assert c.execute(
            "SELECT count(*) FROM bitget_operational_baseline_rows WHERE baseline_id=%s",
            (baseline,),
        ).fetchone() == (25,)
        assert c.execute(
            "SELECT count(*) FROM bitget_operational_baseline_exclusions"
        ).fetchone() == (0,)
        c.execute(
            "UPDATE bitget_operational_baselines SET approval_reference='forged' WHERE id=%s",
            (baseline,),
        )
        c.execute("DELETE FROM bitget_operational_baseline_rows WHERE baseline_id=%s", (baseline,))
        assert c.execute(
            "SELECT approval_reference FROM bitget_operational_baselines WHERE id=%s", (baseline,)
        ).fetchone() == ("owner-approval-1",)
        assert c.execute(
            "SELECT count(*) FROM bitget_operational_baseline_rows WHERE baseline_id=%s",
            (baseline,),
        ).fetchone() == (25,)
        assert c.execute(
            "SELECT count(*) FROM live_order_intents WHERE state='filled'"
        ).fetchone() == (17,)
        assert c.execute(
            "SELECT count(*) FROM bitget_margin_reservations WHERE state='consumed'"
        ).fetchone() == (4,)


@pytest.mark.parametrize(
    "changed",
    [
        {"authenticated": False},
        {"environment": "DEMO"},
        {"positions": 1},
        {"ordinary_orders": 1},
        {"conditional_orders": 1},
        {"conditional_inventory_complete": False},
        {"clock_safe": False},
        {"positions": False},
        {"ordinary_orders": -1},
        {"observed_at": datetime.now(UTC) - timedelta(minutes=2)},
        {"observed_at": datetime.now(UTC) + timedelta(minutes=2)},
        {"observed_at": datetime.now()},
    ],
)
def test_activation_rejects_untrusted_nonflat_incomplete_stale_provider_proof(ledger, changed):
    repo, baseline = prepare(ledger)
    inputs = activation_inputs(ledger)
    proof = replace(inputs["collect_proof"](), **changed)
    inputs["collect_proof"] = lambda: proof
    with pytest.raises(ValueError, match="provider-proof"):
        repo.activate(baseline_id=baseline, **inputs)
    dsn, schema, *_ = ledger
    with _connect(dsn, schema) as c:
        assert c.execute(
            "SELECT count(*) FROM bitget_operational_baseline_activations"
        ).fetchone() == (0,)
        assert c.execute("SELECT count(*) FROM venue_kill_switches WHERE active").fetchone() == (2,)


@pytest.mark.parametrize(
    "changed",
    [
        {"login_authenticated": False},
        {"private_subscriptions_ready": False},
        {"connected": False},
        {"process_generation": ""},
        {"private_pong_at": datetime.now(UTC) - timedelta(minutes=2)},
        {"observed_at": datetime.now(UTC) - timedelta(minutes=2)},
        {"clean_cycle_at": datetime.now(UTC) - timedelta(minutes=2)},
        {"private_pong_at": datetime.now(UTC) + timedelta(minutes=2)},
    ],
)
def test_activation_rejects_missing_stale_monitor_owned_readiness(ledger, changed):
    repo, baseline = prepare(ledger)
    inputs = activation_inputs(ledger)
    readiness = replace(inputs["monitor_ready"](None, inputs["context"]), **changed)
    inputs["monitor_ready"] = lambda c, context: readiness
    with pytest.raises(ValueError, match="monitor-readiness"):
        repo.activate(baseline_id=baseline, **inputs)


@pytest.mark.parametrize(
    "unsafe",
    [
        "global-active",
        "global-missing",
        "pending-intent",
        "unknown-intent",
        "changed-incident",
        "missing-incident",
        "unexpected-reason",
        "missing-review",
        "missing-tests",
    ],
)
def test_activation_refuses_unsafe_ledger_or_unapproved_incidents(ledger, unsafe):
    repo, baseline = prepare(ledger)
    inputs = activation_inputs(ledger)
    dsn, schema, entries, *_ = ledger
    with _connect(dsn, schema) as c:
        if unsafe == "global-active":
            c.execute("UPDATE venue_kill_switches SET active=true WHERE scope='global'")
        elif unsafe == "global-missing":
            c.execute("DELETE FROM venue_kill_switches WHERE scope='global'")
        elif unsafe in {"pending-intent", "unknown-intent"}:
            c.execute(
                """INSERT INTO live_order_intents
                (id,exchange,client_order_id,symbol,side,role,state,requested_qty)
                VALUES (%s,'bitget','new-pending','NEWUSDT','SELL','CLOSE',%s,1)""",
                (uuid4(), "unknown" if unsafe == "unknown-intent" else "requested"),
            )
        elif unsafe == "changed-incident":
            c.execute(
                "UPDATE venue_kill_switches SET updated_at=clock_timestamp() WHERE scope='bitget'"
            )
        elif unsafe == "missing-incident":
            inputs["expected_incidents"] = inputs["expected_incidents"][:1]
        elif unsafe == "unexpected-reason":
            c.execute(
                "UPDATE venue_kill_switches SET reason='new-real-danger' WHERE scope='bitget'"
            )
            inputs["expected_incidents"] = tuple(
                replace(i, reason="new-real-danger") if i.scope == "bitget" else i
                for i in inputs["expected_incidents"]
            )
        elif unsafe == "missing-review":
            inputs["review_reference"] = ""
        elif unsafe == "missing-tests":
            inputs["tests_reference"] = ""
    with pytest.raises(ValueError):
        repo.activate(baseline_id=baseline, **inputs)
    with _connect(dsn, schema) as c:
        assert c.execute(
            "SELECT count(*) FROM bitget_operational_baseline_activations"
        ).fetchone() == (0,)


def test_activation_releases_only_approved_incidents_with_immutable_receipt(ledger):
    repo, baseline = prepare(ledger)
    inputs = activation_inputs(ledger)
    epoch = repo.activate(baseline_id=baseline, **inputs)
    dsn, schema, *_ = ledger
    with _connect(dsn, schema) as c:
        assert c.execute("SELECT count(*) FROM venue_kill_switches WHERE active").fetchone() == (0,)
        assert c.execute(
            "SELECT count(*) FROM bitget_operational_baseline_incident_releases"
        ).fetchone() == (2,)
        c.execute("UPDATE bitget_operational_baseline_activations SET epoch_id=%s", (uuid4(),))
        c.execute("DELETE FROM bitget_operational_baseline_activations")
        assert c.execute(
            "SELECT epoch_id FROM bitget_operational_baseline_activations"
        ).fetchone() == (epoch,)
        assert c.execute("SELECT history_status FROM bitget_operational_baselines").fetchone() == (
            "UNVERIFIED",
        )


def queued_work(ledger, *, attempts=0, management_state="queued", marker=False):
    from source_freshness_fixtures import eligible_dispatch_source

    dsn, schema, *_ = ledger
    dispatch, management = uuid4(), uuid4()
    with _connect(dsn, schema) as c:
        c.execute(
            """INSERT INTO dispatches
            (id,source_type,source_id,revision,exchange,state,attempts)
            VALUES (%s,'fixture',%s,%s,'bitget','QUEUED',%s)""",
            (dispatch, uuid4(), "b" * 64, attempts),
        )
        eligible_dispatch_source(c, dispatch)
        message = c.execute(
            "SELECT message_id FROM canonical_signals s JOIN dispatches d "
            "ON d.source_id=s.id WHERE d.id=%s",
            (dispatch,),
        ).fetchone()[0]
        c.execute(
            """INSERT INTO source_management_updates
            (id,source_message_id,revision,symbol,action,state)
            VALUES (%s,%s,%s,'OLD0USDT','TP1_BOOKED',%s)""",
            (management, message, "b" * 64, management_state),
        )
        if marker:
            c.execute(
                "INSERT INTO source_management_provider_intents VALUES (%s,%s,now())",
                (management, f"source-management-{management.hex}-close"),
            )
    return dispatch, management


def test_activation_audit_terminalizes_unsent_queues_without_replaying_history(ledger):
    repo, baseline = prepare(ledger)
    inputs = activation_inputs(ledger)
    dispatch, management = queued_work(ledger)
    dsn, schema, *_ = ledger
    with _connect(dsn, schema) as c:
        pending_id = uuid4()
        message = c.execute(
            "SELECT source_message_id FROM source_management_updates WHERE id=%s", (management,)
        ).fetchone()[0]
        c.execute(
            """INSERT INTO source_management_updates
            (id,source_message_id,revision,symbol,action,state) VALUES
            (%s,%s,%s,'PENGUUSDT','TP1_BOOKED','reconciliation-pending')""",
            (pending_id, message, "c" * 64),
        )
        c.execute(
            "INSERT INTO source_management_provider_intents VALUES (%s,%s,now())",
            (pending_id, f"source-management-{pending_id.hex}-close"),
        )
    repo.activate(baseline_id=baseline, **inputs)
    with _connect(dsn, schema) as c:
        assert c.execute(
            "SELECT state,attempts FROM dispatches WHERE id=%s", (dispatch,)
        ).fetchone() == ("EXPIRED", 0)
        assert c.execute(
            "SELECT to_state,reason FROM dispatch_transitions WHERE dispatch_id=%s", (dispatch,)
        ).fetchone() == ("EXPIRED", "operational-baseline-cutoff")
        assert c.execute(
            "SELECT state FROM source_management_updates WHERE id=%s", (management,)
        ).fetchone() == ("failed",)
        assert c.execute(
            "SELECT state FROM source_management_updates WHERE id=%s", (pending_id,)
        ).fetchone() == ("reconciliation-pending",)
        assert c.execute("SELECT count(*) FROM source_management_provider_intents").fetchone() == (
            1,
        )
        assert c.execute(
            "SELECT count(*) FROM bitget_operational_baseline_queue_retirements"
        ).fetchone() == (2,)


@pytest.mark.parametrize("unsafe", ["entry-attempt", "management-marker"])
def test_activation_never_terminalizes_ambiguous_queued_work(ledger, unsafe):
    repo, baseline = prepare(ledger)
    inputs = activation_inputs(ledger)
    dispatch, management = queued_work(
        ledger,
        attempts=int(unsafe == "entry-attempt"),
        management_state="claimed",
        marker=unsafe == "management-marker",
    )
    with pytest.raises(ValueError, match="ambiguous-prebaseline"):
        repo.activate(baseline_id=baseline, **inputs)
    dsn, schema, *_ = ledger
    with _connect(dsn, schema) as c:
        assert c.execute("SELECT state FROM dispatches WHERE id=%s", (dispatch,)).fetchone() == (
            "QUEUED",
        )
        assert c.execute(
            "SELECT state FROM source_management_updates WHERE id=%s", (management,)
        ).fetchone() == ("claimed",)


def test_active_baseline_filters_only_bound_unchanged_old_recovery_vetoes(ledger):
    from fatty_trader.execution.bitget_dispatch_repository import PostgresBitgetDispatchRepository

    dsn, schema, entries, *_ = ledger
    repo, baseline = prepare(ledger)
    inputs = activation_inputs(ledger)
    dispatch = PostgresBitgetDispatchRepository(
        lambda: _connect(dsn, schema), baseline_context=inputs["context"]
    )
    assert dispatch.inventory_issues("LIVE") == ["orphan-entry-intent"]
    assert dispatch.unresolved_protection_issues() == ["unresolved-protection"]
    repo.activate(baseline_id=baseline, **inputs)
    assert dispatch.inventory_issues("LIVE") == []
    assert dispatch.unresolved_protection_issues() == []
    assert dispatch.recovery_candidates() == []
    unbound = PostgresBitgetDispatchRepository(lambda: _connect(dsn, schema))
    assert unbound.inventory_issues("LIVE") == ["orphan-entry-intent"]
    assert unbound.unresolved_protection_issues() == ["unresolved-protection"]
    assert dispatch.inventory_issues("DEMO") == ["orphan-entry-intent"]
    with _connect(dsn, schema) as c:
        c.execute(
            """INSERT INTO live_order_intents
            (id,exchange,client_order_id,symbol,side,role,state,requested_qty)
            VALUES (%s,'bitget','new-unknown','NEWUSDT','BUY','ENTRY','unknown',1)""",
            (uuid4(),),
        )
    assert dispatch.inventory_issues("LIVE") == ["orphan-entry-intent"]
    with _connect(dsn, schema) as c:
        c.execute("UPDATE live_order_intents SET fee=0.12345 WHERE id=%s", (entries[0],))
    assert dispatch.unresolved_protection_issues() == ["unresolved-protection"]
    with pytest.raises(ValueError, match="canonical source missing"):
        dispatch.recovery_candidates()


def reserve_new(ledger, context=None, *, environment="LIVE"):
    from source_freshness_fixtures import eligible_dispatch_source

    from fatty_trader.storage.balance_reservations import PostgresBitgetMarginReservationRepository

    dsn, schema, *_ = ledger
    dispatch = uuid4()
    with _connect(dsn, schema) as c:
        c.execute(
            """INSERT INTO dispatches
            (id,source_type,source_id,revision,exchange,state) VALUES
            (%s,'fixture',%s,%s,'bitget','QUEUED')""",
            (dispatch, uuid4(), "d" * 64),
        )
        eligible_dispatch_source(c, dispatch)
    repo = PostgresBitgetMarginReservationRepository(
        lambda: _connect(dsn, schema), baseline_context=context
    )
    return repo.reserve(
        exchange="bitget",
        dispatch_id=dispatch,
        client_order_id=f"new-{dispatch}",
        total_balance=Decimal(1),
        available_balance=Decimal(1),
        equity=Decimal(1),
        margin_coin="USDT",
        observed_at=datetime.now(UTC),
        planned_margin_usdt=Decimal("0.4"),
        headroom=Decimal("0.9"),
        ttl=timedelta(minutes=5),
        max_margin_per_trade_usdt=Decimal(1),
        symbol="NEWUSDT",
        environment=environment,
        max_positions=10,
        provider_active_symbols=(),
    )


def test_baseline_frees_only_current_epoch_margin_without_changing_consumed_truth(ledger):
    repo, baseline = prepare(ledger)
    inputs = activation_inputs(ledger)
    before = reserve_new(ledger, inputs["context"])
    assert before.accepted is False
    assert before.reason == "legacy-unowned-reservation"
    repo.activate(baseline_id=baseline, **inputs)
    assert reserve_new(ledger).reason == "legacy-unowned-reservation"
    assert (
        reserve_new(ledger, inputs["context"], environment="DEMO").reason
        == "legacy-unowned-reservation"
    )
    after = reserve_new(ledger, inputs["context"])
    assert after.accepted is True
    # New reservation ownership still vetoes a second ENTRY at the same symbol.
    assert reserve_new(ledger, inputs["context"]).reason == "symbol-already-owned"
    dsn, schema, *_ = ledger
    with _connect(dsn, schema) as c:
        assert c.execute(
            "SELECT count(*) FROM bitget_margin_reservations WHERE state='consumed'"
        ).fetchone() == (4,)


def test_historical_filled_reads_never_reconsume_or_change_active_baseline_receipt(ledger):
    from fatty_trader.storage.balance_reservations import PostgresBitgetMarginReservationRepository
    from fatty_trader.storage.operational_baseline import set_baseline_context

    repo, baseline = prepare(ledger)
    inputs = activation_inputs(ledger)
    repo.activate(baseline_id=baseline, **inputs)
    dsn, schema, _, reservations, _ = ledger
    with _connect(dsn, schema) as c:
        old = c.execute(
            "SELECT to_jsonb(m) FROM bitget_margin_reservations m ORDER BY id"
        ).fetchall()
    margins = PostgresBitgetMarginReservationRepository(
        lambda: _connect(dsn, schema), baseline_context=inputs["context"]
    )
    for reservation in reservations:
        margins.resolve(reservation, "FILLED")
    with _connect(dsn, schema) as c:
        assert (
            c.execute("SELECT to_jsonb(m) FROM bitget_margin_reservations m ORDER BY id").fetchall()
            == old
        )
        set_baseline_context(c.cursor(), inputs["context"])
        assert c.execute(
            "SELECT count(*) FROM bitget_operational_baseline_exclusions"
        ).fetchone() == (25,)


def test_active_epoch_cutoff_never_replays_still_fresh_prebaseline_source(ledger):
    from fatty_trader.execution.bitget_dispatch_repository import PostgresBitgetDispatchRepository

    repo, baseline = prepare(ledger)
    inputs = activation_inputs(ledger)
    repo.activate(baseline_id=baseline, **inputs)
    dispatch, _ = queued_work(ledger)
    dsn, schema, *_ = ledger
    with _connect(dsn, schema) as c:
        c.execute(
            "UPDATE telegram_messages SET received_at="
            "(SELECT source_cutoff FROM bitget_operational_baseline_activations) "
            "-interval '1 second'"
        )
    dispatch_repo = PostgresBitgetDispatchRepository(lambda: _connect(dsn, schema))
    assert dispatch_repo.entry_source_eligible(dispatch) is False
    assert dispatch_repo.claim("test-worker", 30) is None
    with _connect(dsn, schema) as c:
        assert c.execute(
            "SELECT state,attempts FROM dispatches WHERE id=%s", (dispatch,)
        ).fetchone() == ("EXPIRED", 0)
    # A new source remains eligible under the unchanged ordinary TTL rules.
    new_dispatch, _ = queued_work(ledger)
    assert dispatch_repo.entry_source_eligible(new_dispatch) is True


@pytest.mark.parametrize("initial", [None, True])
def test_prepare_initializes_only_absent_global_switch_and_audits_creation(ledger, initial):
    dsn, schema, *_ = ledger
    with _connect(dsn, schema) as c:
        if initial is None:
            c.execute("DELETE FROM venue_kill_switches WHERE scope='global'")
        else:
            c.execute(
                "UPDATE venue_kill_switches SET active=true,reason='real-global-danger' "
                "WHERE scope='global'"
            )
    _, baseline = prepare(ledger)
    with _connect(dsn, schema) as c:
        assert c.execute(
            "SELECT active FROM venue_kill_switches WHERE scope='global'"
        ).fetchone() == (initial is True,)
        assert c.execute(
            "SELECT created_global_inactive FROM bitget_operational_baselines WHERE id=%s",
            (baseline,),
        ).fetchone() == (initial is None,)


def test_none_context_explicitly_clears_previously_bound_transaction_context(ledger):
    from fatty_trader.storage.operational_baseline import set_baseline_context

    repo, baseline = prepare(ledger)
    inputs = activation_inputs(ledger)
    repo.activate(baseline_id=baseline, **inputs)
    dsn, schema, *_ = ledger
    with _connect(dsn, schema) as c:
        set_baseline_context(c.cursor(), inputs["context"])
        assert c.execute(
            "SELECT count(*) FROM bitget_operational_baseline_exclusions"
        ).fetchone() == (25,)
        set_baseline_context(c.cursor(), None)
        assert c.execute(
            "SELECT count(*) FROM bitget_operational_baseline_exclusions"
        ).fetchone() == (0,)


def test_activation_expiration_emits_deduplicated_outbox_notification(ledger):
    repo, baseline = prepare(ledger)
    inputs = activation_inputs(ledger)
    dispatch, _ = queued_work(ledger)
    repo.activate(baseline_id=baseline, **inputs)
    dsn, schema, *_ = ledger
    with _connect(dsn, schema) as c:
        rows = c.execute("SELECT dedup_key,payload FROM notifications_outbox").fetchall()
        assert len(rows) == 1
        assert (
            rows[0][0]
            == f"dispatch-transition:{dispatch}:QUEUED:EXPIRED:operational-baseline-cutoff"
        )
        assert rows[0][1]["dispatch_id"] == str(dispatch)
        assert rows[0][1]["to_state"] == "EXPIRED"


def test_prepare_preserves_sql_numeric_json_exactly_through_activation(ledger):
    from fatty_trader.storage.operational_baseline import set_baseline_context

    dsn, schema, entries, reservations, _ = ledger
    exact = Decimal("5.4948119636363636363636363636")
    with _connect(dsn, schema) as c:
        c.execute("UPDATE live_order_intents SET fee=%s WHERE id=%s", (exact, entries[0]))
        c.execute(
            "UPDATE bitget_margin_reservations SET planned_margin_usdt=%s WHERE id=%s",
            (exact, reservations[0]),
        )
    repo, baseline = prepare(ledger)
    with _connect(dsn, schema) as c:
        assert c.execute(
            "SELECT (snapshot->>'fee')::numeric FROM "
            "bitget_operational_baseline_rows WHERE row_id=%s",
            (entries[0],),
        ).fetchone() == (exact,)
        assert c.execute(
            "SELECT (snapshot->>'planned_margin_usdt')::numeric FROM "
            "bitget_operational_baseline_rows WHERE row_id=%s",
            (reservations[0],),
        ).fetchone() == (exact,)
    inputs = activation_inputs(ledger)
    repo.activate(baseline_id=baseline, **inputs)
    with _connect(dsn, schema) as c:
        set_baseline_context(c.cursor(), inputs["context"])
        assert c.execute(
            "SELECT count(*) FROM bitget_operational_baseline_exclusions"
        ).fetchone() == (25,)
        c.execute(
            "UPDATE live_order_intents SET fee=%s WHERE id=%s",
            (Decimal("5.4948119636363636363636363637"), entries[0]),
        )
        assert c.execute(
            "SELECT count(*) FROM bitget_operational_baseline_exclusions"
        ).fetchone() == (0,)


@pytest.mark.parametrize("repository", ["dispatch", "margin"])
def test_public_context_binding_validates_type_and_can_clear_binding(ledger, repository):
    from fatty_trader.execution.bitget_dispatch_repository import PostgresBitgetDispatchRepository
    from fatty_trader.storage.balance_reservations import PostgresBitgetMarginReservationRepository
    from fatty_trader.storage.operational_baseline import BaselineContext

    dsn, schema, *_ = ledger
    cls = (
        PostgresBitgetDispatchRepository
        if repository == "dispatch"
        else PostgresBitgetMarginReservationRepository
    )
    repo = cls(lambda: _connect(dsn, schema))
    context = BaselineContext("authenticated-uid-7", "a" * 64)
    repo.bind_baseline_context(context)
    assert repo._baseline_context == context
    with pytest.raises(TypeError, match="BaselineContext"):
        repo.bind_baseline_context({"account_identity": "forged"})
    assert repo._baseline_context == context
    repo.bind_baseline_context(None)
    assert repo._baseline_context is None
    with pytest.raises(TypeError, match="BaselineContext"):
        cls(lambda: _connect(dsn, schema), baseline_context="forged")


def test_activation_reages_provider_proof_after_final_audit_writes(ledger):
    repo, baseline = prepare(ledger)
    inputs = activation_inputs(ledger)
    fresh_proof = inputs["collect_proof"]
    inputs["collect_proof"] = lambda: replace(
        fresh_proof(), observed_at=datetime.now(UTC) - timedelta(seconds=29)
    )
    dsn, schema, *_ = ledger
    with _connect(dsn, schema) as c:
        c.execute(
            "CREATE FUNCTION delay_activation() RETURNS trigger LANGUAGE plpgsql AS "
            "$$ BEGIN PERFORM pg_sleep(2); RETURN NEW; END $$"
        )
        c.execute(
            "CREATE TRIGGER delay_activation BEFORE INSERT ON "
            "bitget_operational_baseline_activations FOR EACH ROW "
            "EXECUTE FUNCTION delay_activation()"
        )
    with pytest.raises(ValueError, match="stale-provider-proof"):
        repo.activate(baseline_id=baseline, **inputs)
    with _connect(dsn, schema) as c:
        assert c.execute(
            "SELECT count(*) FROM bitget_operational_baseline_activations"
        ).fetchone() == (0,)
        assert c.execute("SELECT count(*) FROM venue_kill_switches WHERE active").fetchone() == (2,)


def test_activation_collects_proof_only_after_audit_table_locks(ledger):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic, sleep

    repo, baseline = prepare(ledger)
    inputs = activation_inputs(ledger)
    dsn, schema, *_ = ledger
    called = Event()
    collect = inputs["collect_proof"]

    def proof():
        called.set()
        return collect()

    inputs["collect_proof"] = proof
    with _connect(dsn, schema) as blocker, _connect(dsn, schema) as observer:
        # pg_stat_activity snapshots are transaction-cached; each poll must refresh.
        observer.autocommit = True
        blocker.execute("LOCK TABLE notifications_outbox IN ACCESS EXCLUSIVE MODE")
        pid = blocker.info.backend_pid
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(repo.activate, baseline_id=baseline, **inputs)
            try:
                deadline = monotonic() + 5
                waited = False
                while monotonic() < deadline:
                    waited = observer.execute(
                        "SELECT EXISTS(SELECT 1 FROM pg_stat_activity "
                        "WHERE %s=ANY(pg_blocking_pids(pid)))",
                        (pid,),
                    ).fetchone()[0]
                    if waited or result.done():
                        break
                    sleep(0.01)
                assert waited, "activation must fence audit writers before collecting proof"
                assert not called.is_set()
            finally:
                blocker.commit()
            result.result(timeout=10)
    assert called.is_set()


@pytest.mark.parametrize("kind", ["intent", "reservation", "dispatch"])
def test_activation_rejects_exact_changed_capture_before_collecting_proof(ledger, kind):
    repo, baseline = prepare(ledger)
    inputs = activation_inputs(ledger)
    dsn, schema, entries, reservations, dispatches = ledger
    with _connect(dsn, schema) as c:
        if kind == "intent":
            c.execute(
                "UPDATE live_order_intents SET fee=%s WHERE id=%s",
                (Decimal("0.0000000000000000000000000001"), entries[0]),
            )
        elif kind == "reservation":
            c.execute(
                "UPDATE bitget_margin_reservations SET planned_margin_usdt=%s WHERE id=%s",
                (Decimal("1.0000000000000000000000000001"), reservations[0]),
            )
        else:
            c.execute(
                "UPDATE dispatches SET terminal_reason='new-danger' WHERE id=%s", (dispatches[0],)
            )

    def forbidden():
        pytest.fail("changed capture must be rejected before proof collection")

    inputs["collect_proof"] = forbidden
    with pytest.raises(ValueError, match="captured-rows-changed"):
        repo.activate(baseline_id=baseline, **inputs)


def activation_inputs(ledger):
    from fatty_trader.storage.operational_baseline import (
        BaselineContext,
        FlatAccountProof,
        IncidentLatch,
        MonitorReadinessProof,
    )

    dsn, schema, _, _, _ = ledger
    context = BaselineContext("authenticated-uid-7", "a" * 64)
    with _connect(dsn, schema) as c:
        c.execute(
            "INSERT INTO venue_kill_switches(scope,active,reason) VALUES "
            "('bitget',true,'clock-skew-exceeded'),"
            "('bitget-protection-stream',true,'socket-not-connected')"
        )
        incidents = tuple(
            IncidentLatch(*r)
            for r in c.execute(
                "SELECT scope,reason,latched_at,updated_at FROM venue_kill_switches "
                "WHERE scope <> 'global' ORDER BY scope"
            ).fetchall()
        )

    def proof():
        return FlatAccountProof(context, "LIVE", True, datetime.now(UTC), 0, 0, 0, True, True)

    def monitor(cursor, supplied_context):
        assert supplied_context == context
        now = datetime.now(UTC)
        return MonitorReadinessProof(context, str(uuid4()), now, now, now, True, True, True)

    return dict(
        context=context,
        collect_proof=proof,
        monitor_ready=monitor,
        expected_incidents=incidents,
        tests_reference="full-suite-ref",
        review_reference="independent-review-ref",
    )


def test_active_baseline_requires_bound_context_and_unchanged_semantics(ledger):
    from fatty_trader.storage.operational_baseline import BaselineContext, set_baseline_context

    dsn, schema, entries, reservations, dispatches = ledger
    repo, baseline = prepare(ledger)
    inputs = activation_inputs(ledger)
    epoch = repo.activate(baseline_id=baseline, **inputs)
    with _connect(dsn, schema) as c:
        assert c.execute(
            "SELECT count(*) FROM bitget_operational_baseline_exclusions"
        ).fetchone() == (0,)
        set_baseline_context(c.cursor(), inputs["context"])
        assert c.execute(
            "SELECT count(*) FROM bitget_operational_baseline_exclusions"
        ).fetchone() == (25,)
        assert c.execute(
            "SELECT epoch_id FROM bitget_operational_baseline_activations"
        ).fetchone() == (epoch,)
        c.execute(
            "UPDATE live_order_intents SET updated_at=clock_timestamp() WHERE id=%s", (entries[0],)
        )
        assert c.execute(
            "SELECT count(*) FROM bitget_operational_baseline_exclusions"
        ).fetchone() == (25,)
        set_baseline_context(c.cursor(), BaselineContext("authenticated-uid-7", "b" * 64))
        assert c.execute(
            "SELECT count(*) FROM bitget_operational_baseline_exclusions"
        ).fetchone() == (0,)
        set_baseline_context(c.cursor(), inputs["context"])
        c.execute("UPDATE live_order_intents SET filled_qty=2 WHERE id=%s", (entries[0],))
        assert c.execute(
            "SELECT count(*) FROM bitget_operational_baseline_exclusions"
        ).fetchone() == (0,)
        assert c.execute(
            "SELECT count(*) FROM live_order_intents WHERE state='filled'"
        ).fetchone() == (17,)
        assert c.execute(
            "SELECT count(*) FROM bitget_margin_reservations WHERE state='consumed'"
        ).fetchone() == (4,)
