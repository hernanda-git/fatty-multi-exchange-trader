#!/usr/bin/env python3
"""Helper: read Bitget account state. Run inside dispatcher-bitget container."""
import asyncio
import os
import sys

sys.path.insert(0, "/app/src")
from fatty_trader.exchanges.bitget.client import BitgetRestClient

async def main():
    client = BitgetRestClient(
        api_key=os.environ["BITGET_API_KEY"],
        api_secret=os.environ["BITGET_API_SECRET"],
        passphrase=os.environ["BITGET_API_PASSPHRASE"],
        mode="LIVE",
    )
    try:
        acct = await client.get_account("BTCUSDT")
        print(f"{acct.get('accountEquity', 'N/A')}|{acct.get('available', 'N/A')}|{acct.get('unrealizedPL', 'N/A')}")
    finally:
        await client.aclose()

asyncio.run(main())
