"""Catch-up pass for source-channel messages the realtime handler never saw.

The intake listens on Telethon push updates only, so a dropped or stolen MTProto session
silently stops ingestion and every message in that window is lost forever. Incident
2026-09-27: the session was shared with a host listener, the update stream died at
17:45:47Z, and the source's 20:00:51Z ENA signal never reached the database.

This module polls each configured channel after an independent history-coverage
watermark, never the maximum realtime ID. It deliberately does nothing on a cold start
(no coverage baseline): the first persisted source sighting establishes that baseline.
Reverse polling advances coverage only after durable handling; deleted Telegram IDs
need not be manufactured or treated as unfillable numeric holes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import Any

from telethon.utils import get_peer_id

from fatty_trader.config.telegram import channel_ref


def telegram_peer_id(entity: Any) -> int:
    """Marked channel id (-100<id>), exactly what the realtime handler stores as chat_id.

    The realtime path stores ``event.chat_id``; if catch-up used the raw entity id the
    cursor lookup would never match a persisted row.
    """
    return int(get_peer_id(entity))


class PostgresCatchupCoverage:
    """Independent history coverage; realtime inserts must never advance it."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def __call__(self, channel_id: int) -> int | None:
        from contextlib import closing

        with closing(self._connection_factory()) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT covered_message_id FROM telegram_catchup_coverage WHERE channel_id = %s",
                (channel_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        value = row.get("covered_message_id") if isinstance(row, dict) else row[0]
        return int(value) if value is not None else None

    def advance(self, channel_id: int, message_id: int) -> None:
        from contextlib import closing

        with closing(self._connection_factory()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """UPDATE telegram_catchup_coverage
                    SET covered_message_id = greatest(covered_message_id, %s)
                    WHERE channel_id = %s""",
                    (message_id, channel_id),
                )
            connection.commit()


def build_cursor_lookup(connection_factory: Callable[[], Any]) -> PostgresCatchupCoverage:
    return PostgresCatchupCoverage(connection_factory)


async def catch_up_missed(
    *,
    client: Any,
    forwarder: Any,
    channels: Sequence[str],
    cursor_lookup: Callable[[int], int | None],
    per_run_limit: int = 50,
    peer_id_of: Callable[[Any], int] = telegram_peer_id,
) -> int:
    """Ingest messages newer than the persisted cursor. Returns how many were handled."""
    if per_run_limit < 1:
        raise ValueError("per_run_limit must be positive")
    ingested = 0
    for channel in channels:
        entity = await client.get_entity(channel_ref(channel))
        channel_id = peer_id_of(entity)
        cursor = cursor_lookup(channel_id)
        if cursor is None:
            continue
        async for message in client.iter_messages(
            entity, min_id=cursor, reverse=True, limit=per_run_limit
        ):
            handler = getattr(forwarder, "handle_historical_message", forwarder.handle_message)
            await handler(channel_id, message)
            # A failed persistence/advance retries safely; duplicates are idempotent.
            # Reverse history enumerates extant IDs, so deleted numeric IDs are not gaps.
            advance = getattr(cursor_lookup, "advance", None)
            if advance is not None:
                advance(channel_id, int(message.id))
            ingested += 1
    return ingested


async def run_catchup_loop(
    *,
    interval: float,
    client: Any,
    forwarder: Any,
    channels: Sequence[str],
    cursor_lookup: Callable[[int], int | None],
    per_run_limit: int = 50,
    stop_event: asyncio.Event | None = None,
    sleeper: Callable[[float], Any] = asyncio.sleep,
    peer_id_of: Callable[[Any], int] = telegram_peer_id,
) -> None:
    """Poll for missed messages until stopped; a failure must never kill the listener."""
    if interval <= 0:
        raise ValueError("catch-up interval must be positive")
    while stop_event is None or not stop_event.is_set():
        try:
            ingested = await catch_up_missed(
                client=client,
                forwarder=forwarder,
                channels=channels,
                cursor_lookup=cursor_lookup,
                per_run_limit=per_run_limit,
                peer_id_of=peer_id_of,
            )
            if ingested:
                print(
                    f"service=intake event=catchup-ingested count={ingested}",
                    flush=True,
                )
        except Exception as exc:  # noqa: BLE001 - catch-up is best effort by design
            print(
                f"service=intake event=catchup-failed error={exc!r}",
                flush=True,
            )
        await sleeper(interval)
