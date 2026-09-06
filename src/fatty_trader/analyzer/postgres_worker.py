"""Durable analyzer worker: RECEIVED messages become ANALYZED + DEMO fan-out."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import closing
from typing import Any
from uuid import uuid4

from fatty_trader.analyzer.codex_runner import CodexRunner, CodexRunResult
from fatty_trader.analyzer.integration import analyze_with_fallback
from fatty_trader.intake.persistence import RawTelegramMessage

_SELECT_RECEIVED = """
SELECT id, channel_id, message_id, revision_hash, raw_text, received_at
FROM telegram_messages
WHERE intake_state = 'RECEIVED'
ORDER BY received_at, message_id
FOR UPDATE SKIP LOCKED
LIMIT %s
"""
_UPDATE_STATE = "UPDATE telegram_messages SET intake_state = %s WHERE id = %s"
_SIGNAL_INSERT = """
INSERT INTO canonical_signals
(id, message_id, revision, pair_token, direction, entry_price, stop_loss, take_profits)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT (message_id, revision) DO NOTHING
"""
_DISPATCH_INSERT = """
INSERT INTO dispatches
(id, source_type, source_id, revision, exchange, state)
VALUES (%s, 'canonical_signal', %s, %s, %s, 'QUEUED')
ON CONFLICT (source_type, source_id, revision, exchange) DO NOTHING
"""
_ANALYSIS_NOTIFICATION_INSERT = """
INSERT INTO notifications_outbox (id, dedup_key, payload)
VALUES (%s, %s, %s::jsonb)
ON CONFLICT (dedup_key) DO NOTHING
"""


def process_received_batch(
    connection_factory: Callable[[], Any],
    *,
    runner: Callable[[str], CodexRunResult] | CodexRunner | None = None,
    limit: int = 10,
) -> int:
    """Process one bounded transaction. Dispatch rows are DEMO intents only."""
    if limit < 1:
        raise ValueError("limit must be positive")
    analysis_runner = runner or CodexRunner()
    processed = 0
    with closing(connection_factory()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(_SELECT_RECEIVED, (limit,))
            rows = cursor.fetchall()
            for row in rows:
                message_uuid, channel_id, message_id, revision, raw_text, received_at = row
                message = RawTelegramMessage(
                    channel_id=channel_id,
                    message_id=message_id,
                    revision_hash=revision,
                    raw_text=raw_text,
                    received_at=received_at,
                )
                try:
                    result = analyze_with_fallback(
                        text=message.raw_text,
                        message_id=message.message_id,
                        codex_runner=_runner_callable(analysis_runner),
                    )
                    signal_id = None
                    if result.signal is not None:
                        signal = result.signal.model_copy(update={"source_revision": revision})
                        signal_id = uuid4()
                        cursor.execute(
                            _SIGNAL_INSERT,
                            (
                                signal_id,
                                message_uuid,
                                revision,
                                signal.pair_token,
                                signal.direction.value,
                                signal.entry_price,
                                signal.stop_loss,
                                json.dumps([str(target) for target in signal.take_profits]),
                            ),
                        )
                        for exchange in ("binance", "bitget"):
                            cursor.execute(
                                _DISPATCH_INSERT,
                                (uuid4(), signal_id, revision, exchange),
                            )
                    cursor.execute(
                        _ANALYSIS_NOTIFICATION_INSERT,
                        (
                            uuid4(),
                            f"analysis:{message_uuid}:{revision}",
                            json.dumps(
                                {
                                    "kind": "signal-analysis",
                                    "source_message_id": message_id,
                                    "source_revision": revision,
                                    "status": result.status.value,
                                    "failure_class": result.failure_class or "none",
                                    "canonical_signal": signal_id is not None,
                                    "pair": result.signal.pair_token if result.signal else "none",
                                    "direction": result.signal.direction.value
                                    if result.signal
                                    else "none",
                                    "entry": str(result.signal.entry_price)
                                    if result.signal
                                    else "none",
                                    "stop_loss": str(result.signal.stop_loss)
                                    if result.signal
                                    else "none",
                                    "take_profits": [str(v) for v in result.signal.take_profits]
                                    if result.signal
                                    else [],
                                    "dispatches": 2 if signal_id is not None else 0,
                                }
                            ),
                        ),
                    )
                    cursor.execute(_UPDATE_STATE, ("ANALYZED", message_uuid))
                except Exception:
                    cursor.execute(_UPDATE_STATE, ("FAILED", message_uuid))
                processed += 1
        connection.commit()
    return processed


def _runner_callable(
    runner: Callable[[str], CodexRunResult] | CodexRunner,
) -> Callable[[str], CodexRunResult]:
    if callable(runner):
        return runner
    return runner.run
