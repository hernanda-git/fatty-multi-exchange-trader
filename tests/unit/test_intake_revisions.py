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
    # Same caption: the later revision is audit-only and the entry stays eligible.
    assert revisions[1].entry_rejection_reason == "superseded-source-revision"
    assert revisions[0].entry_rejection_reason is None
    assert revisions[0].entry_expires_at == revisions[1].entry_expires_at


def test_edited_caption_revokes_every_revision_of_the_message():
    repository = InMemoryRawMessageRepository()
    adapter = TelegramIntake(repository)
    adapter.ingest(channel_id=7, message=message(media=None))
    changed = SimpleNamespace(
        id=42,
        message="different caption",
        date=datetime.now(UTC),
        media=None,
        edit_date=datetime.now(UTC),
    )
    adapter.ingest(channel_id=7, message=changed)
    reasons = [item.entry_rejection_reason for item in repository._messages.values()]
    assert reasons == ["edited-source-message", "edited-source-message"]


def test_cosmetic_typo_edit_preserves_entry_eligibility():
    repository = InMemoryRawMessageRepository()
    adapter = TelegramIntake(repository)
    first_msg = SimpleNamespace(
        id=42,
        message="#PUMP $PUMP placing short limit at 0.004195\n\nSl: 0.004285",
        date=datetime.now(UTC),
        media=None,
        edit_date=None,
    )
    adapter.ingest(channel_id=7, message=first_msg)
    cosmetic_edit = SimpleNamespace(
        id=42,
        message="$PUMP placing short limit at 0.004195\n\nSl: 0.004285",
        date=datetime.now(UTC),
        media=None,
        edit_date=datetime.now(UTC),
    )
    adapter.ingest(channel_id=7, message=cosmetic_edit)
    reasons = [item.entry_rejection_reason for item in repository._messages.values()]
    assert "edited-source-message" not in reasons
    assert any(reason is None for reason in reasons)


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
