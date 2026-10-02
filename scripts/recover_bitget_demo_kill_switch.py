"""Release the Bitget DEMO kill switch after clean GET-only reconciliation."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from fatty_trader.exchanges.bitget.client import BitgetRestClient
from fatty_trader.execution.bitget_monitor import BitgetMonitor
from fatty_trader.execution.bitget_protection_watchdog import PROTECTION_STREAM_SCOPE
from fatty_trader.execution.bitget_recovery import release_after_clean_monitor
from fatty_trader.storage.reconciliation import PostgresReconciliationRepository

VENUE_SCOPE = "bitget"


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument(
        "--scope",
        default=VENUE_SCOPE,
        choices=[VENUE_SCOPE, PROTECTION_STREAM_SCOPE],
        help=(
            f"kill-switch scope to release; {PROTECTION_STREAM_SCOPE} latches when the "
            f"protection stream dies, {VENUE_SCOPE} when venue-wide reconciliation fails"
        ),
    )
    return parser.parse_args(argv)


async def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--confirm" not in argv:
        print("REFUSED: explicit --confirm required")
        return 2
    args = parse_args(argv)
    if not args.approval_reference.strip():
        raise ValueError("approval reference is required")
    if os.environ.get("BITGET_MODE", "").upper() != "DEMO":
        raise RuntimeError("kill-switch recovery is DEMO-only")
    import psycopg

    client = BitgetRestClient(
        os.environ["BITGET_API_KEY"],
        os.environ["BITGET_API_SECRET"],
        os.environ["BITGET_API_PASSPHRASE"],
        mode="DEMO",
    )
    try:
        repository = PostgresReconciliationRepository(psycopg.connect)
        report = await BitgetMonitor(
            client,
            repository,
            max_clock_skew_ms=int(os.environ.get("BITGET_MAX_CLOCK_SKEW_MS", "5000")),
        ).run_once()
        recovery = release_after_clean_monitor(
            report,
            repository,
            scope=args.scope,
            approval_reference=args.approval_reference,
        )
        print(
            f"released={str(recovery.released).lower()} reason={recovery.reason} "
            f"monitor_reasons={','.join(report.reasons) or 'none'}"
        )
        return 0 if recovery.released else 2
    finally:
        await client.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
