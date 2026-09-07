#!/usr/bin/env python3
"""Read-only sanitized Bitget DEMO account telemetry."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from fatty_trader.exchanges.bitget.client import BitgetRestClient


def _first(mapping: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _count_rows(value: Any) -> int | None:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        for key in ("entrustedList", "fillList", "data", "list"):
            nested = value.get(key)
            if isinstance(nested, list):
                return len(nested)
        return 0
    return None


def _account_fields(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        nested = value.get("data")
        source = nested if isinstance(nested, dict) else value
        return {
            "available": _first(source, "available", "availableBalance", "available_balance"),
            "equity": _first(source, "usdtEquity", "equity", "totalBalance", "total_balance"),
            "margin_mode": _first(source, "marginMode", "margin_mode"),
            "position_mode": _first(source, "posMode", "positionMode", "position_mode"),
            "margin_coin": _first(source, "marginCoin", "margin_coin"),
        }
    return {
        "available": None,
        "equity": None,
        "margin_mode": None,
        "position_mode": None,
        "margin_coin": None,
    }


async def _run() -> int:
    required = ("BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE")
    if any(not os.environ.get(key, "").strip() for key in required):
        print(json.dumps({"status": "BLOCKED", "reason": "missing-credentials"}))
        return 2
    mode = os.environ.get("BITGET_MODE", "").upper()
    if mode != "DEMO":
        print(json.dumps({"status": "BLOCKED", "reason": "non-DEMO-mode"}))
        return 2
    client = BitgetRestClient(
        os.environ["BITGET_API_KEY"],
        os.environ["BITGET_API_SECRET"],
        os.environ["BITGET_API_PASSPHRASE"],
        mode,
    )
    try:
        account, positions, orders, fills = await asyncio.gather(
            client.get_account(),
            client.get_all_positions(),
            client.get_pending_orders(),
            client.get_fills(),
        )
    finally:
        await client.aclose()
    result = {
        "status": "PASS",
        "mode": mode,
        "account": _account_fields(account),
        "positions": _count_rows(positions),
        "open_orders": _count_rows(orders),
        "fills": _count_rows(fills),
    }
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
