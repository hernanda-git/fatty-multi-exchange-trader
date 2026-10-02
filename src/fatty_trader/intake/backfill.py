"""One-shot source-channel baseline for durable operator telemetry."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from typing import Any

import psycopg

from fatty_trader.config.telegram import TelegramSettings, channel_ref
from fatty_trader.intake.catchup import telegram_peer_id
from fatty_trader.intake.persistence import PostgresRawMessageRepository
from fatty_trader.intake.telegram import TelegramIntake
from fatty_trader.intake.telethon_client import build_telethon_client


async def backfill_latest(
    environ: Mapping[str, str],
    *,
    client_factory: Any = build_telethon_client,
    repository: Any = None,
    peer_id_of: Any = telegram_peer_id,
) -> int:
    """Persist the newest message from every configured source channel once."""
    settings = TelegramSettings.from_mapping(environ)
    client = client_factory(settings)
    intake = TelegramIntake(repository or PostgresRawMessageRepository(psycopg.connect))
    saved = 0
    await client.start()
    try:
        for channel in settings.channels:
            entity = await client.get_entity(channel_ref(channel))
            async for message in client.iter_messages(entity, limit=1):
                # Marked id (-100<id>): the realtime handler and catch-up both store
                # event.chat_id, so the raw entity id would never match the cursor.
                intake.ingest(channel_id=peer_id_of(entity), message=message, origin="backfill")
                saved += 1
    finally:
        await client.disconnect()
    return saved


def main() -> int:
    saved = asyncio.run(backfill_latest(os.environ))
    print(f"telegram_baseline_messages={saved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
