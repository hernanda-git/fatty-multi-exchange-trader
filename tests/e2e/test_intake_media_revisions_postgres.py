import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from test_intake_coverage_postgres import CHANNEL, message, setup
from test_postgres_migration_and_margin_admission import postgres_schema as postgres_schema

from fatty_trader.intake.persistence import PostgresRawMessageRepository
from fatty_trader.intake.telegram import TelegramIntake


@pytest.mark.parametrize("forward", [False, True])
def test_download_retry_heals_only_missing_media_without_replacing_revision(
    postgres_schema, tmp_path, forward
):
    factory = setup(postgres_schema)
    repository = PostgresRawMessageRepository(factory)
    adapter = TelegramIntake(repository)
    source = message(101)
    source.media = SimpleNamespace(photo=SimpleNamespace(id=7))
    source.file = SimpleNamespace(mime_type="image/jpeg", size=4)
    calls = 0

    async def download(*, file):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("offline retry fixture")
        file.write(b"jpeg")
        return file

    source.download_media = download
    failed = asyncio.run(
        adapter.ingest_async(
            channel_id=CHANNEL, message=source, media_root=str(tmp_path), persist=not forward
        )
    )
    if forward:
        assert repository.save_and_enqueue_forward(failed)
    with factory() as connection:
        original_id = connection.execute("SELECT id FROM telegram_messages").fetchone()[0]
    succeeded = asyncio.run(
        adapter.ingest_async(
            channel_id=CHANNEL, message=source, media_root=str(tmp_path), persist=not forward
        )
    )
    if forward:
        assert not repository.save_and_enqueue_forward(succeeded)
    with factory() as connection:
        assert connection.execute(
            "SELECT id,revision_hash,received_at,entry_expires_at,intake_state,"
            "entry_rejection_reason,media_path "
            "FROM telegram_messages"
        ).fetchall() == [
            (
                original_id,
                failed.revision_hash,
                failed.received_at,
                failed.entry_expires_at,
                "RECEIVED",
                None,
                succeeded.media_path,
            )
        ]
    # Redelivery of a failed attempt can never clobber a complete artifact.
    repository.save_if_new(failed)
    with factory() as connection:
        assert connection.execute("SELECT media_path FROM telegram_messages").fetchone() == (
            succeeded.media_path,
        )


def test_original_and_same_caption_edits_remain_distinct_audit_rows(postgres_schema):
    factory = setup(postgres_schema)
    adapter = TelegramIntake(PostgresRawMessageRepository(factory))
    source = message(101)
    first = adapter.ingest(channel_id=CHANNEL, message=source)
    source.edit_date = datetime.now(UTC)
    edited = adapter.ingest(channel_id=CHANNEL, message=source)
    adapter.ingest(channel_id=CHANNEL, message=source)
    assert first.revision_hash != edited.revision_hash
    with factory() as connection:
        assert connection.execute(
            "SELECT revision_hash,intake_state,entry_rejection_reason FROM "
            "telegram_messages ORDER BY intake_state DESC"
        ).fetchall() == [
            (first.revision_hash, "RECEIVED", "edited-source-message"),
            (edited.revision_hash, "EXPIRED", "edited-source-message"),
        ]
