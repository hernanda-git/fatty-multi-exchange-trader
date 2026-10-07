"""Queue creation and historical fill recovery must not imply new execution."""

from fatty_trader.notifications import format_notification_html


def test_signal_analysis_reports_queue_not_execution_confirmation():
    report = format_notification_html(
        {
            "kind": "signal-analysis",
            "canonical_signal": True,
            "pair": "TRUMP",
            "direction": "SHORT",
            "entry": "2.008",
            "stop_loss": "2.042",
            "take_profits": ["1.74"],
            "dispatches": 1,
        }
    )
    assert "1 antrean eksekusi dibuat" in report
    assert "Belum ada konfirmasi order/fill provider pada laporan ini" in report
    assert "proses eksekusi dibuat" not in report


def test_signal_analysis_with_no_dispatch_does_not_claim_queue_exists():
    report = format_notification_html(
        {"kind": "signal-analysis", "canonical_signal": True, "dispatches": 0}
    )
    assert "Tidak ada antrean eksekusi dibuat" in report
    assert "Belum ada konfirmasi order/fill provider pada laporan ini" in report


def test_historical_recovery_fill_is_not_new_trade_success():
    report = format_notification_html(
        {
            "kind": "execution-event",
            "from_state": "FILLED",
            "to_state": "FILLED",
            "reason": "recovery-missing-protection:owned-flat-close-fill-unproven",
            "dispatch_id": "historical-dispatch",
        }
    )
    assert "Recovery Diblokir" in report
    assert "Fill historis, bukan entry baru" in report
    assert "Penutupan/proteksi belum terverifikasi" in report
    assert "Status Eksekusi" not in report
