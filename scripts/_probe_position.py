#!/usr/bin/env python3
"""Helper: read Bitget position for a symbol. Run inside dispatcher-bitget container."""
import asyncio
import json
import os
import sys

sys.path.insert(0, "/app/src")
from fatty_trader.exchanges.bitget.client import BitgetRestClient

async def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    client = BitgetRestClient(
        api_key=os.environ["BITGET_API_KEY"],
        api_secret=os.environ["BITGET_API_SECRET"],
        passphrase=os.environ["BITGET_API_PASSPHRASE"],
        mode="LIVE",
    )
    try:
        pos = await client.get_single_position(symbol)
        print(json.dumps(pos))
    finally:
        await client.aclose()

asyncio.run(main())
