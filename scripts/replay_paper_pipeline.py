#!/usr/bin/env python3
"""Offline replay of the currently observed Telegram messages (PAPER only)."""
from __future__ import annotations

import json

from fatty_trader.analyzer.codex_runner import CodexRunResult
from fatty_trader.analyzer.paper_pipeline import PaperPipeline, observed_messages


def main() -> int:
    # Replay intentionally uses the deterministic fallback: no Codex credentials and no venue calls.
    pipeline = PaperPipeline(
        runner=lambda _: CodexRunResult(False, True, False, 1, "replay offline", "", "")
    )
    messages = observed_messages()
    for message in messages:
        pipeline.process(message)
    print(json.dumps({
        "mode": "PAPER",
        "messages": len(messages),
        "actionable": pipeline.canonical_signal_count,
        "non_actionable": len(messages) - pipeline.canonical_signal_count,
        "dispatches": pipeline.dispatch_repository.count,
        "live_orders_sent": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
