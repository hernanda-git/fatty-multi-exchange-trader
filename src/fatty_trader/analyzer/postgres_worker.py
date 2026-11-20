"""Durable analyzer worker: RECEIVED messages become ANALYZED + DEMO fan-out."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import closing, suppress
from datetime import UTC, datetime
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
from fatty_trader.analyzer.trade_management import (
    SourceManagement,
    looks_like_entry_signal,
    parse_source_managements,
)
from fatty_trader.intake.freshness import ANALYSIS_ELIGIBLE_SQL, SOURCE_ELIGIBLE_SQL
from fatty_trader.intake.persistence import RawTelegramMessage

_SELECT_RECEIVED = f"""
SELECT id, channel_id, message_id, revision_hash, raw_text, received_at,
       has_media, media_path, media_sha256, media_mime_type, media_size_bytes,
       ingestion_origin, entry_expires_at, entry_rejection_reason
FROM telegram_messages tm
WHERE intake_state = 'RECEIVED' AND channel_id = ANY(%s)
  AND {ANALYSIS_ELIGIBLE_SQL.format(alias="tm")}
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
_MANAGEMENT_DEADLINE_MINUTES = 30
_MANAGEMENT_INSERT = """
INSERT INTO source_management_updates
(id, source_message_id, revision, symbol, action, target_price, state, deadline_at)
VALUES (%s, %s, %s, %s, %s, %s, 'queued',
        clock_timestamp() + (%s * interval '1 minute'))
ON CONFLICT (source_message_id, revision, symbol, action) DO NOTHING
"""
_ANALYSIS_NOTIFICATION_INSERT = """
INSERT INTO notifications_outbox (id, dedup_key, payload)
VALUES (%s, %s, %s::jsonb)
ON CONFLICT (dedup_key) DO NOTHING
"""
_OPEN_SYMBOLS = """
WITH latest AS (
    SELECT DISTINCT ON (i.symbol) i.symbol, d.state
    FROM live_order_intents i
    JOIN dispatches d ON d.id = i.dispatch_id AND d.exchange = i.exchange
    WHERE i.exchange = 'bitget' AND i.role = 'ENTRY'
      AND i.entry_leg IS DISTINCT FROM 'limit' AND i.filled_qty > 0
    ORDER BY i.symbol, d.created_at DESC, d.id DESC
)
SELECT symbol FROM latest
WHERE state IN ('FILLED', 'PARTIALLY_FILLED')
ORDER BY symbol
"""


def _open_symbols(cursor: Any) -> list[str]:
    """Symbols whose newest dispatch still holds a filled position."""
    cursor.execute(_OPEN_SYMBOLS)
    return [str(row[0]) for row in cursor.fetchall()]


def _managements_for_message(
    text: str,
    *,
    signal_present: bool,
    source_veto: bool,
    image_result: dict[str, Any] | None,
) -> list[SourceManagement]:
    """Return every management instruction in a non-signal message.

    An entry announcement is a signal, not a management instruction. Without
    this gate, a caption such as "longed $TIA here | Sl at 0.3902" enrolls a
    redundant SL_MOVE for the stop the entry already carries. A message that
    names several symbols yields one instruction per symbol.
    """
    if source_veto or signal_present:
        return []
    managements = parse_source_managements(text)
    if not managements and image_result is not None and not text.strip():
        image_management = management_from_image_json(image_result)
        return [image_management] if image_management is not None else []
    return managements


def _enroll_management(cursor: Any, message_uuid: Any, revision: str, management: Any) -> list[str]:
    """Durably enroll one deterministic management instruction per target symbol.

    A symbol-less stop instruction fans out to every open position so the channel's
    "move your stoploss to entry" reminder is never silently dropped. A per-symbol
    row keeps claim, retry and provider-intent identity independent, exactly like an
    explicitly named instruction.

    Migration 35 requires every non-terminal row to carry a deadline; an insert
    without one raises the table's CHECK and dropped the instruction silently
    (LIVE 2026-10-19, source message #16333: "TP1 booked").  The deadline bounds
    the execution budget, not the source window; the executor re-checks the
    source revision before any mutation.
    """
    symbols = [management.symbol] if management.symbol else _open_symbols(cursor)
    for symbol in symbols:
        cursor.execute(
            _MANAGEMENT_INSERT,
            (
                uuid4(),
                message_uuid,
                revision,
                symbol,
                management.action.value,
                management.target_price,
                _MANAGEMENT_DEADLINE_MINUTES,
            ),
        )
    return symbols


def _notify_management(
    cursor: Any,
    *,
    message: RawTelegramMessage,
    message_uuid: Any,
    revision: str,
    managements: list[SourceManagement],
) -> None:
    """Enqueue the operator card for a management-only message.

    The card is keyed by message revision, so a later re-enrollment cannot
    duplicate it. A close that skips the analyzer still needs to say what it
    did; otherwise the entry lane's most time-critical action is silent.
    """
    cursor.execute(
        _ANALYSIS_NOTIFICATION_INSERT,
        (
            uuid4(),
            f"analysis:{message_uuid}:{revision}",
            json.dumps(
                {
                    "kind": "signal-analysis",
                    "source_message_id": message.message_id,
                    "source_text": message.raw_text,
                    "source_received_at": message.received_at.isoformat()
                    if message.received_at
                    else None,
                    "status": "MANAGEMENT",
                    "failure_class": "none",
                    "canonical_signal": False,
                    "management_action": (",".join(m.action.value for m in managements) or None),
                    "management_symbol": (",".join(m.symbol or "*" for m in managements) or None),
                    "pair": "none",
                    "direction": "none",
                    "entry": "none",
                    "stop_loss": "none",
                    "take_profits": [],
                    "dispatches": 0,
                    "exchange_count": 0,
                }
            ),
        ),
    )


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
                    # Management instructions are deterministic and time-critical:
                    # the channel's "close $RAVE here -0.5R" (LIVE 2026-11-20) was
                    # enrolled 24s after receipt, and the venue stop executed 12s
                    # after the post. Enroll known management before the vision/LLM
                    # analysis so a close never waits on a model call. Entry
                    # announcements keep the full analyzer path.
                    fast_managements = (
                        []
                        if entry_stands_down(message.raw_text)
                        or looks_like_entry_signal(message.raw_text)
                        else parse_source_managements(message.raw_text)
                    )
                    if fast_managements:
                        symbols = [
                            symbol
                            for management in fast_managements
                            for symbol in _enroll_management(
                                cursor, message_uuid, revision, management
                            )
                        ]
                        print(
                            f"[ANALYZER] msg_id={message_id} MANAGEMENT "
                            f"symbols={','.join(symbols) or 'none'} "
                            f"actions={','.join(m.action.value for m in fast_managements)} "
                            f"targets={[str(m.target_price) for m in fast_managements]}",
                            flush=True,
                        )
                        cursor.execute(_UPDATE_STATE, ("ANALYZED", message_uuid))
                        _notify_management(
                            cursor,
                            message=message,
                            message_uuid=message_uuid,
                            revision=revision,
                            managements=fast_managements,
                        )
                        cursor.execute("RELEASE SAVEPOINT analyzer_message")
                        processed += 1
                        continue
                    if len(row) >= 14:
                        cursor.execute(
                            "SELECT 1 FROM telegram_messages tm WHERE tm.id = %s AND "
                            + SOURCE_ELIGIBLE_SQL.format(alias="tm"),
                            (message_uuid,),
                        )
                        if cursor.fetchone() is None:
                            source_veto = entry_stands_down(message.raw_text)
                            managements = (
                                [] if source_veto else parse_source_managements(message.raw_text)
                            )
                            if managements:
                                symbols = [
                                    symbol
                                    for management in managements
                                    for symbol in _enroll_management(
                                        cursor, message_uuid, revision, management
                                    )
                                ]
                                print(
                                    f"[ANALYZER] msg_id={message_id} MANAGEMENT "
                                    f"symbols={','.join(symbols) or 'none'} "
                                    f"actions={','.join(m.action.value for m in managements)} "
                                    f"targets={[str(m.target_price) for m in managements]}",
                                    flush=True,
                                )
                                cursor.execute(_UPDATE_STATE, ("ANALYZED", message_uuid))
                                cursor.execute("RELEASE SAVEPOINT analyzer_message")
                                processed += 1
                                continue
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
                        cursor.execute(
                            "UPDATE telegram_messages SET image_status=%s WHERE id=%s",
                            (image_status, message_uuid),
                        )
                        if image_failure:
                            _alert_vision_failure(
                                cursor, message.message_id, str(image_failure)
                            )
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
                            image_signal = signal_from_image_json(
                                image_result,
                                message_id=message.message_id,
                                source_revision=revision,
                            )
                            if image_signal is None and not entry_stands_down(message.raw_text):
                                setup_action = str(
                                    (image_result.get("setup") or {}).get("action") or ""
                                ).upper()
                                if setup_action == "ENTER" or not setup_action:
                                    explicit_signal = parse_explicit_signal(
                                        message.raw_text,
                                        message_id=message.message_id,
                                        market_price_lookup=lambda _: None,
                                    )
                                    if explicit_signal is not None:
                                        result = AnalysisResult(
                                            AnalysisStatus.FALLBACK_ACCEPTED,
                                            explicit_signal,
                                            "image produced no signal; accepted text fallback",
                                        )
                                    else:
                                        result = AnalysisResult(
                                            AnalysisStatus.CODEX_SUCCEEDED,
                                            None,
                                        )
                                else:
                                    result = AnalysisResult(
                                        AnalysisStatus.CODEX_SUCCEEDED,
                                        None,
                                    )
                            elif image_signal is None:
                                result = AnalysisResult(
                                    AnalysisStatus.CODEX_SUCCEEDED,
                                    None,
                                )
                            else:
                                result = AnalysisResult(
                                    AnalysisStatus.CODEX_SUCCEEDED,
                                    image_signal,
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
                            source_veto = entry_stands_down(message.raw_text)
                            managements = (
                                [] if source_veto else parse_source_managements(message.raw_text)
                            )
                            if managements:
                                symbols = [
                                    symbol
                                    for management in managements
                                    for symbol in _enroll_management(
                                        cursor, message_uuid, revision, management
                                    )
                                ]
                                print(
                                    f"[ANALYZER] msg_id={message_id} MANAGEMENT "
                                    f"symbols={','.join(symbols) or 'none'} "
                                    f"actions={','.join(m.action.value for m in managements)} "
                                    f"targets={[str(m.target_price) for m in managements]}",
                                    flush=True,
                                )
                                cursor.execute(_UPDATE_STATE, ("ANALYZED", message_uuid))
                                cursor.execute("RELEASE SAVEPOINT analyzer_message")
                                processed += 1
                                continue
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
                    # An entry message is a signal, not a management instruction.
                    # The channel's "longed $TIA here | Sl at 0.3902" form would
                    # otherwise enroll a redundant SL_MOVE for the same message.
                    managements = _managements_for_message(
                        message.raw_text,
                        signal_present=result.signal is not None,
                        source_veto=source_veto,
                        image_result=image_result,
                    )
                    if managements:
                        symbols = [
                            symbol
                            for management in managements
                            for symbol in _enroll_management(
                                cursor, message_uuid, revision, management
                            )
                        ]
                        print(
                            f"[ANALYZER] msg_id={message_id} MANAGEMENT "
                            f"symbols={','.join(symbols) or 'none'} "
                            f"actions={','.join(m.action.value for m in managements)} "
                            f"targets={[str(m.target_price) for m in managements]}",
                            flush=True,
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
                        print(
                            f"[ANALYZER] msg_id={message_id} SIGNAL symbol={signal.pair_token} "
                            f"dir={signal.direction.value} entry={signal.entry_price} "
                            f"sl={signal.stop_loss} targets={signal.take_profits} "
                            f"dispatches={dispatches}",
                            flush=True,
                        )
                    if not managements and result.signal is None:
                        preview = message.raw_text.replace("\n", " ")[:60]
                        print(
                            f"[ANALYZER] msg_id={message_id} NO_ACTION text={preview!r}",
                            flush=True,
                        )
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
                                        ",".join(m.action.value for m in managements) or None
                                    ),
                                    "management_symbol": (
                                        ",".join(m.symbol or "*" for m in managements) or None
                                    ),
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
                    failure = f"analysis-failed:{type(exc).__name__}"
                    cursor.execute(
                        "UPDATE telegram_messages SET intake_state='FAILED', "
                        "entry_rejection_reason=COALESCE(entry_rejection_reason, %s) "
                        "WHERE id=%s",
                        (failure, message_uuid),
                    )
                    # A deterministic management instruction does not depend on the model.
                    # Re-enroll it here so a failure earlier in the transaction (even the
                    # enrollment itself) cannot drop the source's instruction. The FAILED
                    # state and reason above remain the primary evidence if this retry or
                    # the alert insert cannot be written either.
                    with suppress(Exception):
                        fallback_managements = (
                            []
                            if entry_stands_down(message.raw_text)
                            else parse_source_managements(message.raw_text)
                        )
                        for fallback_management in fallback_managements:
                            _enroll_management(cursor, message_uuid, revision, fallback_management)
                    # Never silent: one durable card per failed message revision.
                    with suppress(Exception):
                        cursor.execute(
                            _ANALYSIS_NOTIFICATION_INSERT,
                            (
                                uuid4(),
                                f"analysis-failed:{message_uuid}:{revision}",
                                json.dumps(
                                    {
                                        "kind": "system-event",
                                        "message": (
                                            f"Analyzer failed · source #{message_id} · "
                                            f"error={type(exc).__name__}"
                                        ),
                                    }
                                ),
                            ),
                        )
                    print(
                        f"service=analyzer state=message-failed message_id={message_id} "
                        f"reason={failure} error={exc!r}",
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


def _alert_vision_failure(cursor: Any, message_id: int, reason: str) -> None:
    """One card per hour while vision is down, never one per message."""
    payload = {
        "kind": "system-event",
        "state": "warning",
        "message": f"Vision analysis failed: {reason}",
        "reasons": ["warning:vision-analysis-failed"],
        "message_id": message_id,
    }
    bucket = datetime.now(UTC).strftime("%Y-%m-%dT%H")
    cursor.execute("SAVEPOINT vision_alert")
    try:
        cursor.execute(
            "INSERT INTO notifications_outbox (id, dedup_key, payload) "
            "VALUES (%s, %s, %s::jsonb) ON CONFLICT (dedup_key) DO NOTHING",
            (str(uuid4()), f"vision-failed:{bucket}", json.dumps(payload, sort_keys=True)),
        )
        cursor.execute("RELEASE SAVEPOINT vision_alert")
    except Exception:
        # Alerting must never fail the analysis transaction.
        cursor.execute("ROLLBACK TO SAVEPOINT vision_alert")


def _image_runner(
    runner: Callable[[str], CodexRunResult] | CodexRunner,
) -> Callable[[str, tuple[str, ...]], CodexRunResult]:
    if isinstance(runner, CodexRunner):
        return lambda prompt, image_paths: runner.run(prompt, image_paths=image_paths)
    return runner  # type: ignore[return-value]
