"""Re-enroll a stored management instruction that predates its action support.

A source message is parsed once at intake. If the parser did not yet understand
an instruction (for example "sl moved to 4.14" before SL_MOVE existed), the
message stays ANALYZED with no source_management_updates row. This tool re-runs
the deterministic parser over the stored text and enrolls the instruction for
the next source-management cycle.

Dry run by default. Only explicit stop moves replay by default; replaying old
close/TP instructions is a separate, deliberate decision.
"""

from __future__ import annotations

import argparse
import sys
from uuid import uuid4

import psycopg

from fatty_trader.analyzer.trade_management import parse_source_management

_INSERT = """
INSERT INTO source_management_updates
(id, source_message_id, revision, symbol, action, target_price, state, deadline_at)
VALUES (%s, %s, %s, %s, %s, %s, 'queued',
        clock_timestamp() + interval '30 minutes')
ON CONFLICT (source_message_id, revision, symbol, action) DO NOTHING
RETURNING id
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--message-id", type=int, required=True, help="Telegram message id")
    parser.add_argument(
        "--action",
        default="SL_MOVE",
        choices=("SL_MOVE", "SL_TO_ENTRY", "CLOSE", "TP1_BOOKED", "TP_BOOKED"),
    )
    parser.add_argument("--apply", action="store_true", help="write the update (default: dry run)")
    args = parser.parse_args(argv)

    with psycopg.connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, revision_hash, raw_text, intake_state, entry_rejection_reason
            FROM telegram_messages
            WHERE message_id = %s
            ORDER BY (intake_state = 'ANALYZED') DESC, received_at DESC
            LIMIT 1
            """,
            (args.message_id,),
        )
        row = cursor.fetchone()
        if row is None:
            print(f"no stored message with message_id={args.message_id}", file=sys.stderr)
            return 1
        message_uuid, revision, raw_text, intake_state, rejection = row
        management = parse_source_management(raw_text or "")
        print(
            f"message_id={args.message_id} intake_state={intake_state} "
            f"rejection={rejection or '-'} parsed={management}"
        )
        if management is None:
            print("parser recognizes no management instruction in the stored text", file=sys.stderr)
            return 1
        if management.action.value != args.action:
            print(
                f"parsed action {management.action.value} does not match --action {args.action}",
                file=sys.stderr,
            )
            return 1
        if management.symbol is None:
            print("symbol-less instructions are not replayed by this tool", file=sys.stderr)
            return 1
        print(
            f"would enroll {management.action.value} {management.symbol} "
            f"target_price={management.target_price}"
        )
        if not args.apply:
            print("dry run; re-run with --apply to enroll")
            return 0
        cursor.execute(
            _INSERT,
            (
                uuid4(),
                message_uuid,
                revision,
                management.symbol,
                management.action.value,
                management.target_price,
            ),
        )
        inserted = cursor.fetchone()
        connection.commit()
        print("enrolled" if inserted is not None else "already enrolled (conflict ignored)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
