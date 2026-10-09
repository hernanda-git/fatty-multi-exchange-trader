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
        # Marked ids come from Telethon in production; the fake entity is a plain object.
        peer_id_of=lambda entity: int(entity.id),
    )

    assert saved == 1
    assert repository.count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("download_fails", [False, True])
async def test_backfill_retains_media_or_an_explicit_download_failure(tmp_path, download_fails):
    from pathlib import Path

    downloaded = []

    async def download_media(*, file):
        downloaded.append(True)
        if download_fails:
            raise OSError("source media unavailable")
        file.write(b"source image")

    message = SimpleNamespace(
        id=10,
        message="",
        date=datetime.now(UTC),
        reply_to=None,
        media=SimpleNamespace(photo=SimpleNamespace(id=123)),
        file=SimpleNamespace(mime_type="image/png", size=12),
        download_media=download_media,
    )
    disconnected = []

    class FakeClient:
        async def start(self):
            return None

        async def disconnect(self):
            disconnected.append(True)

        async def get_entity(self, channel):
            return SimpleNamespace(id=-100123)

        async def iter_messages(self, entity, limit):
            yield message

    retained = []

    class Repository:
        def save_if_new(self, item):
            retained.append(item)
            return item

    saved = await backfill_latest(
        {
            "TG_API_ID": "1",
            "TG_API_HASH": "hash",
            "TELEGRAM_SESSION": "session",
            "TELEGRAM_SOURCE_CHANNELS": "@fattyfatclub",
            "TELEGRAM_TARGET_CHAT_ID": "1",
            "TELEGRAM_MEDIA_ROOT": str(tmp_path),
        },
        client_factory=lambda _: FakeClient(),
        repository=Repository(),
        peer_id_of=lambda entity: int(entity.id),
    )
    assert saved == 1
    assert downloaded == [True]
    assert disconnected == [True]
    assert retained[0].ingestion_origin == "backfill"
    if download_fails:
        assert retained[0].entry_rejection_reason == "source-media-download-failed"
        assert retained[0].media_path is None
    else:
        assert retained[0].entry_rejection_reason is None
        assert Path(retained[0].media_path).read_bytes() == b"source image"
