"""Signal-level selectivity: reject before the venue, prove the reason after.

Every rule is off by default. A rule may only be switched on when the scorecard
(``scripts/signal_scorecard.py``) shows it would have improved expectancy, and
each rejection records a ``policy-*`` reason so the scorecard can keep proving
it right or wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping, Sequence

POLICY_REASONS = ("policy-reward-risk-floor", "policy-no-target")


@dataclass(frozen=True)
class SelectivitySettings:
    min_reward_risk: Decimal | None = None
    require_target: bool = False

    @property
    def enabled(self) -> bool:
        return self.min_reward_risk is not None or self.require_target

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> SelectivitySettings:
        raw = (environ.get("BITGET_POLICY_MIN_REWARD_RISK") or "").strip()
        minimum: Decimal | None = None
        if raw:
            try:
                parsed = Decimal(raw)
            except InvalidOperation as exc:
                raise ValueError("BITGET_POLICY_MIN_REWARD_RISK must be numeric") from exc
            if parsed <= 0:
                raise ValueError("BITGET_POLICY_MIN_REWARD_RISK must be positive")
            minimum = parsed
        require = (environ.get("BITGET_POLICY_REQUIRE_TARGET") or "").strip() == "1"
        return cls(min_reward_risk=minimum, require_target=require)


def reject_reason(
    *,
    entry: Decimal,
    stop_loss: Decimal,
    take_profits: Sequence[Decimal],
    direction: str,
    settings: SelectivitySettings,
) -> str | None:
    """Reason to refuse this entry, or ``None`` to let it through.

    Fails open on anything it cannot compute: an unreadable setup is a parser
    problem, not proof that the trade is bad.
    """
    if not settings.enabled:
        return None
    targets = [target for target in take_profits if target is not None]
    if not targets:
        return "policy-no-target" if settings.require_target else None
    if settings.min_reward_risk is None:
        return None
    risk = _risk(entry=entry, stop_loss=stop_loss, direction=direction)
    if risk is None:
        return None
    best = max(_reward(entry=entry, target=target, direction=direction) / risk for target in targets)
    if best < settings.min_reward_risk:
        return "policy-reward-risk-floor"
    return None


def _risk(*, entry: Decimal, stop_loss: Decimal, direction: str) -> Decimal | None:
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return None
    if direction.upper() in {"LONG", "BUY"} and stop_loss >= entry:
        return None
    if direction.upper() in {"SHORT", "SELL"} and stop_loss <= entry:
        return None
    return risk


def _reward(*, entry: Decimal, target: Decimal, direction: str) -> Decimal:
    if direction.upper() in {"SHORT", "SELL"}:
        return entry - target
    return target - entry
