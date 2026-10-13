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
        "marginSize": "1.2034",
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


def test_fallback_metadata_is_loaded(monkeypatch) -> None:
    statuses = health_report.load_sltp_status(
        {"positions": [provider_position()], "open_orders": []},
        [
            [
                "WLDUSDT",
                "LONG",
                "0.3971",
                "0.388",
                '["0.427"]',
                "91",
                "active",
                "live-bitget-WLDUSDT-key",
                "2026-09-13 14:28:35+00",
            ]
        ],
    )

    assert statuses["WLDUSDT"]["fallback"] is True
    assert statuses["WLDUSDT"]["fallback_sl"] == "0.388"
    assert statuses["WLDUSDT"]["fallback_tp"] == "0.427"
    assert statuses["WLDUSDT"]["fallback_quantity"] == "91"
    assert statuses["WLDUSDT"]["fallback_state"] == "ACTIVE"
    assert statuses["WLDUSDT"]["enforcement"] == "UNKNOWN"


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
                "margin_used": "1.2034",
            }
        ],
        pending_orders=[],
        sltp={
            "WLDUSDT": {
                "has_sl": False,
                "has_tp": False,
                "sl_label": "MON",
                "tp_label": "MON",
                "fallback": True,
                "fallback_state": "ACTIVE",
                "fallback_entry": "0.3971",
                "fallback_quantity": "91",
                "fallback_sl": "0.388",
                "fallback_tp": "0.427",
            }
        },
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
        account={
            "read_status": "OK",
            "equity": "8.49917295",
            "available": "7.68593629",
            "unrealized_pl": "-0.3913",
        },
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
    assert "provider 1" in html
    assert "DB 0" in html
    assert "Native      SL MISSING · TP MISSING" in html
    assert "FALLBACK ACTIVE" in html
    assert "SL 0.388" in html
    assert "TP 0.427" in html
    assert "BETWEEN SL / TP" in html
    assert "Liquidation" in html
    assert "Liq gap" in html
    assert "0 pending orders · provider read OK" in html
    assert "N/A (no open positions)" not in html


def test_host_and_slash_load_same_core_financial_evidence(monkeypatch) -> None:
    import json

    from test_health_truthfulness import Gateway, snapshot
    from test_operator_health import LoadedConnection, LoadedCursor

    slash = snapshot(Gateway(), LoadedConnection)

    def query_one(sql):
        cursor = LoadedCursor()
        cursor.execute(sql)
        return cursor.fetchone()

    def query_all(sql):
        cursor = LoadedCursor()
        cursor.execute(sql)
        return cursor.fetchall()

    def provider(cmd):
        if "_probe_account.py" in cmd:
            return json.dumps(slash["account"])
        if "_probe_open_state.py" in cmd:
            return '{"positions": [], "open_orders": []}'
        return "LIVE|LIVE|1"

    monkeypatch.setattr(health_report, "query_one", query_one)
    monkeypatch.setattr(health_report, "query_all", query_all)
    monkeypatch.setattr(health_report, "docker_exec_bitget", provider)
    monkeypatch.setattr(health_report, "get_codex_usage", lambda: {"status": "AUTH_FAILED"})
    monkeypatch.setattr(health_report, "get_service_status", lambda: {"status": "UNKNOWN"})
    cron = health_report.load_report_data()
    assert cron["account"] == slash["account"]
    assert cron["pnl"] == slash["pnl"]
    assert (
        cron["metrics"]["last_admission_captured_at"]
        == slash["metrics"]["last_admission_captured_at"]
    )
    assert cron["metrics"]["reservation_totals"] == slash["metrics"]["reservation_totals"]
    assert cron["messages"] == slash["messages"]
    assert cron["codex"]["status"] == "AUTH_FAILED"
    assert cron["account"]["read_status"] == "OK"


def test_failed_open_state_section_does_not_erase_successful_orders(monkeypatch) -> None:
    monkeypatch.setattr(
        health_report,
        "docker_exec_bitget",
        lambda _cmd: '{"positions": {"error_type": "TimeoutError"}, "open_orders": []}',
    )
    state = health_report.load_provider_state()
    assert health_report.load_positions(state) is None
    assert health_report.load_pending_orders(state) == []


def test_supplied_failed_provider_snapshot_is_not_reloaded(monkeypatch) -> None:
    def unexpected_read():
        raise AssertionError("a failed supplied snapshot must remain failed")

    monkeypatch.setattr(health_report, "load_provider_state", unexpected_read)
    assert health_report.load_positions(None) is None
    assert health_report.load_pending_orders(None) is None


def test_host_json_transport_preserves_decimal_nulls_and_message_delimiters(monkeypatch) -> None:
    from decimal import Decimal
    from types import SimpleNamespace

    monkeypatch.setattr(
        health_report.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout='[{"fee":0.02168166,"other":null,"message":"a|b\\n<source>"}]',
        ),
    )
    assert health_report.query_one("SELECT 1") == (
        Decimal("0.02168166"),
        None,
        "a|b\n<source>",
    )


def test_account_probe_uses_same_allowlisted_normalization(capsys) -> None:
    import asyncio
    import json
    from unittest.mock import patch

    probe_path = SCRIPT.parent / "_probe_account.py"
    spec = importlib.util.spec_from_file_location("account_probe", probe_path)
    assert spec is not None and spec.loader is not None
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)

    class Client:
        environment = "LIVE"

        def __init__(self, **kwargs):
            self.closed = False

        async def get_account(self, **kwargs):
            return {
                "accountEquity": "9.17026367",
                "available": "9.17026367",
                "unrealizedPL": "-0.1",
                "apiKey": "secret",
                "uid": "private",
            }

        async def aclose(self):
            self.closed = True

    with (
        patch.object(probe, "BitgetRestClient", Client),
        patch.dict(
            probe.os.environ,
            {
                "BITGET_API_KEY": "fixture",
                "BITGET_API_SECRET": "fixture",
                "BITGET_API_PASSPHRASE": "fixture",
            },
        ),
    ):
        asyncio.run(probe.main())
    account = json.loads(capsys.readouterr().out)
    assert account["equity"] == "9.17026367"
    assert account["unrealized_pl"] == "-0.1"
    assert account["environment"] == "LIVE"
    assert account["observed_at"]
    assert "secret" not in str(account)
    assert "private" not in str(account)


def test_malformed_position_rows_are_unknown_not_filtered_to_flat(monkeypatch) -> None:
    monkeypatch.setattr(health_report, "query_all", lambda _sql: [])
    for value in (None, "", "NaN", "Infinity", "-1"):
        row = {**provider_position(), "total": value}
        assert health_report.load_positions({"positions": [row], "open_orders": []}) is None


def test_missing_order_fields_do_not_become_an_observed_empty_or_zero_order() -> None:
    assert health_report.load_pending_orders({"positions": [], "open_orders": [{}]}) is None
