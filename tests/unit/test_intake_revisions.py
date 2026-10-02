from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from telethon import events

from fatty_trader.config.telegram import TelegramSettings
from fatty_trader.intake.persistence import InMemoryRawMessageRepository
from fatty_trader.intake.telegram import TelegramForwarder, TelegramIntake


def message(**fields):
    return SimpleNamespace(id=42, message="same caption", date=datetime.now(UTC), **fields)


@pytest.mark.asyncio
@pytest.mark.parametrize("forwarder", [False, True])
async def test_actual_edit_event_is_registered_and_retained(forwarder):
    registrations = []
    client = SimpleNamespace(add_event_handler=lambda cb, event: registrations.append((cb, event)))
    repository = InMemoryRawMessageRepository()
    if forwarder:
        adapter = TelegramForwarder(
            client, TelegramSettings(1, "hash", "session", ("-1007",), 123), repository
        )
        await adapter.attach()
    else:
        adapter = TelegramIntake(repository)
        await adapter.attach(client, ("-1007",))
    original = next(cb for cb, event in registrations if type(event) is events.NewMessage)
    edited = next(cb for cb, event in registrations if type(event) is events.MessageEdited)
    source = message(media=None)
    await original(SimpleNamespace(chat_id=-1007, message=source))
    source.edit_date = datetime.now(UTC)
    await edited(SimpleNamespace(chat_id=-1007, message=source))
    await edited(SimpleNamespace(chat_id=-1007, message=source))
    assert repository.count == 2
    revisions = list(repository._messages.values())
    assert revisions[1].entry_rejection_reason == "edited-source-message"
    assert revisions[0].entry_expires_at == revisions[1].entry_expires_at


def test_same_caption_media_replacement_has_distinct_revision_identity():
    repository = InMemoryRawMessageRepository()
    adapter = TelegramIntake(repository)
    first = adapter.ingest(
        channel_id=7, message=message(media=SimpleNamespace(photo=SimpleNamespace(id=10)))
    )
    second = adapter.ingest(
        channel_id=7, message=message(media=SimpleNamespace(photo=SimpleNamespace(id=11)))
    )
    duplicate = adapter.ingest(
        channel_id=7, message=message(media=SimpleNamespace(photo=SimpleNamespace(id=11)))
    )
    assert first.revision_hash != second.revision_hash
    assert second.revision_hash == duplicate.revision_hash
    assert repository.count == 2
