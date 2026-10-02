"""Audit edits revoke all unsent SOURCE revisions; real PG, offline venue."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from test_intake_coverage_postgres import CHANNEL, message, setup
from test_postgres_migration_and_margin_admission import postgres_schema as postgres_schema
from test_source_freshness_boundaries import queued, reserve, seed

from fatty_trader.analyzer.codex_runner import CodexRunResult
from fatty_trader.analyzer.postgres_worker import process_received_batch
from fatty_trader.execution.bitget_dispatch_repository import PostgresBitgetDispatchRepository
from fatty_trader.intake.persistence import PostgresRawMessageRepository
from fatty_trader.intake.telegram import TelegramIntake


def edit(factory, *, reverted=False, media=False, forward=False):
    if reverted:
        edit(factory, media=media, forward=forward)
    item = message(
        101, "#BTC LONG ENTRY: 100 TARGET: 110 STOPLOSS: 90" if reverted else "cancel entry"
    )
    item.edit_date = datetime.now(UTC)
    if media:
        from types import SimpleNamespace

        item.media = SimpleNamespace(document=SimpleNamespace(id=5678))
    intake = TelegramIntake(PostgresRawMessageRepository(factory))
    raw = intake._build_message(channel_id=CHANNEL, message=item)
    repo = PostgresRawMessageRepository(factory)
    (repo.save_and_enqueue_forward if forward else repo.save_if_new)(raw)
    assert raw.entry_rejection_reason == "edited-source-message"


@pytest.mark.parametrize("boundary", ["analyzer", "claim", "reserve", "final"])
@pytest.mark.parametrize(
    "reverted,media,forward",
    [(False, False, False), (True, False, True), (False, True, True), (True, True, False)],
)
def test_edit_revokes_original_at_every_source_boundary(
    postgres_schema, boundary, reverted, media, forward
):
    factory = setup(postgres_schema)
    repo = PostgresBitgetDispatchRepository(factory)
    if boundary == "analyzer":
        seed(factory)
    else:
        dispatch_id = queued(factory)
        if boundary in {"reserve", "final"}:
            assert repo.claim("offline", 30) is not None
        if boundary == "final":
            assert reserve(factory, dispatch_id).accepted
            repo.transition(dispatch_id, expected_state="QUEUED", target_state="SUBMITTING")
    edit(factory, reverted=reverted, media=media, forward=forward)
    # Re-reading the old original cannot restore entry authority.
    seed(factory)
    if boundary == "analyzer":
        model_calls = []

        def runner(request):
            model_calls.append(request)
            return CodexRunResult(False, True, False, 1, "offline", "", "")

        process_received_batch(factory, runner=runner, exchanges=("bitget",))
        assert model_calls == []
        with factory() as c:
            assert c.execute("SELECT count(*) FROM dispatches").fetchone() == (0,)
    elif boundary == "claim":
        assert repo.claim("offline", 30) is None
    elif boundary == "reserve":
        assert not reserve(factory, dispatch_id).accepted
    else:
        assert not repo.entry_source_eligible(dispatch_id)
        with factory() as c:
            assert c.execute("SELECT state FROM bitget_margin_reservations").fetchone() == (
                "released",
            )
    if boundary != "analyzer":
        with factory() as c:
            assert c.execute("SELECT state FROM dispatches").fetchone() == ("EXPIRED",)


def test_same_revision_original_retrieval_stays_idempotent(postgres_schema):
    factory = setup(postgres_schema)
    dispatch_id = queued(factory)
    seed(factory)
    repo = PostgresBitgetDispatchRepository(factory)
    assert repo.entry_source_eligible(dispatch_id)
    assert repo.claim("offline", 30) is not None
    with factory() as c:
        assert c.execute("SELECT count(*) FROM telegram_messages").fetchone() == (1,)


@pytest.mark.parametrize(
    "state,reservation_state",
    [
        ("UNKNOWN", "unknown"),
        ("UNKNOWN", "reserved"),
        ("SUBMITTING", "unknown"),
        ("SUBMITTING", "consumed"),
        ("FILLED", "consumed"),
    ],
)
def test_edit_preserves_ambiguous_and_filled_truth(postgres_schema, state, reservation_state):
    factory = setup(postgres_schema)
    dispatch_id = queued(factory)
    repo = PostgresBitgetDispatchRepository(factory)
    assert repo.claim("offline", 30) is not None
    assert reserve(factory, dispatch_id).accepted
    with factory() as c:
        c.execute("UPDATE dispatches SET state=%s", (state,))
        c.execute("UPDATE bitget_margin_reservations SET state=%s", (reservation_state,))
    edit(factory)
    assert not repo.entry_source_eligible(dispatch_id)
    with factory() as c:
        assert c.execute("SELECT state FROM dispatches").fetchone() == (state,)
        assert c.execute("SELECT state FROM bitget_margin_reservations").fetchone() == (
            reservation_state,
        )
        assert c.execute(
            "SELECT count(*) FROM dispatch_transitions WHERE to_state='EXPIRED'"
        ).fetchone() == (0,)


@pytest.mark.asyncio
async def test_edit_during_leverage_setup_blocks_actual_final_post(postgres_schema):
    from decimal import Decimal
    from typing import Any, cast

    from fatty_trader.exchanges.bitget.async_execution import AsyncBitgetExecution
    from fatty_trader.execution.bitget_dispatch_execution import BitgetDispatchExecution
    from fatty_trader.storage.live_intents import PostgresLiveIntentStore
    from tests.unit.test_bitget_dispatch_execution_adapter import _submission

    factory = setup(postgres_schema)
    dispatch_id = queued(factory)
    repo = PostgresBitgetDispatchRepository(factory)
    dispatch = repo.claim("offline", 30)
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
    repo.transition(dispatch_id, expected_state="QUEUED", target_state="SUBMITTING")

    class Venue:
        async def ensure_leverage(self, symbol, leverage):
            edit(factory, reverted=True, media=True)

    class Client:
        def __init__(self):
            self.posts = []

        async def place_entry_order(self, **kwargs):
            self.posts.append(kwargs)
            raise RuntimeError("offline-recorded-entry-post")

    client = Client()
    adapter = BitgetDispatchExecution(
        AsyncBitgetExecution(cast(Any, client), cast(Any, Venue())),
        PostgresLiveIntentStore(factory),
        dispatch_repository=repo,
    )
    try:
        await adapter.submit_entry(dispatch, submission)
    except RuntimeError as exc:
        assert str(exc) == "offline-recorded-entry-post"
    assert client.posts == []
    with factory() as c:
        assert c.execute("SELECT state FROM dispatches").fetchone() == ("EXPIRED",)
        assert c.execute("SELECT state FROM bitget_margin_reservations").fetchone() == ("released",)
        assert c.execute("SELECT state FROM live_order_intents").fetchone() == ("rejected",)


@pytest.mark.parametrize("intent_state", ["requested", "unknown"])
def test_edit_retains_submitting_durable_ambiguous_intent(postgres_schema, intent_state):
    from decimal import Decimal

    from fatty_trader.execution.bitget_dispatch_execution import BitgetDispatchExecution
    from fatty_trader.storage.live_intents import PostgresLiveIntentStore
    from tests.unit.test_bitget_dispatch_execution_adapter import _submission

    factory = setup(postgres_schema)
    dispatch_id = queued(factory)
    repo = PostgresBitgetDispatchRepository(factory)
    dispatch = repo.claim("offline", 30)
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
    intent.state = intent_state
    assert PostgresLiveIntentStore(factory).claim(intent)
    repo.transition(dispatch_id, expected_state="QUEUED", target_state="SUBMITTING")
    edit(factory)
    assert not repo.entry_source_eligible(dispatch_id)
    with factory() as c:
        assert c.execute("SELECT state FROM dispatches").fetchone() == ("SUBMITTING",)
        assert c.execute("SELECT state FROM bitget_margin_reservations").fetchone() == ("reserved",)
        assert c.execute("SELECT state FROM live_order_intents").fetchone() == (intent_state,)


def test_same_revision_media_retrieval_does_not_revoke_entry(postgres_schema):
    from types import SimpleNamespace

    from fatty_trader.intake.freshness import SOURCE_ELIGIBLE_SQL

    factory = setup(postgres_schema)
    repository = PostgresRawMessageRepository(factory)
    item = message(101, "#BTC LONG ENTRY: 100 TARGET: 110 STOPLOSS: 90")
    item.media = SimpleNamespace(document=SimpleNamespace(id=5678))
    raw = TelegramIntake(repository)._build_message(channel_id=CHANNEL, message=item)
    repository.save_if_new(raw)
    retrieved = replace(
        raw,
        media_path="/offline/retrieved.jpg",
        media_sha256="a" * 64,
        media_mime_type="image/jpeg",
        media_size_bytes=32,
    )
    repository.save_and_enqueue_forward(retrieved)
    repository.save_if_new(retrieved)
    with factory() as c:
        assert c.execute("SELECT count(*) FROM telegram_messages").fetchone() == (1,)
        assert c.execute("SELECT media_path FROM telegram_messages").fetchone() == (
            retrieved.media_path,
        )
        assert c.execute(
            "SELECT count(*) FROM telegram_messages tm WHERE "
            + SOURCE_ELIGIBLE_SQL.format(alias="tm")
        ).fetchone() == (1,)
