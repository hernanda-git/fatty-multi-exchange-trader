"""The transport budget is on escaped output, not just raw fields."""

import pytest

from fatty_trader.notifications import format_notification_html


@pytest.mark.parametrize("kind", ["execution-event", "execution-alert"])
@pytest.mark.parametrize("prefix", ["recovery-missing-protection:", "provider-read-failed:"])
def test_quote_expansion_does_not_exceed_telegram_budget(kind, prefix):
    text = format_notification_html(
        {
            "kind": kind,
            "to_state": '"' * 5000,
            "reason": prefix + '"' * 5000,
            "dispatch_id": '"' * 5000,
        }
    )
    assert len(text) < 4000
    assert text.count("<code>") == text.count("</code>")
