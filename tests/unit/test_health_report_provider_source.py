from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "health_report.py"
SPEC = importlib.util.spec_from_file_location("health_report", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
health_report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(health_report)


def provider_position() -> dict[str, str]:
    return {
        "symbol": "WLDUSDT",
        "holdSide": "long",
        "total": "91",
        "openPriceAvg": "0.3971",
        "markPrice": "0.3928",
        "leverage": "30",
        "marginMode": "isolated",
        "liquidationPrice": "0.386647193124",
        "unrealizedPL": "-0.3913",
        "stopLoss": "",
        "stopLossId": "",
        "takeProfit": "",
        "takeProfitId": "",
    }


def test_provider_position_is_visible_when_ledger_row_is_missing(monkeypatch) -> None:
    monkeypatch.setattr(health_report, "query_all", lambda _sql: [])

    positions = health_report.load_positions(
        {"positions": [provider_position()], "open_orders": []}
    )

    assert positions is not None
    assert len(positions) == 1
    assert positions[0]["symbol"] == "WLDUSDT"
    assert positions[0]["direction"] == "LONG"
    assert positions[0]["qty"] == "91"
    assert positions[0]["margin_mode"] == "isolated"
    assert positions[0]["unrealized_pl"] == "-0.3913"


def test_provider_read_failure_is_not_reported_as_flat(monkeypatch) -> None:
    monkeypatch.setattr(health_report, "load_provider_state", lambda: None)

    assert health_report.load_positions() is None
    assert health_report.load_pending_orders() is None


def test_fallback_protection_is_labeled_as_monitoring(monkeypatch) -> None:
    monkeypatch.setattr(health_report, "query_all", lambda _sql: [["WLDUSDT"]])

    statuses = health_report.load_sltp_status(
        {"positions": [provider_position()], "open_orders": []}
    )

    assert statuses["WLDUSDT"]["sl_label"] == "MON"
    assert statuses["WLDUSDT"]["tp_label"] == "MON"


def test_report_separates_provider_position_from_db_position() -> None:
    html = health_report.format_report(
        positions=[
            {
                "symbol": "WLDUSDT",
                "direction": "LONG",
                "qty": "91",
                "entry_price": "0.3971",
                "mark_price": "0.3928",
                "leverage": "30",
                "margin_mode": "isolated",
                "unrealized_pl": "-0.3913",
            }
        ],
        pending_orders=[],
        sltp={"WLDUSDT": {"has_sl": False, "has_tp": False, "sl_label": "MON", "tp_label": "MON"}},
        pnl={
            "fill_n": "1",
            "total_pnl": "0",
            "gross_profit": "0",
            "gross_loss": "0",
            "total_fees": "0.02168166",
        },
        messages=[],
        metrics={
            "messages": "37",
            "signals": "14",
            "open_positions": "0",
            "provider_positions": "1",
            "pending_orders": "0",
            "total_orders": "0",
        },
        account={"equity": "8.49917295", "available": "7.68593629", "unrealized_pl": "-0.3913"},
        modes={"mode": "LIVE", "venue_mode": "LIVE", "execution_enabled": "1"},
        codex={
            "status": "LIVE",
            "plan": "plus",
            "5h": "0%",
            "7d": "0%",
            "reset": "N/A",
            "refreshed": "now",
        },
    )

    assert "WLDUSDT" in html
    assert "Provider pos  1" in html
    assert "DB pos        0" in html
    assert "MON" in html
    assert "N/A (no open positions)" not in html
