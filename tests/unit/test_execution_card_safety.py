"""Execution failures must not leak secrets or break Telegram limits."""

import pytest

from fatty_trader.notifications import format_notification_html


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
