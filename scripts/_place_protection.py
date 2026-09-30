#!/usr/bin/env python3
"""Helper: place venue-native SL/TP on an existing live position. Run inside dispatcher-bitget."""
import asyncio
import json
import os
import sys

sys.path.insert(0, "/app/src")
from fatty_trader.exchanges.bitget.client import BitgetRestClient

async def main():
    symbol = sys.argv[1]
    hold_side = sys.argv[2]
    quantity = sys.argv[3]
    stop_loss = sys.argv[4]
    take_profit = sys.argv[5]
    client = BitgetRestClient(
        api_key=os.environ["BITGET_API_KEY"],
        api_secret=os.environ["BITGET_API_SECRET"],
        passphrase=os.environ["BITGET_API_PASSPHRASE"],
        mode="LIVE",
    )
    try:
        res = await client.place_position_tpsl(
            symbol=symbol,
            hold_side=hold_side,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        print("TPSL_RESULT", json.dumps(res, default=str))
        pos = await client.get_single_position(symbol)
        print("POSITION_AFTER", json.dumps(pos, default=str))
    except Exception as exc:
        print("TPSL_ERROR", type(exc).__name__, str(exc))
        raise
    finally:
        await client.aclose()

asyncio.run(main())
