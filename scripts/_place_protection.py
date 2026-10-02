#!/usr/bin/env python3
"""Explicitly approved protection repair, requiring a matching provider position."""

import asyncio
import json
import os
import sys

from _operational_safety import protection_args, verified_position

from fatty_trader.exchanges.bitget.client import BitgetRestClient


async def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--confirm" not in argv:
        print("REFUSED: explicit --confirm required")
        return 2
    args = protection_args(argv, fallback=False)
    client = BitgetRestClient(
        api_key=os.environ["BITGET_API_KEY"],
        api_secret=os.environ["BITGET_API_SECRET"],
        passphrase=os.environ["BITGET_API_PASSPHRASE"],
        mode="LIVE",
    )
    try:
        verified_position(await client.get_single_position(args.symbol), args)
        result = await client.place_position_tpsl(
            symbol=args.symbol,
            hold_side=args.hold_side,
            quantity=args.quantity,
            stop_loss=args.stop_loss,
            take_profit=args.take_profit,
        )
        print("TPSL_RESULT", json.dumps(result, default=str), "approval", args.approval_reference)
        print(
            "POSITION_AFTER", json.dumps(await client.get_single_position(args.symbol), default=str)
        )
        return 0
    finally:
        await client.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
