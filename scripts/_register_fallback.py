#!/usr/bin/env python3
"""Explicitly approved protection repair, requiring a matching provider position."""

import asyncio
import os
import sys

from _operational_safety import positive, protection_args, verified_position

from fatty_trader.exchanges.bitget.client import BitgetRestClient


async def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--confirm" not in argv:
        print("REFUSED: explicit --confirm required")
        return 2
    args = protection_args(argv, fallback=True)
    client = BitgetRestClient(
        api_key=os.environ["BITGET_API_KEY"],
        api_secret=os.environ["BITGET_API_SECRET"],
        passphrase=os.environ["BITGET_API_PASSPHRASE"],
        mode="LIVE",
    )
    try:
        row = verified_position(await client.get_single_position(args.symbol), args)
        from fatty_trader.execution.bitget_fallback_protection import load_active, register_fallback

        key = register_fallback(
            exchange="bitget",
            symbol=args.symbol,
            direction=args.hold_side.upper(),
            entry_price=positive(row.get("openPriceAvg")),
            stop_loss=positive(args.stop_loss),
            take_profits=[positive(args.take_profit)],
            quantity=positive(args.quantity),
            position_key=args.position_key,
        )
        print("REGISTERED", key, "approval", args.approval_reference)
        for active in load_active():
            print(
                "ACTIVE", active["symbol"], active["direction"], active["state"], active["quantity"]
            )
        return 0
    finally:
        await client.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
