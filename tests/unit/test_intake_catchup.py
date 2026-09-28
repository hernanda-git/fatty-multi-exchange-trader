"""Offline tests for the intake catch-up safety net.

Incident 2026-09-27: the realtime-only listen path went blind for 5h35m and the source's
ENA signal was lost permanently. Catch-up polls for messages newer than the newest
persisted one, so a blind window costs nothing.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from fatty_trader.intake.catchup import (
    build_cursor_lookup,
    catch_up_missed,
    run_catchup_loop,
)
from fatty_trader.service import run_intake

CHANNEL = "@fattyfatclub"
# get_peer_id() marks channel entities as -100<id>; the realtime handler stores chat_id.
MARKED_CHANNEL_ID = -1001252615519


class FakeMessage:
    def __init__(self, message_id: int, text: str) -> None:
        self.id = message_id
        self.message = text
        self.date = datetime(2026, 9, 27, 20, 0, 51, tzinfo=UTC)
        self.media = None
        self.reply_to = None


class FakeEntity:
    def __init__(self, raw_id: int = 1252615519) -> None:
        self.id = raw_id


class FakeClient:
    """Minimal Telethon stand-in exposing get_entity/iter_messages."""

    def __init__(self, messages: list[FakeMessage], *, fail: bool = False) -> None:
        self._messages = messages
        self._fail = fail
        self.calls: list[dict[str, Any]] = []

    async def get_entity(self, channel: str) -> FakeEntity:
        if self._fail:
            raise RuntimeError("channel lookup failed")
        return FakeEntity()

    def iter_messages(self, entity: Any, **kwargs: Any) -> Any:
        self.calls.append(kwargs)

        async def generator() -> Any:
            limit = kwargs.get("limit")
            for index, message in enumerate(self._messages):
                if index >= limit:
                    break
                yield message

        return generator()


class FakeForwarder:
    def __init__(self) -> None:
        self.handled: list[tuple[int, int]] = []

    async def handle_message(self, channel_id: int, message: FakeMessage) -> None:
        self.handled.append((channel_id, message.id))


def test_catchup_ingests_only_messages_newer_than_the_cursor() -> None:
    client = FakeClient([FakeMessage(16219, "$ENA longed scalp here")])
    forwarder = FakeForwarder()

    ingested = asyncio.run(
        catch_up_missed(
            client=client,
            forwarder=forwarder,
            channels=(CHANNEL,),
            cursor_lookup=lambda _channel_id: 16218,
            peer_id_of=lambda _entity: MARKED_CHANNEL_ID,
        )
    )

    assert ingested == 1
    assert forwarder.handled == [(MARKED_CHANNEL_ID, 16219)]
    # The marked chat id must match what the realtime handler stores.
    assert client.calls[0] == {"min_id": 16218, "reverse": True, "limit": 50}


def test_cold_start_is_skipped_instead_of_replaying_history() -> None:
    client = FakeClient([FakeMessage(16000, "#OLD LONG TRADE ENTRY: 1 STOPLOSS: 0.9")])
    forwarder = FakeForwarder()

    ingested = asyncio.run(
        catch_up_missed(
            client=client,
            forwarder=forwarder,
            channels=(CHANNEL,),
            cursor_lookup=lambda _channel_id: None,
            peer_id_of=lambda _entity: MARKED_CHANNEL_ID,
        )
    )

    assert ingested == 0
    assert forwarder.handled == []
    assert client.calls == []  # history was never even requested


def test_per_run_limit_is_enforced_and_validated() -> None:
    client = FakeClient([FakeMessage(16219 + i, "x") for i in range(5)])
    forwarder = FakeForwarder()

    ingested = asyncio.run(
        catch_up_missed(
            client=client,
            forwarder=forwarder,
            channels=(CHANNEL,),
            cursor_lookup=lambda _channel_id: 16218,
            per_run_limit=2,
            peer_id_of=lambda _entity: MARKED_CHANNEL_ID,
        )
    )

    assert ingested == 2
    with pytest.raises(ValueError, match="per_run_limit"):
        asyncio.run(
            catch_up_missed(
                client=client,
                forwarder=forwarder,
                channels=(CHANNEL,),
                cursor_lookup=lambda _channel_id: 16218,
                per_run_limit=0,
                peer_id_of=lambda _entity: MARKED_CHANNEL_ID,
            )
        )


def test_loop_survives_a_failure_and_honours_the_stop_event() -> None:
    client = FakeClient([], fail=True)
    forwarder = FakeForwarder()
    stop = asyncio.Event()
    sleeps: list[float] = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            stop.set()

    asyncio.run(
        run_catchup_loop(
            interval=60,
            client=client,
            forwarder=forwarder,
            channels=(CHANNEL,),
            cursor_lookup=lambda _channel_id: 16218,
            stop_event=stop,
            peer_id_of=lambda _entity: MARKED_CHANNEL_ID,
            sleeper=sleeper,
        )
    )

    # A failing lookup must not end the loop: it kept polling, then stopped on request.
    assert len(sleeps) == 2


def test_loop_requires_a_positive_interval() -> None:
    with pytest.raises(ValueError, match="interval"):
        asyncio.run(
            run_catchup_loop(
                interval=0,
                client=FakeClient([]),
                forwarder=FakeForwarder(),
                channels=(CHANNEL,),
                cursor_lookup=lambda _channel_id: None,
            )
        )


class FakeCursor:
    def __init__(self, row: Any) -> None:
        self._row = row

    def execute(self, _statement: str, _params: Any = ()) -> None:
        return None

    def fetchone(self) -> Any:
        return self._row

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class FakeConnection:
    def __init__(self, row: Any) -> None:
        self._row = row

    def cursor(self) -> FakeCursor:
        return FakeCursor(self._row)

    def close(self) -> None:
        return None

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None


def test_cursor_lookup_reads_the_newest_persisted_message_id() -> None:
    lookup = build_cursor_lookup(lambda: FakeConnection((16218,)))

    assert lookup(MARKED_CHANNEL_ID) == 16218
    assert build_cursor_lookup(lambda: FakeConnection((None,)))(MARKED_CHANNEL_ID) is None
    assert build_cursor_lookup(lambda: FakeConnection(None))(MARKED_CHANNEL_ID) is None


class FakeIntakeClient(FakeClient):
    """Client whose disconnect returns immediately, so run_intake can be tested."""

    def __init__(self, messages: list[FakeMessage]) -> None:
        super().__init__(messages)
        self.started = False
        self.handler_attached = False

    def add_event_handler(self, _handler: Any, _event: Any) -> None:
        self.handler_attached = True

    async def start(self) -> None:
        self.started = True

    async def run_until_disconnected(self) -> None:
        # The real client blocks here, which is what lets catch-up poll alongside it.
        await asyncio.sleep(0.05)


class FakeRepository:
    def __init__(self) -> None:
        self.enqueued: list[int] = []

    def save_if_new(self, message: Any) -> Any:
        return message

    def save_and_enqueue_forward(self, message: Any) -> bool:
        self.enqueued.append(message.message_id)
        return True


def test_run_intake_arms_catchup_and_pulls_the_missed_signal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = FakeRepository()
    client = FakeIntakeClient([FakeMessage(16219, "$ENA longed scalp here")])
    environ = {
        "TELEGRAM_API_ID": "12345",
        "TELEGRAM_API_HASH": "hash",
        "TELEGRAM_SESSION": "session",
        "TELEGRAM_SOURCE_CHANNELS": CHANNEL,
        "TELEGRAM_CATCHUP_SECONDS": "0.01",
        "TELEGRAM_TARGET_CHAT_ID": "5894116684",
    }

    asyncio.run(
        run_intake(
            environ,
            client_factory=lambda _settings: client,
            repository=repository,
            connection_factory=lambda: FakeConnection((16218,)),
            peer_id_of=lambda _entity: MARKED_CHANNEL_ID,
        )
    )
    printed = capsys.readouterr().out
    assert "event=catchup-armed" in printed
    assert client.handler_attached is True
    assert repository.enqueued == [16219] or 16219 in repository.enqueued


def test_run_intake_stays_inert_without_a_connection_factory() -> None:
    client = FakeIntakeClient([])
    environ = {
        "TELEGRAM_API_ID": "12345",
        "TELEGRAM_API_HASH": "hash",
        "TELEGRAM_SESSION": "session",
        "TELEGRAM_SOURCE_CHANNELS": CHANNEL,
        "TELEGRAM_TARGET_CHAT_ID": "5894116684",
    }

    # No connection factory means no cursor to read: catch-up must not be armed, and no
    # database connection may be attempted.
    asyncio.run(
        run_intake(
            environ,
            client_factory=lambda _settings: client,
            repository=FakeRepository(),
            connection_factory=None,
        )
    )
