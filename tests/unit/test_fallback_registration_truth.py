"""E04: registration is persistence, not verified fallback enforcement (no I/O)."""

from decimal import Decimal

import pytest
from test_bitget_async_execution import (
    _filled_entry_intent,
    _NativeUnsupportedClient,
    _protection_plan,
)

from fatty_trader.exchanges.bitget.async_execution import AsyncBitgetExecution
from fatty_trader.exchanges.bitget.async_venue import AsyncBitgetVenue
from fatty_trader.exchanges.bitget.live import InMemoryLiveIntentStore
from fatty_trader.execution.protection import ProtectionState


@pytest.fixture
def persisted_fallback_rows(monkeypatch):
    """Run the real registry against a tiny parameterized DB boundary fake."""
    import fatty_trader.execution.bitget_fallback_protection as registry

    rows = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def cursor(self):
            return self

        def execute(self, sql, params=()):
            if "INSERT INTO fallback_protection" in sql:
                rows.append(params)

        def fetchone(self):
            return ("fallback-row-1",)

        def commit(self):
            pass

    monkeypatch.setattr(registry, "_psycopg_connect", Connection)
    return rows


@pytest.mark.asyncio
async def test_real_registration_without_enforcement_evidence_is_degraded(persisted_fallback_rows):
    client = _NativeUnsupportedClient()
    execution = AsyncBitgetExecution(
        client, AsyncBitgetVenue(client), fallback_protection_enabled=True
    )

    result = await execution.protect_filled_position(
        _filled_entry_intent(), _protection_plan(), InMemoryLiveIntentStore()
    )

    assert persisted_fallback_rows == []
    assert result.state is ProtectionState.DEGRADED
    assert result.reason == "fallback-registration-failed"
    assert result.observed_quantity == Decimal("0.001")
    assert execution.degraded is True


@pytest.mark.asyncio
async def test_real_dispatch_adapter_preserves_fill_but_not_protection(persisted_fallback_rows):
    from dataclasses import replace

    from test_bitget_dispatch_execution_adapter import _dispatch, _submission

    from fatty_trader.execution.bitget_dispatch_execution import BitgetDispatchExecution

    client = _NativeUnsupportedClient()
    execution = AsyncBitgetExecution(
        client, AsyncBitgetVenue(client), fallback_protection_enabled=True
    )
    store = InMemoryLiveIntentStore()
    adapter = BitgetDispatchExecution(execution, store)
    dispatch = _dispatch()

    status = await adapter.submit_entry(dispatch, replace(_submission(), quantity=Decimal("0.001")))

    assert persisted_fallback_rows == []
    assert status == "FILLED_UNPROTECTED"
    assert execution.degraded is True
    intent = store.get(adapter.client_oid(dispatch))
    assert intent is not None
    assert intent.state == "filled"
    assert intent.filled_qty == Decimal("0.001")
    assert intent.provider_order_id == "provider-1"
    assert len(client.entry_calls) == 1

    # Durable replay is GET-only: it must not infer enforcement from the row,
    # register another fallback, or place a second entry.
    assert (
        await adapter.submit_entry(dispatch, replace(_submission(), quantity=Decimal("0.001")))
        == "FILLED_UNPROTECTED"
    )
    assert persisted_fallback_rows == []
    assert len(client.entry_calls) == 1
