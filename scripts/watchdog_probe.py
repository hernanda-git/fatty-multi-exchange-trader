#!/usr/bin/env python3
"""Read-only venue snapshot for the overnight watchdog.

Prints one JSON object: live positions, their native protection plans, pending
orders owned by the bot, and the venue clock skew. It performs GET requests
only and never mutates the account.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from fatty_trader.exchanges.bitget.client import BitgetRestClient


def _rows(value: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        for key in (*keys, "data", "entrustedList", "positionList"):
            rows = value.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


async def main() -> None:
    client = BitgetRestClient(
        api_key=os.environ["BITGET_API_KEY"],
        api_secret=os.environ["BITGET_API_SECRET"],
        passphrase=os.environ["BITGET_API_PASSPHRASE"],
        mode=os.environ.get("BITGET_MODE", "LIVE"),
    )
    result: dict[str, Any] = {
        "positions": [],
        "plans": {},
        "open_orders": [],
        "clock_skew_ms": None,
        "errors": [],
    }
    try:
        try:
            result["clock_skew_ms"] = await client.get_clock_skew_ms()
        except Exception as exc:  # noqa: BLE001 - evidence, not control flow
            result["errors"].append(f"clock-skew-unavailable:{type(exc).__name__}")

        positions = _rows(await client.get_all_positions())
        live = [p for p in positions if _positive(p.get("total"))]
        for position in live:
            result["positions"].append(
                {
                    "symbol": position.get("symbol"),
                    "side": position.get("holdSide"),
                    "total": position.get("total"),
                    "entry": position.get("openPriceAvg"),
                    "mark": position.get("markPrice"),
                    "margin": position.get("marginSize"),
                    "liquidation": position.get("liquidationPrice"),
                    "stop_loss": position.get("stopLoss"),
                    "take_profit": position.get("takeProfit"),
                }
            )
        for symbol in sorted({str(p.get("symbol")) for p in live if p.get("symbol")}):
            try:
                plans = await client.get_pending_plan_orders(symbol)
            except Exception as exc:  # noqa: BLE001
                result["errors"].append(f"plans-unavailable:{symbol}:{type(exc).__name__}")
                plans = []
            result["plans"][symbol] = [
                {
                    "plan_type": plan.get("planType"),
                    "trigger": plan.get("triggerPrice"),
                    "status": plan.get("planStatus"),
                    "client_oid": plan.get("clientOid"),
                }
                for plan in _rows(plans)
            ]

        orders = _rows(await client.get_pending_orders(), "entrustedList")
        for order in orders:
            result["open_orders"].append(
                {
                    "client_oid": order.get("clientOid"),
                    "symbol": order.get("symbol"),
                    "side": order.get("side"),
                    "price": order.get("price"),
                    "size": order.get("size"),
                    "status": order.get("status"),
                    "reduce_only": order.get("reduceOnly"),
                }
            )
    finally:
        await client.aclose()
    print(json.dumps(result, default=str, sort_keys=True))


def _positive(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


if __name__ == "__main__":
    asyncio.run(main())
