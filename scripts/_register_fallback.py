#!/usr/bin/env python3
"""Helper: register a live naked position with the bot-managed fallback monitor."""
import sys
from decimal import Decimal

sys.path.insert(0, "/app/src")
from fatty_trader.execution.bitget_fallback_protection import register_fallback, load_active

key = register_fallback(
    exchange="bitget",
    symbol="PENGUUSDT",
    direction="SHORT",
    entry_price=Decimal("0.009834"),
    stop_loss=Decimal("0.010125"),
    take_profits=[Decimal("0.0085")],
    quantity=Decimal("5082"),
    position_key="live-bitget-PENGUUSDT-e8dcda3560325754",
)
print("REGISTERED", key)
for row in load_active():
    print("ACTIVE", row["symbol"], row["direction"], row["state"], row["quantity"])
