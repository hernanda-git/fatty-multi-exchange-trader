"""Recovery alerts must stay visible: accurate bucket, honest wording."""

from datetime import UTC, datetime

from fatty_trader.execution.bitget_dispatch_repository import _alert_bucket
from fatty_trader.notifications import format_notification_html, should_deliver_to_telegram


def _payload(**overrides):
    payload = {
        "kind": "execution-alert",
        "reason": "dispatcher-recovery-blocked",
        "state": "blocked",
        "evidence": "provider-position-not-uniquely-owned-and-protected:WLDUSDT",
        "issues": ["provider-position-not-uniquely-owned-and-protected:WLDUSDT"],
        "queued": 2,
        "oldest_queued_minutes": 431,
    }
    payload.update(overrides)
    return payload


def test_bucket_changes_at_six_hour_boundaries_and_date_rollover():
    # A recurring block must produce a fresh dedup key, or the owner is silenced.
    assert _alert_bucket(datetime(2026, 11, 19, 0, 0, tzinfo=UTC)) == "2026-11-19T00"
    assert _alert_bucket(datetime(2026, 11, 19, 5, 59, tzinfo=UTC)) == "2026-11-19T00"
    assert _alert_bucket(datetime(2026, 11, 19, 6, 0, tzinfo=UTC)) == "2026-11-19T06"
    assert _alert_bucket(datetime(2026, 11, 19, 23, 59, tzinfo=UTC)) == "2026-11-19T18"
    assert _alert_bucket(datetime(2026, 11, 20, 0, 0, tzinfo=UTC)) == "2026-11-20T00"


def test_blocked_card_says_trading_halted_and_shows_the_cost():
    rendered = format_notification_html(_payload())

    assert "TRADING HALTED" in rendered
    assert "No new entries" in rendered
    assert "Queued signals: <b>2</b>" in rendered
    assert "oldest waiting 431 min" in rendered
    assert "WLDUSDT" in rendered
    assert should_deliver_to_telegram(_payload()) is True


def test_advisory_card_says_trading_continues():
    rendered = format_notification_html(
        _payload(
            reason="dispatcher-recovery-warning",
            state="warning",
            queued=0,
            oldest_queued_minutes=None,
        )
    )

    assert "DISPATCHER WARNING" in rendered
    assert "Trading continues" in rendered
    assert "Queued signals: <b>0</b>" in rendered
    assert "TRADING HALTED" not in rendered
