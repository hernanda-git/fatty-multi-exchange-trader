#!/usr/bin/env python3
"""Read-only durable ledger readiness. Never repairs rows or releases a latch."""

import json
import os

import psycopg

from fatty_trader.execution.bitget_dispatch_repository import PostgresBitgetDispatchRepository
from fatty_trader.execution.bitget_runtime_readiness import ledger_readiness_issues
from fatty_trader.production_policy import validate_production_live_policy


def main() -> int:
    validate_production_live_policy(os.environ)
    connections = []

    def connect():
        connection = psycopg.connect(options="-c default_transaction_read_only=on")
        connections.append(connection)
        return connection

    try:
        issues = ledger_readiness_issues(PostgresBitgetDispatchRepository(connect), "LIVE")
        print(json.dumps({"ledger_readiness": "blocked" if issues else "ready", "issues": issues}))
        return 2 if issues else 0
    finally:
        for connection in connections:
            connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
