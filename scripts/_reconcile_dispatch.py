#!/usr/bin/env python3
"""Helper: reconcile one UNKNOWN dispatch to FILLED using the production repository.

Read-only provider evidence must already be confirmed by the operator. Uses the
repository's own transition() so the audit row, canary-reservation release and
notification are written in the same transaction as production code would.
"""
import asyncio
import os
import sys
import uuid

sys.path.insert(0, "/app/src")

DISPATCH_ID = sys.argv[1]


def main() -> None:
    import psycopg
    from fatty_trader.execution.bitget_dispatch_repository import (
        PostgresBitgetDispatchRepository,
    )

    def factory():
        return psycopg.connect(
            host=os.environ.get("PGHOST", "postgres"),
            port=os.environ.get("PGPORT", "5432"),
            dbname=os.environ.get("PGDATABASE", "fatty_trader"),
            user=os.environ.get("PGUSER", "fatty_app"),
            password=os.environ.get("PGPASSWORD", ""),
        )

    repo = PostgresBitgetDispatchRepository(factory)
    dispatch_id = uuid.UUID(DISPATCH_ID)
    repo.transition(
        dispatch_id,
        expected_state="UNKNOWN",
        target_state="FILLED",
        reason="reconciled-to-provider-fill-evidence",
    )
    print("TRANSITIONED", DISPATCH_ID, "UNKNOWN->FILLED")


main()
