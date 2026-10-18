"""Execution failures must not leak secrets or break Telegram limits."""

import pytest

from fatty_trader.notifications import format_notification_html


def test_execution_alert_renders_only_identities_it_has():
    with_intent = format_notification_html(
        {
            "kind": "execution-alert",
            "reason": "protection-plan-absent-settled",
            "intent": "live-WLDUSDT-test-sl",
            "symbol": "WLDUSDT",
        }
    )
    assert "Intent: <code>live-WLDUSDT-test-sl</code>" in with_intent
    assert "Symbol: <code>WLDUSDT</code>" in with_intent
    assert "Dispatch:" not in with_intent

    with_dispatch = format_notification_html(
        {"kind": "execution-alert", "reason": "dispatcher-recovery-blocked", "dispatch_id": "d-9"}
    )
    assert "Dispatch: <code>d-9</code>" in with_dispatch
    assert "Intent:" not in with_dispatch


@pytest.mark.parametrize("kind", ["execution-event", "execution-alert"])
@pytest.mark.parametrize(
    "prefix",
    [
        "recovery-missing-protection:",
        "recovery-filled-protection-unverified:",
        "provider-read-failed:",
    ],
)
def test_execution_reason_is_redacted(kind, prefix):
    text = format_notification_html(
        {
            "kind": kind,
            "to_state": "FILLED",
            "reason": prefix + "token=REVIEW_SECRET <script>",
            "dispatch_id": "test-dispatch",
        }
    )
    assert "REVIEW_SECRET" not in text
    assert "[redacted]" in text
    assert "<script>" not in text


@pytest.mark.parametrize("kind", ["execution-event", "execution-alert"])
@pytest.mark.parametrize(
    "prefix",
    [
        "recovery-missing-protection:",
        "recovery-filled-protection-unverified:",
        "provider-read-failed:",
    ],
)
def test_execution_card_is_bounded_without_cutting_html(kind, prefix):
    text = format_notification_html(
        {
            "kind": kind,
            "to_state": "&" * 5000,
            "reason": prefix + "&" * 5000,
            "dispatch_id": "&" * 5000,
        }
    )
    assert len(text) < 4000
    assert text.count("<code>") == text.count("</code>")
