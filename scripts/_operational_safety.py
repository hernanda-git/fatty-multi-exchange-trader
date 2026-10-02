"""Shared fail-closed argument and provider position checks for repair helpers."""

import argparse
import re
from decimal import Decimal, InvalidOperation


def protection_args(argv, *, fallback=False):
    parser = argparse.ArgumentParser(description="Repair existing protection; never open a trade")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--approval-reference", required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--hold-side", choices=("long", "short"), required=True)
    parser.add_argument("--quantity", required=True)
    parser.add_argument("--stop-loss", required=True)
    parser.add_argument("--take-profit", required=True)
    if fallback:
        parser.add_argument("--position-key", required=True)
    args = parser.parse_args(argv)
    if not args.approval_reference.strip():
        raise ValueError("approval reference is required")
    if fallback and not args.position_key.strip():
        raise ValueError("position key is required")
    if not re.fullmatch(r"[A-Z0-9]{2,20}USDT", args.symbol):
        raise ValueError("symbol must be an exact uppercase USDT provider identifier")
    for value in (args.quantity, args.stop_loss, args.take_profit):
        positive(value)
    return args


def positive(value):
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("invalid provider position numeric evidence") from exc
    if not number.is_finite() or number <= 0:
        raise ValueError("invalid provider position numeric evidence")
    return number


def verified_position(payload, args):
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise ValueError("provider position evidence must identify exactly one position")
    row = payload[0]
    if row.get("symbol") != args.symbol or row.get("holdSide") != args.hold_side:
        raise ValueError("provider position identity mismatch")
    if positive(row.get("total")) != positive(args.quantity):
        raise ValueError("provider position quantity mismatch")
    return row
