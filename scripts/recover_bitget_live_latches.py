"""GET-only LIVE flat-baseline latch recovery. Stop all mutation consumers first.

Default is dry-run proof. Both proof and apply require explicit approval; apply
also requires the reviewed recovery digest. No risk/environment flags are changed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from uuid import UUID

from fatty_trader.exchanges.bitget.client import BitgetRestClient
from fatty_trader.exchanges.bitget.websocket_v2 import BitgetV2WebSocket
from fatty_trader.storage.bitget_baseline import BaselineRefused
from fatty_trader.storage.bitget_live_latch_recovery import (
    PostgresBitgetLiveLatchRecovery,
    require_production_settings,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    action = result.add_mutually_exclusive_group()
    action.add_argument("--dry-run", action="store_true", help="proof only (default)")
    action.add_argument(
        "--apply", action="store_true", help="atomically release only reviewed old latches"
    )
    result.add_argument(
        "--confirm", action="store_true", help="explicit approved operator confirmation"
    )
    result.add_argument("--approval-reference", required=True)
    result.add_argument("--account-id", required=True, help="expected authenticated Bitget UID")
    result.add_argument(
        "--baseline-id", required=True, type=UUID, help="applied audited baseline receipt UUID"
    )
    result.add_argument(
        "--expected-digest", help="reviewed dry-run recovery_digest, required for apply"
    )
    return result


async def run(args: argparse.Namespace) -> dict[str, object]:
    if not args.confirm or not args.approval_reference.strip() or not args.account_id.strip():
        raise BaselineRefused("explicit --confirm, approval reference and account ID are required")
    require_production_settings(os.environ)
    import psycopg

    client = BitgetRestClient(
        os.environ["BITGET_API_KEY"],
        os.environ["BITGET_API_SECRET"],
        os.environ["BITGET_API_PASSPHRASE"],
        mode="LIVE",
        max_get_retries=0,
    )
    socket = BitgetV2WebSocket(
        api_key=os.environ["BITGET_API_KEY"],
        api_secret=os.environ["BITGET_API_SECRET"],
        passphrase=os.environ["BITGET_API_PASSPHRASE"],
        symbols=("BTCUSDT",),
        stale_after=5.0,
        heartbeat_interval=1.0,
        private_silent_after=5.0,
    )
    try:
        recovery = PostgresBitgetLiveLatchRecovery(
            lambda: psycopg.connect(os.environ.get("DATABASE_URL", ""))
        )
        return await recovery.run(
            client,
            socket,
            environ=os.environ,
            confirmed=args.confirm,
            account_id=args.account_id,
            baseline_id=args.baseline_id,
            approval_reference=args.approval_reference,
            apply=args.apply,
            expected_digest=args.expected_digest,
        )
    finally:
        await client.aclose()


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        result = asyncio.run(run(args))
    except BaselineRefused as exc:
        print(json.dumps({"applied": False, "refused": str(exc)}))
        return 2
    except Exception:
        # Never expose provider account payloads, credentials or database DSNs.
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
