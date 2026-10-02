import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from fatty_trader.intake.persistence import InMemoryRawMessageRepository
from fatty_trader.intake.telegram import TelegramIntake


def source(download, *, media_id=10, size=None):
    return SimpleNamespace(
        id=1,
        message="chart",
        date=datetime.now(UTC),
        media=SimpleNamespace(photo=SimpleNamespace(id=media_id)),
        file=SimpleNamespace(mime_type="image/jpeg", size=size),
        download_media=download,
    )


@pytest.mark.asyncio
async def test_download_is_stream_bounded_before_unadvertised_bytes_are_written(tmp_path):
    written = 0

    async def download(*, file):
        nonlocal written
        assert hasattr(file, "write"), "download must use a bounded sink, not an unrestricted path"
        for _ in range(11):
            file.write(b"x" * (1024 * 1024))
            written += 1024 * 1024
        return file

    item = await TelegramIntake(InMemoryRawMessageRepository()).ingest_async(
        channel_id=7, message=source(download), media_root=str(tmp_path)
    )
    assert written == 10 * 1024 * 1024
    assert item.entry_rejection_reason == "oversized-source-media"
    assert item.media_path is None
    assert list(tmp_path.rglob("*")) == []


@pytest.mark.asyncio
async def test_simultaneous_same_message_downloads_are_isolated_and_idempotent(tmp_path):
    sinks = []

    async def download(*, file):
        assert hasattr(file, "write"), "concurrent downloads must not share a filename"
        sinks.append(file)
        file.write(b"chart-bytes")
        await asyncio.sleep(0)
        return file

    repository = InMemoryRawMessageRepository()
    adapter = TelegramIntake(repository)
    msg = source(download)
    first, second = await asyncio.gather(
        *[
            adapter.ingest_async(channel_id=7, message=msg, media_root=str(tmp_path))
            for _ in range(2)
        ]
    )
    assert sinks[0] is not sinks[1]
    assert first == second
    assert repository.count == 1
    assert first.media_path is not None
    assert Path(first.media_path).read_bytes() == b"chart-bytes"
    assert len([path for path in tmp_path.rglob("*") if path.is_file()]) == 1


@pytest.mark.asyncio
async def test_download_timeout_is_rejected_and_cleans_partial_bytes(tmp_path, monkeypatch):
    from fatty_trader.intake import telegram

    monkeypatch.setattr(telegram, "SOURCE_MEDIA_DOWNLOAD_TIMEOUT_SECONDS", 0.01, raising=False)

    async def download(*, file):
        if hasattr(file, "write"):
            file.write(b"partial")
        await asyncio.sleep(0.05)
        return file

    item = await TelegramIntake(InMemoryRawMessageRepository()).ingest_async(
        channel_id=7, message=source(download), media_root=str(tmp_path)
    )
    assert item.entry_rejection_reason == "source-media-download-timeout"
    assert item.media_path is None
    assert list(tmp_path.rglob("*")) == []


@pytest.mark.asyncio
async def test_ten_mib_artifact_limit_is_also_metadata_preflight_limit(tmp_path):
    async def forbidden(**kwargs):
        pytest.fail("artifact-oversized content must not start downloading")

    item = await TelegramIntake(InMemoryRawMessageRepository()).ingest_async(
        channel_id=7, message=source(forbidden, size=10 * 1024 * 1024 + 1), media_root=str(tmp_path)
    )
    assert item.entry_rejection_reason == "oversized-source-media"


@pytest.mark.asyncio
@pytest.mark.parametrize("forward", [False, True])
async def test_failed_download_retry_heals_same_revision_without_refreshing_age(tmp_path, forward):
    attempts = 0

    async def download(*, file):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            file.write(b"partial")
            raise OSError("offline fixture failure")
        file.write(b"complete-chart")
        return file

    repository = InMemoryRawMessageRepository()
    adapter = TelegramIntake(repository)
    msg = source(download)
    first = await adapter.ingest_async(
        channel_id=7, message=msg, media_root=str(tmp_path), persist=not forward
    )
    if forward:
        assert repository.save_and_enqueue_forward(first)
    second = await adapter.ingest_async(
        channel_id=7, message=msg, media_root=str(tmp_path), persist=not forward
    )
    if forward:
        assert not repository.save_and_enqueue_forward(second)
        second = repository.save_if_new(second)
    assert repository.count == 1
    assert first.revision_hash == second.revision_hash
    assert first.entry_rejection_reason == "source-media-download-failed"
    assert second.entry_rejection_reason is None
    assert second.media_path is not None
    assert second.received_at == first.received_at
    assert second.entry_expires_at == first.entry_expires_at
    assert Path(second.media_path).read_bytes() == b"complete-chart"
