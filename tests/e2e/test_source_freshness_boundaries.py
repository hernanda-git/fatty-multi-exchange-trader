"""Real PostgreSQL source-age boundaries; offline fake analysis/venue only."""

import time
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from test_intake_coverage_postgres import CHANNEL, message, setup
from test_postgres_migration_and_margin_admission import postgres_schema as postgres_schema

from fatty_trader.analyzer.codex_runner import CodexRunResult
from fatty_trader.analyzer.postgres_worker import process_received_batch
from fatty_trader.intake.persistence import PostgresRawMessageRepository
from fatty_trader.intake.telegram import TelegramIntake


def seed(factory):
    TelegramIntake(PostgresRawMessageRepository(factory)).ingest(
        channel_id=CHANNEL, message=message(101, "#BTC LONG ENTRY: 100 TARGET: 110 STOPLOSS: 90")
    )


def queued(factory):
    seed(factory)
    assert (
        process_received_batch(
            factory,
            runner=lambda _: CodexRunResult(False, True, False, 1, "offline", "", ""),
            exchanges=("bitget",),
        )
        == 1
    )
    with factory() as c:
        return c.execute("SELECT id FROM dispatches").fetchone()[0]


@pytest.mark.parametrize("invalid", ["aged", "missing", "future", "legacy", "infinite"])
def test_claim_retires_unsafe_source_with_audit(postgres_schema, invalid):
    from fatty_trader.execution.bitget_dispatch_repository import PostgresBitgetDispatchRepository

    factory = setup(postgres_schema)
    dispatch_id = queued(factory)
    updates = {
        "aged": "entry_expires_at=clock_timestamp()-interval '1 second'",
        "missing": "entry_expires_at=NULL",
        "future": "received_at=clock_timestamp()+interval '1 day'",
        "legacy": "ingestion_origin='legacy-unknown'",
        "infinite": "entry_expires_at='infinity'",
    }
    with factory() as c:
        c.execute("UPDATE telegram_messages SET " + updates[invalid])
    assert PostgresBitgetDispatchRepository(factory).claim("offline", 30) is None
    with factory() as c:
        assert c.execute(
            "SELECT state,terminal_reason,attempts FROM dispatches WHERE id=%s", (dispatch_id,)
        ).fetchone() == ("EXPIRED", "stale-source-message", 0)
        assert c.execute(
            "SELECT from_state,to_state,reason FROM dispatch_transitions WHERE dispatch_id=%s",
            (dispatch_id,),
        ).fetchall() == [("QUEUED", "EXPIRED", "stale-source-message")]
        assert c.execute(
            "SELECT count(*) FROM notifications_outbox WHERE payload->>'to_state'='EXPIRED'"
        ).fetchone() == (1,)


def reserve(factory, dispatch_id, client_oid="offline-entry"):
    from datetime import timedelta

    from fatty_trader.storage.balance_reservations import PostgresBitgetMarginReservationRepository

    return PostgresBitgetMarginReservationRepository(factory).reserve(
        exchange="bitget",
        dispatch_id=dispatch_id,
        client_order_id=client_oid,
        total_balance=Decimal("10"),
        available_balance=Decimal("10"),
        equity=Decimal("10"),
        margin_coin="USDT",
        observed_at=datetime.now(UTC),
        planned_margin_usdt=Decimal("1"),
        headroom=Decimal("1"),
        ttl=timedelta(seconds=30),
        max_margin_per_trade_usdt=Decimal("1"),
        symbol="BTCUSDT",
        environment="DEMO",
        max_positions=3,
        provider_active_symbols=(),
    )


def test_margin_admission_checks_source_after_claim(postgres_schema):
    from fatty_trader.execution.bitget_dispatch_repository import PostgresBitgetDispatchRepository

    factory = setup(postgres_schema)
    dispatch_id = queued(factory)
    assert PostgresBitgetDispatchRepository(factory).claim("offline", 30) is not None
    with factory() as c:
        c.execute(
            "UPDATE telegram_messages SET entry_expires_at=clock_timestamp()-interval '1 second'"
        )
    result = reserve(factory, dispatch_id)
    assert not result.accepted
    assert result.reason == "stale-source-message"
    with factory() as c:
        assert c.execute("SELECT count(*) FROM balance_snapshots").fetchone() == (0,)
        assert c.execute("SELECT count(*) FROM bitget_margin_reservations").fetchone() == (0,)
        assert c.execute("SELECT state,terminal_reason FROM dispatches").fetchone() == (
            "EXPIRED",
            "stale-source-message",
        )
        assert c.execute(
            "SELECT count(*) FROM dispatch_transitions WHERE to_state='EXPIRED'"
        ).fetchone() == (1,)


@pytest.mark.asyncio
async def test_entry_mutation_rechecks_source_after_admission(postgres_schema):
    from fatty_trader.exchanges.bitget.live import InMemoryLiveIntentStore, LiveOrderStatus
    from fatty_trader.execution.bitget_dispatch_execution import BitgetDispatchExecution
    from fatty_trader.execution.bitget_dispatch_repository import PostgresBitgetDispatchRepository
    from tests.unit.test_bitget_dispatch_execution_adapter import Execution, _result, _submission

    factory = setup(postgres_schema)
    dispatch_id = queued(factory)
    repository = PostgresBitgetDispatchRepository(factory)
    dispatch = repository.claim("offline", 30)
    assert reserve(factory, dispatch_id).accepted
    repository.transition(dispatch_id, expected_state="QUEUED", target_state="SUBMITTING")
    with factory() as c:
        c.execute(
            "UPDATE telegram_messages SET entry_expires_at=clock_timestamp()-interval '1 second'"
        )
    from dataclasses import replace

    execution = Execution(
        result=replace(
            _result(LiveOrderStatus.ACCEPTED),
            client_oid=BitgetDispatchExecution.client_oid(dispatch),
        )
    )
    store = InMemoryLiveIntentStore()
    adapter = BitgetDispatchExecution(execution, store, dispatch_repository=repository)
    assert await adapter.submit_entry(dispatch, _submission()) == "EXPIRED"
    assert execution.submit_calls == []
    assert store.get(adapter.client_oid(dispatch)) is None
    with factory() as c:
        assert c.execute("SELECT state FROM dispatches WHERE id=%s", (dispatch_id,)).fetchone() == (
            "EXPIRED",
        )
        assert c.execute("SELECT state FROM bitget_margin_reservations").fetchone() == ("released",)


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["admission", "mutation"])
async def test_dispatcher_handles_expiry_without_unknown_or_duplicate_transition(
    postgres_schema, boundary
):
    from fatty_trader.exchanges.bitget.live import InMemoryLiveIntentStore, LiveOrderStatus
    from fatty_trader.execution.bitget_dispatch_execution import BitgetDispatchExecution
    from fatty_trader.execution.bitget_dispatch_repository import PostgresBitgetDispatchRepository
    from fatty_trader.execution.bitget_dispatcher import (
        BitgetAdmission,
        BitgetDispatcher,
        DispatchGate,
    )
    from tests.unit.test_bitget_dispatch_execution_adapter import Execution, _result, _submission

    factory = setup(postgres_schema)
    dispatch_id = queued(factory)
    repository = PostgresBitgetDispatchRepository(factory)
    execution = Execution(result=_result(LiveOrderStatus.ACCEPTED))
    adapter = BitgetDispatchExecution(
        execution, InMemoryLiveIntentStore(), dispatch_repository=repository
    )

    def expire():
        with factory() as c:
            c.execute(
                "UPDATE telegram_messages SET entry_expires_at=clock_timestamp()-interv"
                "al '1 second'"
            )

    def preflight(dispatch):
        if boundary == "admission":
            expire()
        result = reserve(factory, dispatch.id)
        if not result.accepted:
            from fatty_trader.intake.freshness import SourceFreshnessExpired

            raise SourceFreshnessExpired(result.reason)
        expire()
        return BitgetAdmission(_submission())

    dispatcher = BitgetDispatcher(
        repository,
        gate=DispatchGate(execution_enabled=True),
        execution=adapter,
        preflight=preflight,
    )
    assert await dispatcher.run_once("offline", 30) == "expired"
    assert execution.submit_calls == []
    with factory() as c:
        assert c.execute("SELECT state FROM dispatches WHERE id=%s", (dispatch_id,)).fetchone() == (
            "EXPIRED",
        )
        assert c.execute(
            "SELECT count(*) FROM dispatch_transitions WHERE to_state='EXPIRED'"
        ).fetchone() == (1,)
        assert c.execute(
            "SELECT count(*) FROM dispatch_transitions WHERE to_state='UNKNOWN'"
        ).fetchone() == (0,)


def test_slow_analysis_expires_before_canonical_fanout(postgres_schema):
    factory = setup(postgres_schema)
    seed(factory)
    with factory() as c:
        c.execute(
            "UPDATE telegram_messages SET entry_expires_at=clock_timestamp()+interval '0.15 second'"
        )

    def slow(prompt):
        time.sleep(0.2)
        return CodexRunResult(False, True, False, 1, "offline", "", "offline")

    assert process_received_batch(factory, runner=slow) == 1
    with factory() as c:
        assert c.execute("SELECT count(*) FROM dispatches").fetchone() == (0,)
        assert c.execute("SELECT count(*) FROM canonical_signals").fetchone() == (0,)
        assert c.execute(
            "SELECT intake_state,entry_rejection_reason FROM telegram_messages"
        ).fetchone() == ("EXPIRED", "stale-source-message")


@pytest.mark.asyncio
async def test_source_ageing_during_durable_claim_blocks_final_post(postgres_schema):
    from dataclasses import replace

    from fatty_trader.exchanges.bitget.live import LiveOrderStatus
    from fatty_trader.execution.bitget_dispatch_execution import BitgetDispatchExecution
    from fatty_trader.execution.bitget_dispatch_repository import PostgresBitgetDispatchRepository
    from fatty_trader.storage.live_intents import PostgresLiveIntentStore
    from tests.unit.test_bitget_dispatch_execution_adapter import Execution, _result, _submission

    factory = setup(postgres_schema)
    dispatch_id = queued(factory)
    repository = PostgresBitgetDispatchRepository(factory)
    dispatch = repository.claim("offline", 30)
    assert dispatch is not None
    dispatch = replace(dispatch, pair_token="BTCUSDT")
    admission = reserve(factory, dispatch_id, BitgetDispatchExecution.client_oid(dispatch))
    assert admission.accepted
    with factory() as c:
        reservation_id, snapshot_id = c.execute(
            "SELECT id,balance_snapshot_id FROM bitget_margin_reservations"
        ).fetchone()
    submission = replace(
        _submission(),
        margin_reservation_id=reservation_id,
        balance_snapshot_id=snapshot_id,
        planned_margin_usdt=Decimal("1"),
    )
    repository.transition(dispatch_id, expected_state="QUEUED", target_state="SUBMITTING")

    class SlowClaimStore(PostgresLiveIntentStore):
        def claim(self, record):
            claimed = super().claim(record)
            with factory() as c:
                c.execute(
                    "UPDATE telegram_messages SET "
                    "received_at=clock_timestamp()-interval '6 minutes'"
                )
            return claimed

    store = SlowClaimStore(factory)
    execution = Execution(
        result=replace(
            _result(LiveOrderStatus.ACCEPTED),
            client_oid=BitgetDispatchExecution.client_oid(dispatch),
        )
    )
    adapter = BitgetDispatchExecution(execution, store, dispatch_repository=repository)
    assert await adapter.submit_entry(dispatch, submission) == "EXPIRED"
    assert execution.submit_calls == []
    with factory() as c:
        assert c.execute("SELECT state FROM dispatches").fetchone() == ("EXPIRED",)
        assert c.execute("SELECT state FROM bitget_margin_reservations").fetchone() == ("released",)
        assert c.execute("SELECT state FROM live_order_intents").fetchone() == ("rejected",)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["requested", "unknown"])
async def test_stale_ambiguous_entry_retains_dispatch_and_reservation(postgres_schema, state):
    from dataclasses import replace

    from fatty_trader.execution.bitget_dispatch_execution import BitgetDispatchExecution
    from fatty_trader.execution.bitget_dispatch_repository import PostgresBitgetDispatchRepository
    from fatty_trader.storage.live_intents import PostgresLiveIntentStore
    from tests.unit.test_bitget_dispatch_execution_adapter import _submission

    factory = setup(postgres_schema)
    dispatch_id = queued(factory)
    repository = PostgresBitgetDispatchRepository(factory)
    dispatch = repository.claim("offline", 30)
    assert dispatch is not None
    dispatch = replace(dispatch, pair_token="BTCUSDT")
    assert reserve(factory, dispatch_id, BitgetDispatchExecution.client_oid(dispatch)).accepted
    with factory() as c:
        reservation_id, snapshot_id = c.execute(
            "SELECT id,balance_snapshot_id FROM bitget_margin_reservations"
        ).fetchone()
    submission = replace(
        _submission(),
        margin_reservation_id=reservation_id,
        balance_snapshot_id=snapshot_id,
        planned_margin_usdt=Decimal("1"),
    )
    intent = BitgetDispatchExecution._intent(dispatch, submission)
    intent.state = state
    assert PostgresLiveIntentStore(factory).claim(intent)
    repository.transition(dispatch_id, expected_state="QUEUED", target_state="SUBMITTING")
    with factory() as c:
        c.execute("UPDATE telegram_messages SET received_at=clock_timestamp()-interval '6 minutes'")
    assert not repository.entry_source_eligible(dispatch_id)
    with factory() as c:
        assert c.execute("SELECT state FROM dispatches").fetchone() == ("SUBMITTING",)
        assert c.execute("SELECT state FROM bitget_margin_reservations").fetchone() == ("reserved",)
        assert c.execute("SELECT state FROM live_order_intents").fetchone() == (state,)
        assert c.execute(
            "SELECT count(*) FROM dispatch_transitions WHERE to_state='EXPIRED'"
        ).fetchone() == (0,)
    from fatty_trader.exchanges.bitget.live import LiveOrderStatus
    from fatty_trader.storage.balance_reservations import PostgresBitgetMarginReservationRepository
    from tests.unit.test_bitget_dispatch_execution_adapter import Execution, _result

    execution = Execution(
        result=replace(_result(LiveOrderStatus.UNKNOWN), client_oid=intent.client_oid)
    )
    adapter = BitgetDispatchExecution(
        execution,
        PostgresLiveIntentStore(factory),
        dispatch_repository=repository,
        reservation_repository=PostgresBitgetMarginReservationRepository(factory),
    )
    assert await adapter.submit_entry(dispatch, submission) == "UNKNOWN"
    assert execution.submit_calls == []
    assert execution.reconcile_calls == [intent.client_oid]
    with factory() as c:
        assert c.execute("SELECT state FROM dispatches").fetchone() == ("SUBMITTING",)
        assert c.execute("SELECT state FROM bitget_margin_reservations").fetchone() == ("unknown",)


@pytest.mark.asyncio
async def test_durable_recovery_veto_appearing_during_claim_blocks_final_post(postgres_schema):
    from dataclasses import replace

    from fatty_trader.exchanges.bitget.live import LiveOrderStatus
    from fatty_trader.execution.bitget_dispatch_execution import BitgetDispatchExecution
    from fatty_trader.execution.bitget_dispatch_repository import PostgresBitgetDispatchRepository
    from fatty_trader.storage.balance_reservations import PostgresBitgetMarginReservationRepository
    from fatty_trader.storage.live_intents import PostgresLiveIntentStore
    from tests.unit.test_bitget_dispatch_execution_adapter import Execution, _result, _submission

    factory = setup(postgres_schema)
    dispatch_id = queued(factory)
    repository = PostgresBitgetDispatchRepository(factory)
    dispatch = repository.claim("offline", 30)
    assert dispatch is not None
    dispatch = replace(dispatch, pair_token="BTCUSDT")
    assert reserve(factory, dispatch_id, BitgetDispatchExecution.client_oid(dispatch)).accepted
    with factory() as c:
        reservation_id, snapshot_id = c.execute(
            "SELECT id,balance_snapshot_id FROM bitget_margin_reservations"
        ).fetchone()
    submission = replace(
        _submission(),
        margin_reservation_id=reservation_id,
        balance_snapshot_id=snapshot_id,
        planned_margin_usdt=Decimal("1"),
    )
    repository.transition(dispatch_id, expected_state="QUEUED", target_state="SUBMITTING")

    class VetoClaimStore(PostgresLiveIntentStore):
        def claim(self, record):
            claimed = super().claim(record)
            with factory() as c:
                c.execute("UPDATE dispatches SET terminal_reason='missing-protection-escalated'")
            return claimed

    store = VetoClaimStore(factory)
    execution = Execution(
        result=replace(
            _result(LiveOrderStatus.ACCEPTED),
            client_oid=BitgetDispatchExecution.client_oid(dispatch),
        )
    )
    adapter = BitgetDispatchExecution(
        execution,
        store,
        dispatch_repository=repository,
        reservation_repository=PostgresBitgetMarginReservationRepository(factory),
    )
    adapter.recovery_ready = True
    assert await adapter.submit_entry(dispatch, submission) == "REJECTED"
    assert execution.submit_calls == []
    with factory() as c:
        assert c.execute("SELECT terminal_reason FROM dispatches").fetchone() == (
            "missing-protection-escalated",
        )
        assert c.execute("SELECT state FROM bitget_margin_reservations").fetchone() == ("released",)
        assert c.execute("SELECT state FROM live_order_intents").fetchone() == ("rejected",)


def test_unknown_without_acknowledgement_cannot_be_retired_by_age(postgres_schema):
    from fatty_trader.execution.bitget_dispatch_repository import PostgresBitgetDispatchRepository
    from fatty_trader.intake.freshness import expire_source_dispatch

    factory = setup(postgres_schema)
    dispatch_id = queued(factory)
    repository = PostgresBitgetDispatchRepository(factory)
    assert repository.claim("offline", 30) is not None
    assert reserve(factory, dispatch_id).accepted
    repository.transition(dispatch_id, expected_state="QUEUED", target_state="SUBMITTING")
    repository.transition(dispatch_id, expected_state="SUBMITTING", target_state="UNKNOWN")
    with factory() as c:
        c.execute("UPDATE telegram_messages SET received_at=clock_timestamp()-interval '6 minutes'")
        cursor = c.cursor()
        cursor.execute("SELECT state FROM dispatches WHERE id=%s FOR UPDATE", (dispatch_id,))
        state = cursor.fetchone()[0]
        expire_source_dispatch(cursor, dispatch_id, state)
    with factory() as c:
        assert c.execute("SELECT state FROM dispatches").fetchone() == ("UNKNOWN",)
        assert c.execute("SELECT state FROM bitget_margin_reservations").fetchone() == ("reserved",)


@pytest.mark.asyncio
async def test_source_ageing_during_async_leverage_setup_blocks_actual_post(postgres_schema):
    from dataclasses import replace
    from typing import Any, cast

    from fatty_trader.exchanges.bitget.async_execution import AsyncBitgetExecution
    from fatty_trader.execution.bitget_dispatch_execution import BitgetDispatchExecution
    from fatty_trader.execution.bitget_dispatch_repository import PostgresBitgetDispatchRepository
    from fatty_trader.storage.live_intents import PostgresLiveIntentStore
    from tests.unit.test_bitget_dispatch_execution_adapter import _submission

    factory = setup(postgres_schema)
    dispatch_id = queued(factory)
    repository = PostgresBitgetDispatchRepository(factory)
    dispatch = repository.claim("offline", 30)
    assert dispatch is not None
    dispatch = replace(dispatch, pair_token="BTCUSDT")
    assert reserve(factory, dispatch_id, BitgetDispatchExecution.client_oid(dispatch)).accepted
    with factory() as c:
        reservation_id, snapshot_id = c.execute(
            "SELECT id,balance_snapshot_id FROM bitget_margin_reservations"
        ).fetchone()
    submission = replace(
        _submission(),
        margin_reservation_id=reservation_id,
        balance_snapshot_id=snapshot_id,
        planned_margin_usdt=Decimal("1"),
    )
    repository.transition(dispatch_id, expected_state="QUEUED", target_state="SUBMITTING")

    class Venue:
        async def ensure_leverage(self, symbol, leverage):
            with factory() as c:
                c.execute(
                    "UPDATE telegram_messages SET "
                    "received_at=clock_timestamp()-interval '6 minutes'"
                )

    class Client:
        def __init__(self):
            self.posts = []

        async def place_entry_order(self, **kwargs):
            self.posts.append(kwargs)
            # Stop immediately at the actual provider mutation boundary. This is
            # a fake transport, not an exchange request or fabricated readback.
            raise RuntimeError("offline-recorded-entry-post")

    client = Client()
    execution = AsyncBitgetExecution(cast(Any, client), cast(Any, Venue()))
    adapter = BitgetDispatchExecution(
        execution, PostgresLiveIntentStore(factory), dispatch_repository=repository
    )
    try:
        await adapter.submit_entry(dispatch, submission)
    except RuntimeError as exc:
        assert str(exc) == "offline-recorded-entry-post"
    assert client.posts == []
