"""Image-plus-caption analysis with a deliberately small JSON contract."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Sequence
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fatty_trader.analyzer.codex_runner import CodexRunResult
from fatty_trader.analyzer.source_guard import entry_stands_down
from fatty_trader.analyzer.trade_management import ManagementAction, SourceManagement
from fatty_trader.domain.enums import Direction
from fatty_trader.domain.models import CanonicalSignal


def image_source_revision(
    *,
    image_path: str,
    channel_id: int,
    message_id: int,
    text: str,
    canonical_revision: str | None = None,
    media_sha256: str | None = None,
) -> str:
    """Use intake's canonical identity, or hash real source identity and media bytes."""
    from fatty_trader.intake.media import MAX_MEDIA_BYTES

    with Path(image_path).open("rb") as image:
        content = image.read(MAX_MEDIA_BYTES + 1)
    if not content or len(content) > MAX_MEDIA_BYTES:
        raise ValueError("media is empty or exceeds its bound")
    digest = hashlib.sha256(content).hexdigest()
    if media_sha256 is not None and digest != media_sha256:
        raise ValueError("media does not match intake hash")
    if canonical_revision is not None:
        if re.fullmatch(r"[0-9a-f]{64}", canonical_revision) is None:
            raise ValueError("invalid canonical source revision")
        return canonical_revision
    identity = {
        "channel_id": channel_id,
        "message_id": message_id,
        "raw_text": text,
        "media_sha256": digest,
    }
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


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


def _extract_first_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    s = str(value).strip()
    match = re.search(r"(\d+(?:\.\d+(?:[eE][+-]?\d+)?)?)", s)
    if not match:
        return None
    try:
        return Decimal(match.group(1))
    except (InvalidOperation, ValueError):
        return None


def _extract_all_decimals(values: Any) -> list[Decimal]:
    if not values:
        return []
    if not isinstance(values, (list, tuple)):
        values = [values]
    res: list[Decimal] = []
    for item in values:
        for match in re.finditer(r"(\d+(?:\.\d+(?:[eE][+-]?\d+)?)?)", str(item)):
            try:
                res.append(Decimal(match.group(1)))
            except (InvalidOperation, ValueError):
                pass
    return res


def signal_from_image_json(
    data: dict[str, Any], *, message_id: int, source_revision: str, original_text: str = ""
) -> CanonicalSignal | None:
    if entry_stands_down(original_text):
        return None
    setup = data.get("setup")
    if not isinstance(setup, dict) or str(setup.get("action", "")).upper() != "ENTER":
        return None
    caption = str(data.get("message") or "").casefold()
    if entry_stands_down(caption):
        return None
    try:
        pair = str(setup.get("asset") or setup.get("pair") or "").upper()
        pair = pair.replace("#", "").replace("$", "").removesuffix("USDT")
        side = str(setup.get("side") or setup.get("direction") or "").upper()
        direction = Direction(side)

        entry = _extract_first_decimal(setup.get("entry") or setup.get("entry_price"))
        if entry is None and isinstance(setup.get("levels"), dict):
            entry = _extract_first_decimal(setup["levels"].get("chart_entry_zone"))
        stop = _extract_first_decimal(setup.get("stop_loss") or setup.get("stop"))
        if entry is None or stop is None or entry <= 0 or stop <= 0:
            return None

        raw_targets = setup.get("take_profits") or setup.get("targets")
        targets = _extract_all_decimals(raw_targets)
        if not targets and isinstance(setup.get("levels"), dict):
            level_key = "lower_marked_level" if direction is Direction.SHORT else "upper_marked_level"
            targets = _extract_all_decimals(setup["levels"].get(level_key))

        # Harmonize target scales if they were extracted in chart units (e.g. 1e-5) while entry is unscaled (e.g. 0.51)
        aligned_targets: list[Decimal] = []
        for target in targets:
            if target > 0 and entry > 0:
                ratio = float(entry / target)
                if ratio > 0:
                    k = round(math.log10(ratio))
                    if k != 0 and abs(k) <= 8:
                        candidate = target * (Decimal(10) ** k)
                    else:
                        candidate = target
                    # Geometry check for direction
                    if direction is Direction.LONG and candidate > entry:
                        aligned_targets.append(candidate)
                    elif direction is Direction.SHORT and candidate < entry:
                        aligned_targets.append(candidate)

        return CanonicalSignal(
            source_message_id=message_id,
            source_revision=source_revision,
            pair_token=pair,
            direction=direction,
            entry_price=entry,
            stop_loss=stop,
            take_profits=tuple(aligned_targets),
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
