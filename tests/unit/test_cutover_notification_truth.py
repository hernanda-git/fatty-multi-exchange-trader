"""A gate decision does not establish the configured provider mode."""

from fatty_trader.notifications import format_notification_html


def test_cutover_refusal_does_not_invent_demo_mode():
    report = format_notification_html(
        {"kind": "execution-event", "to_state": "REJECTED", "reason": "cutover-gated"}
    )
    assert "Tidak ada order dikirim" in report
    assert "gate eksekusi tertutup" in report
    assert "mode DEMO" not in report
