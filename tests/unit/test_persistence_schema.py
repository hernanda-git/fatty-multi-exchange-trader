from fatty_trader.storage.schema import CLAIM_DISPATCH_SQL, INITIAL_SCHEMA_SQL, apply_initial_schema


def test_schema_enforces_one_dispatch_per_signal_revision_and_exchange() -> None:
    assert "UNIQUE (source_type, source_id, revision, exchange)" in INITIAL_SCHEMA_SQL
    assert "FOR UPDATE SKIP LOCKED" in CLAIM_DISPATCH_SQL
    assert "lease_until <= now()" in CLAIM_DISPATCH_SQL


def test_schema_records_submission_idempotency_and_protection_state() -> None:
    assert "client_order_id" in INITIAL_SCHEMA_SQL
    assert "UNIQUE (exchange, client_order_id)" in INITIAL_SCHEMA_SQL
    assert "protection_state" in INITIAL_SCHEMA_SQL


def test_schema_is_applied_as_one_atomic_script() -> None:
    calls: list[str] = []

    class Cursor:
        def execute(self, statement: str) -> None:
            calls.append(statement)

    apply_initial_schema(Cursor())

    assert calls == [INITIAL_SCHEMA_SQL]
