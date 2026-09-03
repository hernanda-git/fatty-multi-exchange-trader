from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from fatty_trader.intake.backfill import backfill_latest
from fatty_trader.intake.persistence import InMemoryRawMessageRepository


@pytest.mark.asyncio
async def test_backfill_persists_latest_message_from_each_configured_channel() -> None:
    message = SimpleNamespace(
        id=9,
        message="BTCUSDT LONG",
        date=datetime(2026, 1, 1, tzinfo=UTC),
        reply_to=None,
        media=None,
    )

    class FakeClient:
        async def start(self) -> None:
            return None

        async def disconnect(self) -> None:
            return None

        async def get_entity(self, channel: str) -> SimpleNamespace:
            return SimpleNamespace(id=-100123)

        async def iter_messages(self, entity: object, limit: int):
            assert limit == 1
            yield message

    repository = InMemoryRawMessageRepository()
    saved = await backfill_latest(
        {
            "TG_API_ID": "1",
            "TG_API_HASH": "hash",
            "TELEGRAM_SESSION": "session",
            "TELEGRAM_SOURCE_CHANNELS": "@fattyfatclub",
            "TELEGRAM_TARGET_CHAT_ID": "1",
        },
        client_factory=lambda _: FakeClient(),
        repository=repository,
    )

    assert saved == 1
    assert repository.count == 1
