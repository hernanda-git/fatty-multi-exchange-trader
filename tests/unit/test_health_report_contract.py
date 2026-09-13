"""Contract tests for the scheduled and legacy Fatty health reports.

The shell report remains covered for its historical format contract. The
scheduled unit must use the provider-first Python report so the live Telegram
card cannot silently fall back to DB-only position snapshots.
"""

from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "telegram_health_report.sh"
DEPLOY_SERVICE = (
    Path(__file__).resolve().parents[2] / "deploy" / "systemd" / "fatty-health-report.service"
)


def _scheduled_service_text() -> str:
    return DEPLOY_SERVICE.read_text(encoding="utf-8")


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_exists():
    assert SCRIPT.is_file(), f"missing {SCRIPT}"


def test_required_section_headers():
    text = _text()
    headers = ("<b>Balance</b>", "<b>Positions</b>", "<b>Orders</b>", "<b>PNL</b>", "<b>Safety</b>")
    for header in headers:
        assert header in text, f"missing section header {header}"


def test_positions_table_columns():
    text = _text()
    for col in ("SYMBOL", "SIDE", "SIZE", "ENTRY", "MARK", "LIQ", "UPNL"):
        assert col in text, f"positions table missing column {col}"


def test_pnl_rows():
    text = _text()
    for row in ("Profit", "Loss", "Fees", "Net", "Unrealized"):
        assert row in text, f"PNL block missing row {row}"


def test_safety_line():
    text = _text()
    for token in ("Isolated", "Leverage", "SL-before-liq"):
        assert token in text, f"safety block missing token {token}"


def test_html_escape_helper_present_and_used():
    text = _text()
    assert "html_escape()" in text, "missing html_escape helper definition"
    # dynamic block values must be routed through esc()/html_escape
    assert "esc(" in text or "| html_escape" in text
    assert text.count("html_escape") >= 3, "dynamic values must use html_escape"


def test_pre_tables_only_no_table_tags():
    text = _text()
    assert "<pre>" in text, "expected <pre> fixed-width tables"
    assert "<table" not in text.lower(), "Telegram HTML must not use <table> tags"


def test_message_size_guard():
    text = _text()
    assert "4096" in text, "missing Telegram 4096-char limit reference"
    assert "truncat" in text.lower(), "missing truncation notice"


def test_na_or_stale_fallback():
    text = _text()
    assert "N/A" in text, "missing N/A fallback for unavailable data"
    assert "STALE" in text, "missing STALE fallback for outdated data"


def test_live_tables_match_schema_names():
    text = _text()
    for table in ("balance_snapshots", "position_snapshots", "live_order_intents", "fills"):
        assert table in text, f"report must query live table {table}"


def test_scheduled_unit_uses_provider_first_report():
    text = _scheduled_service_text()
    assert "scripts/health_report.py" in text
    assert "telegram_health_report.sh" not in text
    assert "TimeoutStartSec=120" in text
