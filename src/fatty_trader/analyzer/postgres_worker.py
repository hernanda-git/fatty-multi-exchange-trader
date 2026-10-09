"""Durable analyzer worker: RECEIVED messages become ANALYZED + DEMO fan-out."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import closing
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from fatty_trader.analyzer.codex_runner import CodexRunner, CodexRunResult
from fatty_trader.analyzer.deterministic_parser import parse_explicit_signal
from fatty_trader.analyzer.image_analysis import (
    analyze_image_json,
    management_from_image_json,
    signal_from_image_json,
)
from fatty_trader.analyzer.integration import AnalysisResult, AnalysisStatus, analyze_with_fallback
from fatty_trader.analyzer.source_guard import entry_stands_down
from fatty_trader.analyzer.trade_management import parse_source_management
from fatty_trader.intake.freshness import SOURCE_ELIGIBLE_SQL
from fatty_trader.intake.persistence import RawTelegramMessage

_SELECT_RECEIVED = f"""
SELECT id, channel_id, message_id, revision_hash, raw_text, received_at,
       has_media, media_path, media_sha256, media_mime_type, media_size_bytes,
       ingestion_origin, entry_expires_at, entry_rejection_reason
FROM telegram_messages tm
WHERE intake_state = 'RECEIVED' AND channel_id = ANY(%s)
  AND {SOURCE_ELIGIBLE_SQL.format(alias="tm")}
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
_SIGNAL_ID_SELECT = """
SELECT id FROM canonical_signals WHERE message_id = %s AND revision = %s
"""
_DISPATCH_INSERT = """
INSERT INTO dispatches
(id, source_type, source_id, revision, exchange, state)
VALUES (%s, 'canonical_signal', %s, %s, %s, 'QUEUED')
ON CONFLICT (source_type, source_id, revision, exchange) DO NOTHING
RETURNING id
"""
_MANAGEMENT_INSERT = """
INSERT INTO source_management_updates
(id, source_message_id, revision, symbol, action, state)
VALUES (%s, %s, %s, %s, %s, 'queued')
ON CONFLICT (source_message_id, revision, symbol, action) DO NOTHING
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
    exchanges: tuple[str, ...] = ("binance", "bitget"),
    image_analysis_enabled: bool = False,
    channel_ids: tuple[int, ...] = (-1001252615519,),
) -> int:
    """Process one bounded transaction and fan out only to enabled engines.

    ``channel_ids`` keeps this worker on the channels that are allowed to reach the live
    lane. Adding a new source channel (for example the paper-only Kaka channel) must never
    let its messages create live dispatches by accident.
    """
    """Process one bounded transaction and fan out only to enabled engines."""
    if limit < 1:
        raise ValueError("limit must be positive")
    if not exchanges or any(exchange not in {"binance", "bitget"} for exchange in exchanges):
        raise ValueError("exchanges must contain enabled supported engines")
    analysis_runner = runner or CodexRunner()
    processed = 0
    with closing(connection_factory()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(_SELECT_RECEIVED, (list(channel_ids), limit))
            rows = cursor.fetchall()
            for row in rows:
                if len(row) >= 11:
                    (
                        message_uuid,
                        channel_id,
                        message_id,
                        revision,
                        raw_text,
                        received_at,
                        has_media,
                        media_path,
                        media_sha256,
                        media_mime_type,
                        media_size_bytes,
                    ) = row[:11]
                else:
                    message_uuid, channel_id, message_id, revision, raw_text, received_at = row
                    has_media = False
                    media_path = media_sha256 = media_mime_type = media_size_bytes = None
                eligibility = (
                    dict(
                        zip(
                            ("ingestion_origin", "entry_expires_at", "entry_rejection_reason"),
                            row[11:14],
                            strict=True,
                        )
                    )
                    if len(row) >= 14
                    else {}
                )
                message = RawTelegramMessage(
                    channel_id=channel_id,
                    message_id=message_id,
                    revision_hash=revision,
                    raw_text=raw_text,
                    received_at=received_at,
                    has_media=has_media,
                    media_path=media_path,
                    media_sha256=media_sha256,
                    media_mime_type=media_mime_type,
                    media_size_bytes=media_size_bytes,
                    **eligibility,
                )
                cursor.execute("SAVEPOINT analyzer_message")
                try:
                    if len(row) >= 14:
                        cursor.execute(
                            "SELECT 1 FROM telegram_messages tm WHERE tm.id = %s AND "
                            + SOURCE_ELIGIBLE_SQL.format(alias="tm"),
                            (message_uuid,),
                        )
                        if cursor.fetchone() is None:
                            cursor.execute(
                                "UPDATE telegram_messages SET intake_state='EXPIRED', "
                                "entry_rejection_reason=COALESCE(entry_rejection_reason, "
                                "'stale-source-message') WHERE id=%s",
                                (message_uuid,),
                            )
                            cursor.execute("RELEASE SAVEPOINT analyzer_message")
                            processed += 1
                            continue
                    image_result: dict[str, Any] | None = None
                    image_status: str | None = None
                    if image_analysis_enabled and message.has_media and message.media_path:
                        try:
                            image_result = analyze_image_json(
                                text=message.raw_text,
                                message_id=message.message_id,
                                image_path=message.media_path,
                                runner=_image_runner(analysis_runner),
                            )
                        except OSError:
                            image_result = {"message": "Image analysis unavailable", "setup": {}}
                        image_failure = (
                            image_result.get("message")
                            if not image_result.get("setup")
                            and image_result.get("message")
                            in {
                                "Image analysis unavailable",
                                "Image analysis returned invalid JSON",
                            }
                            else None
                        )
                        image_status = "FAILED" if image_failure else "SUCCEEDED"
                        if image_failure:
                            # A failed image is not successful analysis. Accept only explicit
                            # source text; no second model call or implicit market-price I/O.
                            explicit_signal = parse_explicit_signal(
                                message.raw_text,
                                message_id=message.message_id,
                                market_price_lookup=lambda _: None,
                            )
                            result = AnalysisResult(
                                AnalysisStatus.FALLBACK_ACCEPTED
                                if explicit_signal is not None
                                else AnalysisStatus.MANUAL_REVIEW,
                                explicit_signal,
                                str(image_failure),
                            )
                        else:
                            result = AnalysisResult(
                                AnalysisStatus.CODEX_SUCCEEDED,
                                signal_from_image_json(
                                    image_result,
                                    message_id=message.message_id,
                                    source_revision=revision,
                                ),
                            )
                    else:
                        result = analyze_with_fallback(
                            text=message.raw_text,
                            message_id=message.message_id,
                            codex_runner=_runner_callable(analysis_runner),
                        )
                    if len(row) >= 14:
                        cursor.execute(
                            "SELECT 1 FROM telegram_messages tm WHERE tm.id = %s AND "
                            + SOURCE_ELIGIBLE_SQL.format(alias="tm"),
                            (message_uuid,),
                        )
                        if cursor.fetchone() is None:
                            cursor.execute(
                                "UPDATE telegram_messages SET intake_state='EXPIRED', "
                                "entry_rejection_reason=COALESCE(entry_rejection_reason, "
                                "'stale-source-message') WHERE id=%s",
                                (message_uuid,),
                            )
                            cursor.execute("RELEASE SAVEPOINT analyzer_message")
                            processed += 1
                            continue
                    source_veto = entry_stands_down(message.raw_text)
                    if source_veto:
                        result = AnalysisResult(result.status, None, "source caption stands down")
                    management = None if source_veto else parse_source_management(message.raw_text)
                    # None from the source parser is not permission to resurrect a negated
                    # instruction via image JSON. Captioned management requires explicit text.
                    if (
                        management is None
                        and image_result is not None
                        and not message.raw_text.strip()
                    ):
                        management = management_from_image_json(image_result)
                    if management is not None:
                        cursor.execute(
                            _MANAGEMENT_INSERT,
                            (
                                uuid4(),
                                message_uuid,
                                revision,
                                management.symbol,
                                management.action.value,
                            ),
                        )
                    signal_id = None
                    dispatches = 0
                    if result.signal is not None:
                        signal = result.signal.model_copy(update={"source_revision": revision})
                        signal_id = uuid5(
                            NAMESPACE_URL, f"fatty-canonical:{message_uuid}:{revision}"
                        )
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
                        cursor.execute(_SIGNAL_ID_SELECT, (message_uuid, revision))
                        signal_row = cursor.fetchone()
                        if signal_row is None:
                            raise ValueError("canonical signal insert/read-back failed")
                        signal_id = (
                            signal_row["id"] if isinstance(signal_row, dict) else signal_row[0]
                        )
                        dispatches = 0
                        for exchange in exchanges:
                            cursor.execute(
                                _DISPATCH_INSERT,
                                (uuid4(), signal_id, revision, exchange),
                            )
                            # Count the rows actually written. Reporting len(exchanges)
                            # claimed a dispatch even when the insert conflicted, which
                            # is how three signals lost their fan-out unnoticed.
                            if cursor.fetchone() is not None:
                                dispatches += 1
                    cursor.execute(
                        _ANALYSIS_NOTIFICATION_INSERT,
                        (
                            uuid4(),
                            f"analysis:{message_uuid}:{revision}",
                            json.dumps(
                                {
                                    **(image_result or {}),
                                    "kind": "signal-analysis",
                                    "image_analysis_status": image_status,
                                    "source_message_id": message_id,
                                    "source_text": message.raw_text,
                                    "source_received_at": message.received_at.isoformat()
                                    if message.received_at
                                    else None,
                                    "status": result.status.value,
                                    "failure_class": result.failure_class or "none",
                                    "canonical_signal": signal_id is not None,
                                    "management_action": (
                                        management.action.value if management else None
                                    ),
                                    "management_symbol": management.symbol if management else None,
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
                                    "dispatches": dispatches if signal_id is not None else 0,
                                    "exchange_count": len(exchanges),
                                    "exchanges": list(exchanges),
                                }
                            ),
                        ),
                    )
                    cursor.execute(_UPDATE_STATE, ("ANALYZED", message_uuid))
                except Exception as exc:  # noqa: BLE001 - one bad message must not kill the batch
                    # Restore transaction usability and erase this row's partial fan-out.
                    cursor.execute("ROLLBACK TO SAVEPOINT analyzer_message")
                    cursor.execute(_UPDATE_STATE, ("FAILED", message_uuid))
                    print(
                        f"service=analyzer state=message-failed message_id={message_id} "
                        f"error={exc!r}",
                        flush=True,
                    )
                cursor.execute("RELEASE SAVEPOINT analyzer_message")
                processed += 1
        connection.commit()
    return processed


def _runner_callable(
    runner: Callable[[str], CodexRunResult] | CodexRunner,
) -> Callable[[str], CodexRunResult]:
    if callable(runner):
        return runner
    return runner.run


def _image_runner(
    runner: Callable[[str], CodexRunResult] | CodexRunner,
) -> Callable[[str, tuple[str, ...]], CodexRunResult]:
    if isinstance(runner, CodexRunner):
        return lambda prompt, image_paths: runner.run(prompt, image_paths=image_paths)
    return runner  # type: ignore[return-value]
