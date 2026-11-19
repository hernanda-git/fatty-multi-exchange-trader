"""Selectivity must be off by default and only reject on what it can prove."""

from decimal import Decimal

import pytest

from fatty_trader.policy.selectivity import SelectivitySettings, reject_reason


def _reason(**kwargs):
    defaults = dict(
        entry=Decimal("1.00"),
        stop_loss=Decimal("0.90"),
        take_profits=(Decimal("1.30"),),
        direction="LONG",
        settings=SelectivitySettings(),
    )
    defaults.update(kwargs)
    return reject_reason(**defaults)


def test_disabled_policy_never_rejects():
    assert _reason() is None
    assert _reason(take_profits=()) is None


def test_reward_risk_floor_rejects_a_thin_setup():
    settings = SelectivitySettings(min_reward_risk=Decimal("2"))

    # entry 1.00, stop 0.90 -> risk 0.10; target 1.10 -> R=1; target 1.30 -> R=3
    assert _reason(take_profits=(Decimal("1.10"),), settings=settings) == "policy-reward-risk-floor"


def test_reward_risk_floor_accepts_at_the_threshold_and_beyond():
    settings = SelectivitySettings(min_reward_risk=Decimal("2"))

    assert _reason(take_profits=(Decimal("1.15"),), settings=settings) == "policy-reward-risk-floor"
    assert _reason(take_profits=(Decimal("1.20"),), settings=settings) is None
    assert _reason(take_profits=(Decimal("1.30"),), settings=settings) is None


def test_short_reward_risk_is_measured_downward():
    settings = SelectivitySettings(min_reward_risk=Decimal("2"))

    assert (
        _reason(
            direction="SHORT",
            entry=Decimal("1.00"),
            stop_loss=Decimal("1.10"),
            take_profits=(Decimal("0.75"),),
            settings=settings,
        )
        is None
    )
    assert (
        _reason(
            direction="SHORT",
            entry=Decimal("1.00"),
            stop_loss=Decimal("1.10"),
            take_profits=(Decimal("0.95"),),
            settings=settings,
        )
        == "policy-reward-risk-floor"
    )


def test_require_target_rejects_target_less_signals_only_when_enabled():
    settings = SelectivitySettings(require_target=True)

    assert _reason(take_profits=(), settings=settings) == "policy-no-target"
    assert _reason(take_profits=(), settings=SelectivitySettings()) is None


def test_environment_parsing_fails_closed_on_bad_values():
    assert SelectivitySettings.from_environ({}).enabled is False
    assert (
        SelectivitySettings.from_environ({"BITGET_POLICY_MIN_REWARD_RISK": "1.5"}).min_reward_risk
        == Decimal("1.5")
    )
    with pytest.raises(ValueError):
        SelectivitySettings.from_environ({"BITGET_POLICY_MIN_REWARD_RISK": "abc"})
    with pytest.raises(ValueError):
        SelectivitySettings.from_environ({"BITGET_POLICY_MIN_REWARD_RISK": "0"})
