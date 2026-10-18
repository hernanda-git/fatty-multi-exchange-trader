from __future__ import annotations

import json
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from fatty_trader.execution.bitget_dispatch_repository import PostgresBitgetDispatchRepository


class Cursor:
    def __init__(self, rows: list[dict[str, object] | None]) -> None:
        self.rows = rows
        self.statements: list[tuple[str, tuple[object, ...] | dict[str, object]]] = []

    def execute(self, statement: str, params: tuple[object, ...] | dict[str, object] = ()) -> None:
        self.statements.append((statement, params))

    def fetchone(self) -> dict[str, object] | None:
        return (
            None
            if self.statements[-1][0].startswith("SELECT d.id FROM dispatches d")
            else self.rows.pop(0)
        )


class Connection:
    def __init__(self, cursor: Cursor) -> None:
        self.cursor_value = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> Cursor:
        return self.cursor_value

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _row(dispatch_id: UUID, *, claimed_by: str | None = None) -> dict[str, object]:
    return {
        "id": dispatch_id,
        "state": "QUEUED",
        "claimed_by": claimed_by,
        "lease_until": None,
        "attempts": 1,
        "pair_token": "BTCUSDT",
        "direction": "LONG",
        "entry_price": Decimal("64000"),
        "stop_loss": Decimal("63000"),
        "take_profits": ["65000"],
    }


def test_claim_uses_skip_locked_and_two_workers_receive_distinct_queued_rows() -> None:
    first_id, second_id = uuid4(), uuid4()
    first_cursor = Cursor([_row(first_id, claimed_by="worker-a")])
    second_cursor = Cursor([_row(second_id, claimed_by="worker-b")])
    first_connection = Connection(first_cursor)
    second_connection = Connection(second_cursor)

    first = PostgresBitgetDispatchRepository(lambda: first_connection).claim("worker-a", 30)
    second = PostgresBitgetDispatchRepository(lambda: second_connection).claim("worker-b", 30)

    assert first is not None and first.id == first_id
    assert second is not None and second.id == second_id
    # The lock must name the dispatch table only: an unqualified FOR UPDATE also locks
    # the joined signal and message rows, which starved the claim in production.
    assert "FOR UPDATE OF d SKIP LOCKED" in first_cursor.statements[-1][0]
    assert "exchange = 'bitget'" in first_cursor.statements[-1][0]
    assert "state = 'QUEUED'" in first_cursor.statements[-1][0]
    assert first_connection.commits == second_connection.commits == 1


def test_claim_recovers_only_expired_lease_and_never_steals_active_lease() -> None:
    recovered_id = uuid4()
    active_cursor = Cursor([None])
    recovered_cursor = Cursor([_row(recovered_id, claimed_by="worker-b")])
    active_connection = Connection(active_cursor)
    recovered_connection = Connection(recovered_cursor)

    assert PostgresBitgetDispatchRepository(lambda: active_connection).claim("worker-b", 30) is None
    recovered = PostgresBitgetDispatchRepository(lambda: recovered_connection).claim("worker-b", 30)

    assert recovered is not None and recovered.id == recovered_id
    sql = recovered_cursor.statements[-1][0]
    assert "claimed_by IS NULL OR lease_until <= now()" in sql
    assert "attempts = attempts + 1" in sql
    assert active_connection.commits == recovered_connection.commits == 1


def test_canary_reservation_uses_uppercase_entry_roles_and_a_transactional_exchange_lock() -> None:
    dispatch_id = uuid4()
    cursor = Cursor([{"dispatch_id": dispatch_id}])
    connection = Connection(cursor)

    reserved = PostgresBitgetDispatchRepository(lambda: connection).reserve_canary_entry(
        dispatch_id, "bitget", 1
    )

    assert reserved is True
    statement, params = cursor.statements[-1]
    assert "pg_advisory_xact_lock" in statement
    assert "role = 'ENTRY'" in statement
    assert "canary_entry_reservations" in statement
    assert params == ("bitget", dispatch_id, "bitget", "bitget", "bitget", 1)
    assert connection.commits == 1


def test_transition_evidence_outbox_and_json_log_share_committed_event(capsys) -> None:
    dispatch_id = uuid4()
    cursor = Cursor([{"id": dispatch_id}])
    connection = Connection(cursor)
    evidence = {
        "stage": "contract_catalog",
        "attempt": 2,
        "worker_id": "worker-a",
        "source_channel_id": -100123,
        "source_message_id": 16310,
        "audit_evidence": {
            "code": "catalog_rows_invalid",
            "row_count": 834,
            "Authorization": "Bearer secret",
            "invalid_rows": [{"index": 22, "row_type": "str"}],
        },
    }

    PostgresBitgetDispatchRepository(lambda: connection).transition(
        dispatch_id,
        expected_state="PREFLIGHT",
        target_state="REJECTED",
        reason="catalog_rows_invalid",
        evidence=evidence,
    )

    transition_sql, transition_params = next(
        (sql, params)
        for sql, params in cursor.statements
        if "INSERT INTO dispatch_transitions" in sql
    )
    persisted = json.loads(transition_params[-1])
    assert "evidence" in transition_sql and "%s::jsonb" in transition_sql
    assert persisted["dispatch_id"] == str(dispatch_id)
    assert persisted["attempt"] == 2
    assert persisted["worker_id"] == "worker-a"
    assert persisted["source_message_id"] == 16310
    assert persisted["provider_effect"] == "not_submitted"
    assert persisted["audit_evidence"]["row_count"] == 834
    assert "Authorization" not in persisted["audit_evidence"]
    payload = json.loads(cursor.statements[-1][1][-1])
    assert payload["evidence"] == persisted
    assert connection.commits == 1 and connection.rollbacks == 0
    logged = json.loads(capsys.readouterr().out)
    assert logged["event"] == "bitget-dispatch-transition"
    assert logged["stage"] == "contract_catalog"
    assert logged["audit_evidence"] == persisted["audit_evidence"]
    assert "secret" not in json.dumps(logged)


@pytest.mark.parametrize("failure", ["audit-insert", "outbox-insert", "commit"])
def test_failed_audit_transaction_rolls_back_and_never_logs_success(failure, capsys) -> None:
    dispatch_id = uuid4()

    class BrokenCursor(Cursor):
        def execute(self, statement, params=()):
            super().execute(statement, params)
            if (failure == "audit-insert" and "INSERT INTO dispatch_transitions" in statement) or (
                failure == "outbox-insert" and "INSERT INTO notifications_outbox" in statement
            ):
                raise RuntimeError("audit write failed")

    class BrokenConnection(Connection):
        def commit(self):
            if failure == "commit":
                raise RuntimeError("commit failed")
            super().commit()

    connection = BrokenConnection(BrokenCursor([{"id": dispatch_id}]))
    with pytest.raises(RuntimeError):
        PostgresBitgetDispatchRepository(lambda: connection).transition(
            dispatch_id,
            expected_state="VALIDATED",
            target_state="SUBMITTING",
            evidence={"stage": "provider_submission"},
        )
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert capsys.readouterr().out == ""


def test_audit_diagnostics_are_bounded_and_do_not_stringify_private_objects() -> None:
    from fatty_trader.execution.bitget_dispatch_repository import sanitize_audit_evidence

    class Private:
        def __str__(self):
            raise AssertionError("must not stringify a private provider object")

    evidence = sanitize_audit_evidence(
        {
            "stage": "contract_catalog",
            "Authorization": "Bearer secret",
            "headers": {"api-key": "secret"},
            "payload": {"account": "secret"},
            "prompt": "private",
            "requested_symbol": "MAGICUSDT",
            "offending_symbols": ["龙虾USDT"] * 1000,
            "invalid_rows": [{"index": i, "row_type": Private()} for i in range(1000)],
            "selected_metadata": {"symbol": "Bearer secret", "maxLever": "x" * 10000},
        }
    )
    assert evidence["requested_symbol"] == "MAGICUSDT"
    assert len(evidence["offending_symbols"]) == 12
    assert len(evidence["invalid_rows"]) == 12
    assert evidence["invalid_rows"][0]["row_type"] is None
    assert evidence["selected_metadata"]["symbol"] == "[redacted]"
    encoded = json.dumps(evidence)
    assert len(encoded) < 24000
    assert "secret" not in encoded and "private" not in encoded
    assert sanitize_audit_evidence(Private()) == {}


def test_transition_reason_is_redacted_before_persistence_notifications_and_logs(capsys) -> None:
    dispatch_id = uuid4()
    cursor = Cursor([{"id": dispatch_id}])
    connection = Connection(cursor)
    PostgresBitgetDispatchRepository(lambda: connection).transition(
        dispatch_id,
        expected_state="SUBMITTING",
        target_state="UNKNOWN",
        reason="Authorization: Bearer secret",
    )
    assert cursor.statements[0][1][1] == "redacted-transition-reason"
    logged = json.loads(capsys.readouterr().out)
    assert logged["provider_effect"] == "submission_may_have_occurred"
    assert "secret" not in json.dumps(cursor.statements, default=str)
    assert "secret" not in json.dumps(logged)
