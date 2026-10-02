#!/usr/bin/env python3
"""Retired UNKNOWN-to-FILLED bypass; local state is not provider fill evidence.

Use agentic_ops_snapshot.py --symbol SYMBOL for read-only durable/provider evidence,
then the normal reconciliation path. A standalone transition cannot atomically
verify intent/dispatch linkage, provider fill identity and quantity. Even explicit
confirmation cannot safely substitute for those prerequisites.
"""


def main(argv=None):
    print(
        "REFUSED: blind dispatch transitions are disabled; collect provider evidence "
        "with agentic_ops_snapshot.py and use the production reconciliation path."
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
