"""Explicit approved baseline command. Provider requests are GET-only."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime

import psycopg

from fatty_trader.exchanges.bitget.client import BitgetRestClient
from fatty_trader.storage.bitget_baseline import BaselineRefused, PostgresBitgetBaseline


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    action = result.add_mutually_exclusive_group(required=True)
    action.add_argument("--dry-run", action="store_true")
    action.add_argument("--apply", action="store_true")
    result.add_argument("--approval-reference", required=True)
    result.add_argument("--account-id", required=True, help="expected authenticated Bitget UID")
    result.add_argument("--environment", choices=("LIVE", "DEMO"), required=True)
    result.add_argument("--historical-before", required=True, help="fixed ISO-8601 UTC cutoff")
    result.add_argument(
        "--expected-digest", help="reviewed dry-run candidate_digest; required for apply"
    )
    return result


async def run(args: argparse.Namespace) -> dict[str, object]:
    mode = os.environ["BITGET_MODE"]
    if mode != args.environment:
        raise BaselineRefused("BITGET_MODE differs from approved environment")
    cutoff = datetime.fromisoformat(args.historical_before.replace("Z", "+00:00"))
    client = BitgetRestClient(
        os.environ["BITGET_API_KEY"],
        os.environ["BITGET_API_SECRET"],
        os.environ["BITGET_API_PASSPHRASE"],
        mode=mode,
        max_get_retries=0,
    )
    try:
        # libpq PG* environment is supported; DATABASE_URL also supports containers.
        database_url = os.environ.get("DATABASE_URL", "")
        baseline = PostgresBitgetBaseline(lambda: psycopg.connect(database_url))
        return await baseline.run(
            client,
            expected_account_id=args.account_id,
            environment=args.environment,
            historical_before=cutoff,
            approval_reference=args.approval_reference,
            apply=args.apply,
            expected_digest=args.expected_digest,
        )
    finally:
        await client.aclose()


def main() -> int:
    args = parser().parse_args()
    try:
        result = asyncio.run(run(args))
    except BaselineRefused as exc:
        print(json.dumps({"applied": False, "refused": str(exc)}))
        return 2
    except Exception:
        # Provider/database errors can include account data or DSN credentials.
        print(
            json.dumps(
                {"applied": False, "refused": "provider/database/configuration check failed"}
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
