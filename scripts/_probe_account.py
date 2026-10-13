#!/usr/bin/env python3
"""Read-only account telemetry DTO; run inside dispatcher-bitget."""

import asyncio
import json
import os

from fatty_trader.exchanges.bitget.client import BitgetRestClient
from fatty_trader.operator.health import normalize_account_snapshot


async def main(symbol="BTCUSDT"):
    client = BitgetRestClient(
        api_key=os.environ["BITGET_API_KEY"],
        api_secret=os.environ["BITGET_API_SECRET"],
        passphrase=os.environ["BITGET_API_PASSPHRASE"],
        mode=os.environ.get("BITGET_MODE", "LIVE"),
    )
    try:
        account = await client.get_account(symbol=symbol)
        if not isinstance(account, dict):
            raise ValueError("invalid account response")
        print(json.dumps(normalize_account_snapshot(account, environment=client.environment)))
    except Exception as exc:
        print(json.dumps({"read_status": "UNKNOWN", "error": type(exc).__name__}))
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
