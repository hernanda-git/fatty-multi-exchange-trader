"""Actual PostgreSQL eligibility proofs before model or market-price analysis."""

from datetime import UTC, datetime, timedelta

import pytest
from test_intake_coverage_postgres import CHANNEL, message, setup
from test_postgres_migration_and_margin_admission import postgres_schema as postgres_schema

from fatty_trader.analyzer.postgres_worker import process_received_batch
from fatty_trader.intake.persistence import PostgresRawMessageRepository
from fatty_trader.intake.telegram import TelegramIntake


@pytest.mark.parametrize("reason", ["aged", "rejected", "future", "missing"])
def test_ineligible_received_row_never_reaches_analysis(postgres_schema, reason):
    factory = setup(postgres_schema)
    TelegramIntake(PostgresRawMessageRepository(factory)).ingest(
        channel_id=CHANNEL, message=message(101, "$BTC long sl 90")
    )
    with factory() as c:
        if reason == "aged":
            c.execute("UPDATE telegram_messages SET entry_expires_at=now()-interval '1 second'")
        elif reason == "rejected":
            c.execute("UPDATE telegram_messages SET entry_rejection_reason='unsafe-source-edit'")
        elif reason == "future":
            c.execute(
                "UPDATE telegram_messages SET received_at=%s",
                (datetime.now(UTC) + timedelta(days=1),),
            )
        else:
            c.execute("UPDATE telegram_messages SET entry_expires_at=NULL")

    calls = []

    def forbidden(prompt):
        calls.append(prompt)
        raise AssertionError("eligibility must be checked before analysis")

    assert process_received_batch(factory, runner=forbidden) == 0
    assert calls == []
    with factory() as c:
        assert c.execute("SELECT count(*) FROM canonical_signals").fetchone() == (0,)
        assert c.execute("SELECT count(*) FROM dispatches").fetchone() == (0,)
