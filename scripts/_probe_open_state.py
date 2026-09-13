#!/usr/bin/env python3
"""Read current Bitget positions and pending orders without provider mutation."""

from __future__ import annotations

import asyncio
import json
import os

from fatty_trader.exchanges.bitget.client import BitgetRestClient


async def main() -> None:
    client = BitgetRestClient(
        api_key=os.environ["BITGET_API_KEY"],
        api_secret=os.environ["BITGET_API_SECRET"],
        passphrase=os.environ["BITGET_API_PASSPHRASE"],
        mode=os.environ.get("BITGET_MODE", "LIVE"),
    )
    try:
        result: dict[str, object] = {}
        for name, call in (
            ("positions", client.get_all_positions()),
            ("open_orders", client.get_pending_orders()),
        ):
            try:
                result[name] = await call
            except Exception as exc:
                result[name] = {
                    "error_type": type(exc).__name__,
                    "provider_code": getattr(exc, "code", ""),
                    "message": str(exc),
                }
        print(json.dumps(result, default=str, sort_keys=True))
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
