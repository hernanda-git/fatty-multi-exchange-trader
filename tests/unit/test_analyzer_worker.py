from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

from fatty_trader.analyzer.codex_runner import CodexRunResult
from fatty_trader.analyzer.postgres_worker import process_received_batch


class Cursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, object]] = []
        self.signal_id: object | None = None
        self.rows: list[tuple[object, ...]] = [
            (
                uuid4(),
                7,
                42,
                "a" * 64,
                "#ETH LONG ENTRY: 100 TARGET: 110 STOPLOSS: 95",
                datetime.now(UTC),
            )
        ]

    def __enter__(self) -> Cursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: str, params: object = ()) -> None:
        self.executed.append((statement, params))
        if "INSERT INTO canonical_signals" in statement:
            self.signal_id = cast(tuple[Any, ...], params)[0]

    def fetchone(self) -> tuple[object, ...] | None:
        return (self.signal_id,) if self.signal_id is not None else None

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


class Connection:
    def __init__(self, cursor: Cursor) -> None:
        self.cursor_obj = cursor
        self.commits = 0

    def __enter__(self) -> Connection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def cursor(self) -> Cursor:
        return self.cursor_obj

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        return None


def test_process_received_batch_persists_analysis_and_two_paper_dispatches() -> None:
    cursor = Cursor()
    connection = Connection(cursor)
    result = process_received_batch(
        lambda: connection,
        runner=lambda _: CodexRunResult(False, True, False, 1, "unavailable", "", ""),
    )

    assert result == 1
    statements = "\n".join(statement for statement, _ in cursor.executed)
    assert "SELECT id, channel_id, message_id" in statements
    assert "isfinite(tm.received_at)" in statements
    assert "isfinite(tm.entry_expires_at)" in statements
    assert "tm.ingestion_origin IN" in statements
    assert statements.count("INSERT INTO dispatches") == 2
    assert "INSERT INTO canonical_signals" in statements
    assert "UPDATE telegram_messages" in statements
    assert connection.commits == 1


def test_process_received_batch_uses_existing_canonical_id_on_conflict() -> None:
    existing_id = uuid4()

    class ExistingCanonicalCursor(Cursor):
        def execute(self, statement: str, params: object = ()) -> None:
            super().execute(statement, params)
            if "SELECT id FROM canonical_signals" in statement:
                self.signal_id = existing_id

    cursor = ExistingCanonicalCursor()
    connection = Connection(cursor)

    assert (
        process_received_batch(
            lambda: connection,
            runner=lambda _: CodexRunResult(False, True, False, 1, "unavailable", "", ""),
            exchanges=("bitget",),
        )
        == 1
    )
    dispatch_params = [
        params for statement, params in cursor.executed if "INSERT INTO dispatches" in statement
    ]
    assert len(dispatch_params) == 1
    assert cast(tuple[Any, ...], dispatch_params[0])[1] == existing_id


def test_process_received_batch_dispatches_only_to_enabled_engine() -> None:
    cursor = Cursor()
    connection = Connection(cursor)

    result = process_received_batch(
        lambda: connection,
        runner=lambda _: CodexRunResult(False, True, False, 1, "unavailable", "", ""),
        exchanges=("bitget",),
    )

    assert result == 1
    dispatch_params = [
        params for statement, params in cursor.executed if "INSERT INTO dispatches" in statement
    ]
    assert len(dispatch_params) == 1
    assert cast(tuple[Any, ...], dispatch_params[0])[-1] == "bitget"


def media_message_cursor(*, media_path: str | None = None) -> Cursor:
    """A RECEIVED source message that carries media, the shape that lost dispatches.

    Audit 2026-09-27: three canonical signals (WLD/NEAR/PENGU) had zero dispatches and
    all three came from messages with has_media set, while every non-media signal had
    one. The analysis notification claimed ``dispatches: 1`` because it reported
    ``len(exchanges)`` rather than the rows actually inserted, so the loss was invisible.
    """

    cursor = Cursor()
    cursor.rows = [
        (
            uuid4(),
            7,
            4242,
            "b" * 64,
            "#WLD LONG ENTRY: 1 TARGET: 1.1 STOPLOSS: 0.95",
            datetime.now(UTC),
            True,
            media_path,
            "c" * 64 if media_path else None,
            "image/png" if media_path else None,
            1024 if media_path else None,
        )
    ]
    return cursor


def test_media_message_still_fans_out_one_dispatch_per_enabled_engine() -> None:
    """A canonical signal must never exist without at least one dispatch to execute."""

    cursor = media_message_cursor()
    connection = Connection(cursor)

    assert (
        process_received_batch(
            lambda: connection,
            runner=lambda _: CodexRunResult(False, True, False, 1, "unavailable", "", ""),
            exchanges=("bitget",),
        )
        == 1
    )

    statements = "\n".join(statement for statement, _ in cursor.executed)
    assert "INSERT INTO canonical_signals" in statements
    dispatch_params = [
        params for statement, params in cursor.executed if "INSERT INTO dispatches" in statement
    ]
    assert len(dispatch_params) == 1
    assert cast(tuple[Any, ...], dispatch_params[0])[-1] == "bitget"


def test_analysis_notification_reports_written_dispatches_not_intent() -> None:
    """The outbox must never claim a dispatch that no row backs.

    Audit 2026-09-27: three canonical signals had zero dispatches while their
    notification payload reported ``dispatches: 1``, because the payload used
    ``len(exchanges)``. Here the dispatch insert conflicts (fetchone -> None) and the
    payload must say so.
    """

    class ConflictingDispatchCursor(Cursor):
        def __init__(self) -> None:
            super().__init__()
            self._signal_reads = 0

        def fetchone(self) -> tuple[object, ...] | None:
            if self.signal_id is None:
                return None
            self._signal_reads += 1
            # First read is the canonical-signal read-back; later reads are the
            # dispatch insert's RETURNING, which yields nothing on conflict.
            return (self.signal_id,) if self._signal_reads == 1 else None

    cursor = ConflictingDispatchCursor()
    connection = Connection(cursor)

    assert (
        process_received_batch(
            lambda: connection,
            runner=lambda _: CodexRunResult(False, True, False, 1, "unavailable", "", ""),
            exchanges=("bitget",),
        )
        == 1
    )

    notifications = [
        params
        for statement, params in cursor.executed
        if "INSERT INTO notifications_outbox" in statement
    ]
    assert len(notifications) == 1
    payload = json.loads(cast(tuple[Any, ...], notifications[0])[2])
    assert payload["canonical_signal"] is True
    assert payload["dispatches"] == 0
    assert payload["exchange_count"] == 1
    assert payload["exchanges"] == ["bitget"]


def test_analysis_notification_counts_the_dispatch_row_that_was_written() -> None:
    cursor = Cursor()
    connection = Connection(cursor)

    assert (
        process_received_batch(
            lambda: connection,
            runner=lambda _: CodexRunResult(False, True, False, 1, "unavailable", "", ""),
            exchanges=("bitget",),
        )
        == 1
    )

    notifications = [
        params
        for statement, params in cursor.executed
        if "INSERT INTO notifications_outbox" in statement
    ]
    payload = json.loads(cast(tuple[Any, ...], notifications[0])[2])
    assert payload["dispatches"] == 1


def test_media_message_dispatches_even_with_image_analysis_enabled() -> None:
    """Image analysis must not replace the fan-out; a media message is still dispatched."""

    cursor = media_message_cursor()
    connection = Connection(cursor)

    assert (
        process_received_batch(
            lambda: connection,
            runner=lambda _: CodexRunResult(False, True, False, 1, "unavailable", "", ""),
            exchanges=("bitget",),
            image_analysis_enabled=True,
        )
        == 1
    )

    dispatch_params = [
        params for statement, params in cursor.executed if "INSERT INTO dispatches" in statement
    ]
    assert len(dispatch_params) == 1


def test_worker_rechecks_source_eligibility_before_model_or_market_io():
    from datetime import timedelta

    class ExpiredCursor(Cursor):
        def __init__(self):
            super().__init__()
            self.rows[0] = (
                *self.rows[0],
                False,
                None,
                None,
                None,
                None,
                "realtime",
                datetime.now(UTC) + timedelta(minutes=5),
                None,
            )

        def fetchone(self):
            return None

    cursor = ExpiredCursor()
    connection = Connection(cursor)
    model_calls = []
    assert (
        process_received_batch(
            lambda: connection,
            runner=lambda prompt: model_calls.append(prompt),
            exchanges=("bitget",),
        )
        == 1
    )
    assert model_calls == []
    statements = "\n".join(statement for statement, _ in cursor.executed)
    assert "SELECT 1 FROM telegram_messages tm" in statements
    assert "intake_state='EXPIRED'" in statements
    assert "COALESCE(entry_rejection_reason" in statements
    assert "INSERT INTO canonical_signals" not in statements
    assert "INSERT INTO dispatches" not in statements
    assert connection.commits == 1
