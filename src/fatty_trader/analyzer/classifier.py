"""Strict JSON-to-domain classification for arbitrary Telegram text."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from fatty_trader.domain.enums import Direction
from fatty_trader.domain.models import CanonicalSignal


@dataclass(frozen=True)
class SignalClassification:
    actionable: bool
    pair: str | None
    side: str | None
    entry: Decimal | None
    stop_loss: Decimal | None
    take_profits: tuple[Decimal, ...]
    confidence: Decimal | None
    reason: str
    signal: CanonicalSignal | None = None


def classifier_prompt(text: str) -> str:
    """Build a prompt that makes missing values explicit rather than guessed."""
    return (
        "Classify this arbitrary Telegram text as a trade signal. Return ONLY JSON with keys "
        "actionable, pair, side, entry, stop_loss, take_profits, confidence, reason. "
        "Use null or [] for values absent from the text; never infer or invent prices. "
        "actionable is true only when pair, side, entry, stop_loss and at least one take profit "
        "are explicitly present and their geometry is valid.\nTEXT:\n" + text
    )


def classify_json(text: str, output: str, *, message_id: int) -> SignalClassification:
    try:
        data = json.loads(_json_object(output))
        if not isinstance(data, dict):
            raise ValueError("classifier output is not an object")
        return _from_mapping(text, data, message_id)
    except (ValueError, TypeError, json.JSONDecodeError, InvalidOperation) as exc:
        return SignalClassification(
            False, None, None, None, None, (), None, f"invalid classifier JSON: {exc}"
        )


def _json_object(output: str) -> str:
    cleaned = output.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I | re.S)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError("missing JSON object")
    return cleaned[start : end + 1]


def _decimal(value: Any, name: str) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    return Decimal(str(value))


def _from_mapping(text: str, data: dict[str, Any], message_id: int) -> SignalClassification:
    actionable = data.get("actionable") is True
    pair_value = data.get("pair")
    pair = str(pair_value).upper().replace("#", "").replace("$", "") if pair_value else None
    if pair:
        pair = pair.removesuffix("USDT")
    side_value = data.get("side")
    side = str(side_value).upper() if side_value else None
    entry = _decimal(data.get("entry"), "entry")
    stop = _decimal(data.get("stop_loss"), "stop_loss")
    raw_tps = data.get("take_profits") or []
    if not isinstance(raw_tps, list):
        raise ValueError("take_profits must be an array")
    tps = tuple(_decimal(value, "take_profits") for value in raw_tps)
    if any(value is None for value in tps):
        raise ValueError("take_profits cannot contain null")
    take_profits = tuple(value for value in tps if value is not None)
    confidence = _decimal(data.get("confidence"), "confidence")
    reason = str(data.get("reason") or "")
    signal = None
    if actionable:
        if (
            pair is None
            or side not in {"LONG", "SHORT"}
            or entry is None
            or stop is None
            or not take_profits
        ):
            return SignalClassification(
                False,
                pair,
                side,
                entry,
                stop,
                take_profits,
                confidence,
                "missing required trade geometry",
            )
        try:
            signal = CanonicalSignal(
                source_message_id=message_id,
                source_revision=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                pair_token=pair,
                direction=Direction(side),
                entry_price=entry,
                stop_loss=stop,
                take_profits=take_profits,
            )
        except ValueError as exc:
            return SignalClassification(
                False,
                pair,
                side,
                entry,
                stop,
                take_profits,
                confidence,
                f"invalid geometry: {exc}",
            )
    return SignalClassification(
        actionable, pair, side, entry, stop, take_profits, confidence, reason, signal
    )
