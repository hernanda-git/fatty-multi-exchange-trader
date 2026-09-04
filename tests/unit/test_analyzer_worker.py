from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fatty_trader.analyzer.codex_runner import CodexRunResult
from fatty_trader.analyzer.postgres_worker import process_received_batch


class Cursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, object]] = []
        self.rows = [
            (
                uuid4(), 7, 42, "a" * 64,
                "#ETH LONG ENTRY: 100 TARGET: 110 STOPLOSS: 95", datetime.now(UTC)
            )
        ]

    def __enter__(self) -> Cursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: str, params: object = ()) -> None:
        self.executed.append((statement, params))

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
    assert statements.count("INSERT INTO dispatches") == 2
    assert "INSERT INTO canonical_signals" in statements
    assert "UPDATE telegram_messages" in statements
    assert connection.commits == 1
