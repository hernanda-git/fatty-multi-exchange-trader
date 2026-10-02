"""Production preflight against disposable PostgreSQL; fake read-only venue."""

import runpy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from test_postgres_migration_and_margin_admission import _connect, _migrate
from test_postgres_migration_and_margin_admission import postgres_schema as _postgres_schema

from fatty_trader.service import _bitget_dispatch_preflight
from fatty_trader.storage.balance_reservations import PostgresBitgetMarginReservationRepository

postgres_schema = _postgres_schema
# Reuse the read-only venue/dispatch fixture without replacing the repository.
fixtures = runpy.run_path(
    str(Path(__file__).parents[1] / "unit/test_bitget_service_admission_wiring.py")
)


@pytest.mark.asyncio
async def test_production_preflight_real_reserve_persists_ownership(postgres_schema):
    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    dispatch = replace(fixtures["_dispatch"](), id=uuid4())
    with _connect(dsn, schema) as c:
        c.execute(
            """INSERT INTO dispatches (id,source_type,source_id,revision,exchange,state)
        VALUES (%s,'admission',%s,%s,'bitget','QUEUED')""",
            (dispatch.id, uuid4(), "a" * 64),
        )
        from source_freshness_fixtures import eligible_dispatch_source

        eligible_dispatch_source(c, dispatch.id)
    venue = fixtures["Venue"]()
    repository = PostgresBitgetMarginReservationRepository(lambda: _connect(dsn, schema))
    admission = await _bitget_dispatch_preflight(
        venue,
        {"BITGET_MAX_MARGIN_PER_TRADE_USDT": "1", "BITGET_MODE": "DEMO"},
        reservation_repository=repository,
    )(dispatch)
    with _connect(dsn, schema) as c:
        assert c.execute(
            "SELECT symbol, environment, state FROM bitget_margin_reservations WHERE id=%s",
            (admission.submission.margin_reservation_id,),
        ).fetchone() == ("PENDLEUSDT", "DEMO", "reserved")
    # Existing exposure must not be automatically released by a later flat read.
    repository.resolve(admission.submission.margin_reservation_id, "FILLED")
    with pytest.raises(ValueError, match="symbol-already-owned"):
        await _bitget_dispatch_preflight(
            venue, {"BITGET_MAX_MARGIN_PER_TRADE_USDT": "1"}, reservation_repository=repository
        )(dispatch)
    with _connect(dsn, schema) as c:
        assert c.execute("SELECT state FROM bitget_margin_reservations").fetchone() == ("consumed",)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "symbols,cap,reason",
    [
        (("PENDLEUSDT",), "5", "symbol-already-owned"),
        (("BTCUSDT", "BTCUSDT"), "2", "sizing rejected"),
        (("BTCUSDT", "BTCUSDT"), "3", None),
    ],
)
async def test_real_preflight_uses_normalized_provider_ownership_and_hedge_slots(
    postgres_schema, symbols, cap, reason
):
    from fatty_trader.exchanges.bitget.async_venue import AsyncBitgetVenue

    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    dispatch = replace(fixtures["_dispatch"](), id=uuid4())
    with _connect(dsn, schema) as c:
        c.execute(
            """INSERT INTO dispatches (id,source_type,source_id,revision,exchange,state)
        VALUES (%s,'admission',%s,%s,'bitget','QUEUED')""",
            (dispatch.id, uuid4(), "a" * 64),
        )
        from source_freshness_fixtures import eligible_dispatch_source

        eligible_dispatch_source(c, dispatch.id)

    class Provider:
        calls = 0

        async def get_all_positions(self):
            self.calls += 1
            return [{"symbol": s, "total": "1"} for s in symbols]

    provider = Provider()
    venue = fixtures["Venue"]()
    venue.active_position_snapshot = AsyncBitgetVenue(provider).active_position_snapshot
    repository = PostgresBitgetMarginReservationRepository(lambda: _connect(dsn, schema))
    preflight = _bitget_dispatch_preflight(
        venue,
        {"BITGET_MAX_MARGIN_PER_TRADE_USDT": "1", "BITGET_MAX_NORMAL_POSITIONS": cap},
        reservation_repository=repository,
    )
    if reason:
        with pytest.raises(ValueError, match=reason):
            await preflight(dispatch)
    else:
        await preflight(dispatch)
    assert provider.calls == 1
    with _connect(dsn, schema) as c:
        assert c.execute("SELECT count(*) FROM bitget_margin_reservations").fetchone() == (
            0 if reason else 1,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source,offset", [("account", -60), ("account", 60), ("positions", -60), ("positions", 60)]
)
async def test_real_repository_seam_refuses_stale_or_future_provider_evidence(
    postgres_schema, source, offset
):
    from datetime import timedelta
    from types import SimpleNamespace

    dsn, schema = postgres_schema
    _migrate(dsn, schema)
    venue = fixtures["Venue"]()
    stamp = datetime.now(UTC) + timedelta(seconds=offset)
    if source == "account":
        venue.snapshot.account.observed_at = stamp
    else:

        async def positions():
            return SimpleNamespace(symbols=(), observed_at=stamp)

        venue.active_position_snapshot = positions
    repository = PostgresBitgetMarginReservationRepository(lambda: _connect(dsn, schema))
    with pytest.raises(ValueError, match="stale|future"):
        await _bitget_dispatch_preflight(
            venue, {"BITGET_MAX_MARGIN_PER_TRADE_USDT": "1"}, reservation_repository=repository
        )(fixtures["_dispatch"]())
    with _connect(dsn, schema) as c:
        assert c.execute("SELECT count(*) FROM bitget_margin_reservations").fetchone() == (0,)
        assert c.execute("SELECT count(*) FROM balance_snapshots").fetchone() == (0,)
