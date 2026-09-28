"""Catch-up pass for source-channel messages the realtime handler never saw.

The intake listens on Telethon push updates only, so a dropped or stolen MTProto session
silently stops ingestion and every message in that window is lost forever. Incident
2026-09-27: the session was shared with a host listener, the update stream died at
17:45:47Z, and the source's 20:00:51Z ENA signal never reached the database.

This module polls each configured channel for messages newer than the newest message
already persisted. It deliberately does nothing on a cold start (no cursor for a channel)
because fetching history there would replay old signals as if they were live; realtime
ingestion owns the first sighting of a channel.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import Any

from telethon.utils import get_peer_id


def telegram_peer_id(entity: Any) -> int:
    """Marked channel id (-100<id>), exactly what the realtime handler stores as chat_id.

    The realtime path stores ``event.chat_id``; if catch-up used the raw entity id the
    cursor lookup would never match a persisted row.
    """
    return int(get_peer_id(entity))


def build_cursor_lookup(connection_factory: Callable[[], Any]) -> Callable[[int], int | None]:
    """Return a callable giving the newest persisted message id for a channel."""

    def lookup(channel_id: int) -> int | None:
        from contextlib import closing

        with closing(connection_factory()) as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT max(message_id) FROM telegram_messages WHERE channel_id = %s",
                (channel_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        value = row.get("max") if isinstance(row, dict) else row[0]
        return int(value) if value is not None else None

    return lookup


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
        entity = await client.get_entity(channel)
        channel_id = peer_id_of(entity)
        cursor = cursor_lookup(channel_id)
        if cursor is None:
            continue
        async for message in client.iter_messages(
            entity, min_id=cursor, reverse=True, limit=per_run_limit
        ):
            await forwarder.handle_message(channel_id, message)
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
