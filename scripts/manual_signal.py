#!/usr/bin/env python3
"""Owner manual lane: a manual instruction becomes a normal dispatch.

Why this exists: a position opened directly on the venue is a foreign position —
the bot cannot own, protect or account for it (that caused the 2026-11-19 outage).
This script writes the same rows the analyzer writes, so the dispatcher claims the
instruction and applies preflight, $2/20x sizing, native SL/TP, reconciliation and
scorecard attribution exactly as it does for a sourced signal.

Usage (inside the deployed image):

    docker compose exec -T dispatcher-bitget /app/.venv/bin/python \
        /app/scripts/manual_signal.py --symbol SOLUSDT --direction LONG \
        --entry 132.5 --stop 128.1 --target 142

Without ``--confirm`` it prints the plan and changes nothing.

Close an owned position through the same gate with ``--action close``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Sequence

MANUAL_CHANNEL_ID = -999000001
MANUAL_CHANNEL_LABEL = "manual-instructions"
_SYMBOL = re.compile(r"^[A-Z0-9]{2,15}USDT$")
_MANAGEMENT_DEADLINE_MINUTES = 30


@dataclass(frozen=True)
class Instruction:
    action: str
    symbol: str
    direction: str
    entry: Decimal | None
    stop: Decimal | None
    targets: tuple[Decimal, ...]
    order_type: str = "MARKET"
    expires_minutes: int = 5

    @property
    def text(self) -> str:
        base = f"#MANUAL {self.symbol} {self.direction}"
        if self.action == "close":
            return base + " CLOSE"
        parts = [f"{base} TRADE ENTRY: {self.entry}"]
        parts += [f"TARGET: {target}" for target in self.targets]
        parts.append(f"STOPLOSS: {self.stop}")
        return " ".join(parts)

    @property
    def revision(self) -> str:
        digest = hashlib.sha256(self.text.encode("utf-8"))
        digest.update(f"|{MANUAL_CHANNEL_ID}|{self.action}".encode("utf-8"))
        return digest.hexdigest()

    def plan(self) -> dict[str, Any]:
        risk = None
        rewards: list[Decimal] = []
        if self.entry is not None and self.stop is not None:
            risk = abs(self.entry - self.stop)
            rewards = [
                _reward(self.entry, target, self.direction) for target in self.targets
            ]
        return {
            "action": self.action,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry": str(self.entry) if self.entry is not None else None,
            "stop": str(self.stop) if self.stop is not None else None,
            "targets": [str(target) for target in self.targets],
            "risk_per_unit": str(risk) if risk else None,
            "reward_risk": [str(reward / risk) if risk else None for reward in rewards],
            "expires_minutes": self.expires_minutes,
            "text": self.text,
        }


def _reward(entry: Decimal, target: Decimal, direction: str) -> Decimal:
    return entry - target if direction == "SHORT" else target - entry


def parse_instruction(args: argparse.Namespace) -> Instruction:
    """Validate before anything is written; every error is a refusal, not a guess."""
    symbol = (args.symbol or "").strip().upper()
    if not _SYMBOL.match(symbol):
        raise ValueError("symbol must look like SOLUSDT (quote-suffixed)")
    action = args.action
    if action == "close":
        return Instruction(action="close", symbol=symbol, direction="LONG",
                           entry=None, stop=None, targets=())
    direction = (args.direction or "").strip().upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")
    entry = _positive(args.entry, "entry")
    stop = _positive(args.stop, "stop")
    targets = tuple(_positive(value, "target") for value in args.target or ())
    if not targets:
        raise ValueError("at least one --target is required")
    if direction == "LONG":
        if not stop < entry:
            raise ValueError("LONG needs stop < entry")
        if any(target <= entry for target in targets):
            raise ValueError("LONG targets must be above entry")
    else:
        if not stop > entry:
            raise ValueError("SHORT needs stop > entry")
        if any(target >= entry for target in targets):
            raise ValueError("SHORT targets must be below entry")
    order_type = (args.order_type or "MARKET").strip().upper()
    if order_type not in {"MARKET", "LIMIT"}:
        raise ValueError("order-type must be MARKET or LIMIT")
    expires = int(args.expires_minutes)
    if not 1 <= expires <= 30:
        raise ValueError("expires-minutes must be within 1..30")
    return Instruction(
        action="enter",
        symbol=symbol,
        direction=direction,
        entry=entry,
        stop=stop,
        targets=targets,
        order_type=order_type,
        expires_minutes=expires,
    )


def _positive(raw: Any, label: str) -> Decimal:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{label} must be positive and finite")
    return value


def message_values(instruction: Instruction, *, now: datetime) -> dict[str, Any]:
    return {
        "channel_id": MANUAL_CHANNEL_ID,
        "message_id": int(now.timestamp() * 1000),
        "revision_hash": instruction.revision,
        "received_at": now,
        "raw_text": instruction.text,
        "entry_expires_at": now + timedelta(minutes=instruction.expires_minutes),
    }


def write_instruction(cursor: Any, instruction: Instruction, *, now: datetime) -> dict[str, Any]:
    """Insert the durable rows the pipeline already understands. No venue calls."""
    from uuid import uuid4

    message = message_values(instruction, now=now)
    cursor.execute(
        """
        INSERT INTO telegram_messages (
            id, channel_id, message_id, revision_hash, received_at, raw_text,
            has_media, intake_state, ingestion_origin, entry_expires_at
        ) VALUES (gen_random_uuid(), %(channel_id)s, %(message_id)s, %(revision_hash)s,
                  %(received_at)s, %(raw_text)s, FALSE, 'ANALYZED', 'realtime',
                  %(entry_expires_at)s)
        RETURNING id
        """,
        message,
    )
    message_uuid = cursor.fetchone()[0]
    if instruction.action == "close":
        cursor.execute(
            """
            INSERT INTO source_management_updates
                (id, source_message_id, revision, symbol, action, target_price,
                 state, deadline_at)
            VALUES (%s, %s, %s, %s, 'CLOSE', NULL, 'queued',
                    clock_timestamp() + (%s * interval '1 minute'))
            """,
            (
                uuid4(),
                message_uuid,
                instruction.revision,
                instruction.symbol,
                _MANAGEMENT_DEADLINE_MINUTES,
            ),
        )
        return {"message_id": str(message_uuid), "management": "CLOSE"}
    cursor.execute(
        """
        INSERT INTO canonical_signals
            (id, message_id, revision, pair_token, direction, entry_price,
             stop_loss, take_profits)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
        """,
        (
            uuid4(),
            message_uuid,
            instruction.revision,
            instruction.symbol.removesuffix("USDT"),
            instruction.direction,
            instruction.entry,
            instruction.stop,
            json.dumps([str(target) for target in instruction.targets]),
        ),
    )
    cursor.execute(
        """
        INSERT INTO dispatches
            (id, source_type, source_id, revision, exchange, state)
        SELECT gen_random_uuid(), 'canonical_signal', s.id, s.revision, 'bitget', 'QUEUED'
        FROM canonical_signals s
        WHERE s.message_id = %s AND s.revision = %s
        RETURNING id
        """,
        (message_uuid, instruction.revision),
    )
    dispatch_id = cursor.fetchone()[0]
    return {"message_id": str(message_uuid), "dispatch_id": str(dispatch_id)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=("enter", "close"), default="enter")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--direction")
    parser.add_argument("--entry")
    parser.add_argument("--stop")
    parser.add_argument("--target", action="append")
    parser.add_argument("--order-type", default="MARKET")
    parser.add_argument("--expires-minutes", default=5)
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument(
        "--verify-sql",
        action="store_true",
        help="execute the inserts and roll back: proves the SQL without leaving rows",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        instruction = parse_instruction(args)
    except ValueError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    now = datetime.now(UTC)
    plan = instruction.plan()
    executes = args.confirm or args.verify_sql
    if args.json or not executes:
        print(json.dumps(plan, indent=2, sort_keys=True))
    if not executes:
        print(
            "dry-run: nothing written. Re-run with --confirm to submit this instruction.",
            file=sys.stderr,
        )
        return 0
    import psycopg

    with psycopg.connect() as connection:
        with connection.cursor() as cursor:
            result = write_instruction(cursor, instruction, now=now)
        if args.verify_sql:
            connection.rollback()
            print(json.dumps({"sql_ok": result}, sort_keys=True))
            return 0
        connection.commit()
    print(json.dumps({"submitted": result, **plan}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
