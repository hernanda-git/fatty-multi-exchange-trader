import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from fatty_trader.intake.persistence import InMemoryRawMessageRepository
from fatty_trader.intake.telegram import TelegramIntake


def source(**fields):
    return SimpleNamespace(
        id=1, message="BTC LONG ENTRY: 100 STOPLOSS: 95", date=datetime.now(UTC), **fields
    )


def test_edited_source_is_audit_only_without_refreshing_entry_age():
    intake = TelegramIntake(InMemoryRawMessageRepository())
    item = intake.ingest(channel_id=7, message=source(edit_date=datetime.now(UTC)))
    assert item.entry_rejection_reason == "edited-source-message"


@pytest.mark.parametrize(
    "mime,size,reason",
    [
        ("video/mp4", 10, "unsupported-source-media"),
        ("image/jpeg", 20 * 1024 * 1024 + 1, "oversized-source-media"),
    ],
)
def test_unsafe_media_is_rejected_before_download(tmp_path, mime, size, reason):
    async def forbidden(**kwargs):
        raise AssertionError("unsafe media must not be downloaded")

    item = asyncio.run(
        TelegramIntake(InMemoryRawMessageRepository()).ingest_async(
            channel_id=7,
            message=source(
                media=object(),
                file=SimpleNamespace(mime_type=mime, size=size),
                download_media=forbidden,
            ),
            media_root=str(tmp_path),
        )
    )
    assert item.entry_rejection_reason == reason
    assert item.media_path is None


def test_unadvertised_oversized_download_never_becomes_model_attachment(tmp_path):
    async def download(*, file):
        file.write(b"x" * (10 * 1024 * 1024))
        file.write(b"x")
        return file

    item = asyncio.run(
        TelegramIntake(InMemoryRawMessageRepository()).ingest_async(
            channel_id=7,
            message=source(
                media=object(),
                file=SimpleNamespace(mime_type="image/jpeg"),
                download_media=download,
            ),
            media_root=str(tmp_path),
        )
    )
    assert item.entry_rejection_reason == "oversized-source-media"
    assert item.media_path is None
