#!/usr/bin/env python
"""Sanitized, read-only Bitget production readiness probe."""

from __future__ import annotations

import argparse
import asyncio
import json
import os

from fatty_trader.exchanges.bitget.client import BitgetRestClient
from fatty_trader.exchanges.bitget.probe import run_read_only_probe


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit sanitized JSON")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    required = ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE")
    if any(not os.environ.get(key, "").strip() for key in required):
        print("BITGET_PROBE BLOCKED reason=missing-credentials")
        return 2
    client = BitgetRestClient(
        api_key=os.environ["BITGET_API_KEY"],
        api_secret=os.environ["BITGET_API_SECRET"],
        passphrase=os.environ["BITGET_API_PASSPHRASE"],
        mode=os.environ.get("BITGET_MODE", "DEMO").upper(),
    )
    try:
        result = await run_read_only_probe(client)
    finally:
        await client.aclose()
    if args.json:
        print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    else:
        for name, check in result["checks"].items():
            fields = " ".join(f"{key}={value}" for key, value in check.items())
            print(f"BITGET_PROBE endpoint={name} {fields}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
