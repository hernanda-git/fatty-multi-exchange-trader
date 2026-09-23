"""Image-plus-caption analysis with a deliberately small JSON contract."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fatty_trader.analyzer.codex_runner import CodexRunResult
from fatty_trader.analyzer.trade_management import ManagementAction, SourceManagement
from fatty_trader.domain.enums import Direction
from fatty_trader.domain.models import CanonicalSignal


def image_classifier_prompt(text: str) -> str:
    return (
        "Analyze the attached trading chart together with the caption. Return ONLY a JSON object "
        "with exactly these top-level keys: message and setup. The message is a concise summary. "
        "The setup is an object with action (ENTER or CLOSE), asset, side, entry, stop_loss, "
        "take_profits, and any clearly marked levels; use null or [] when absent or unreadable. "
        "Never invent prices, timeframe, stop loss, or targets. Do not return a status, decision, "
        "or execution instruction.\nCAPTION:\n" + text
    )


def analyze_image_json(
    *,
    text: str,
    message_id: int,
    image_path: str,
    runner: Callable[[str, Sequence[str]], CodexRunResult] | Any,
) -> dict[str, Any]:
    del message_id  # retained in the API for traceable callers
    if not Path(image_path).is_file():
        return {"message": "Image analysis unavailable", "setup": {}}
    prompt = image_classifier_prompt(text)
    result = (
        runner(prompt, (image_path,))
        if callable(runner)
        else runner.run(prompt, image_paths=(image_path,))
    )
    if not result.succeeded:
        return {"message": "Image analysis unavailable", "setup": {}}
    try:
        raw = result.stdout.strip()
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start : end + 1])
        if not isinstance(data, dict):
            raise ValueError("result is not an object")
        setup = data.get("setup")
        if not isinstance(setup, dict):
            setup = {}
        return {"message": str(data.get("message") or ""), "setup": setup}
    except (ValueError, TypeError, json.JSONDecodeError):
        return {"message": "Image analysis returned invalid JSON", "setup": {}}


def signal_from_image_json(
    data: dict[str, Any], *, message_id: int, source_revision: str
) -> CanonicalSignal | None:
    setup = data.get("setup")
    if not isinstance(setup, dict) or str(setup.get("action", "ENTER")).upper() == "CLOSE":
        return None
    try:
        pair = str(setup.get("asset") or setup.get("pair") or "").upper()
        pair = pair.replace("#", "").replace("$", "").removesuffix("USDT")
        side = str(setup.get("side") or setup.get("direction") or "").upper()
        entry = Decimal(str(setup.get("entry") or setup.get("entry_price")))
        stop = Decimal(str(setup.get("stop_loss")))
        targets = setup.get("take_profits") or setup.get("targets") or []
        if not isinstance(targets, list):
            targets = [targets]
        return CanonicalSignal(
            source_message_id=message_id,
            source_revision=source_revision,
            pair_token=pair,
            direction=Direction(side),
            entry_price=entry,
            stop_loss=stop,
            take_profits=tuple(Decimal(str(target)) for target in targets),
        )
    except (InvalidOperation, TypeError, ValueError):
        return None


def management_from_image_json(data: dict[str, Any]) -> SourceManagement | None:
    setup = data.get("setup")
    if not isinstance(setup, dict) or str(setup.get("action", "")).upper() != "CLOSE":
        return None
    asset = str(setup.get("asset") or setup.get("pair") or "").upper()
    asset = asset.replace("#", "").replace("$", "").removesuffix("USDT")
    if not asset:
        return None
    return SourceManagement(symbol=f"{asset}USDT", action=ManagementAction.CLOSE)
