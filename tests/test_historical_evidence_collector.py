"""GET-only historical collector contract; no production credentials."""

import importlib.util
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts/collect_bitget_historical_evidence.py"


def module():
    spec = importlib.util.spec_from_file_location("collector", PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_pagination_preserves_short_page_then_explicit_empty():
    import asyncio

    c = module()
    calls = []
    raw = [
        {"code": "00000", "data": {"fillList": [{"tradeId": "8"}], "endId": "8"}},
        {"code": "00000", "data": {"fillList": [], "endId": ""}},
    ]

    async def get(path, params):
        calls.append(dict(params))
        return raw[len(calls) - 1]

    result = asyncio.run(c.paginate(get, "/fill", {}, "fillList"))
    assert calls == [{"limit": "100"}, {"limit": "100", "idLessThan": "8"}]
    assert result["status"] == "explicit-empty"
    assert [page["raw"] for page in result["pages"]] == raw


@pytest.mark.parametrize(
    "raw,expected",
    [
        ({"code": "00000", "data": None}, "successful-null-unproven"),
        (
            {"code": "00000", "data": {"fillList": None, "endId": None}},
            "successful-null-list-unproven",
        ),
        ({"code": "00000", "data": {"fillList": [{}], "endId": ""}}, "missing-cursor"),
        ({"code": "00000", "data": {"fillList": "bad"}}, "malformed-list"),
        ({"code": "40017", "data": None}, "provider-error"),
        ({"code": "00000", "data": {}}, "malformed-list"),
    ],
)
def test_uncertain_terminal_is_not_exhaustion(raw, expected):
    import asyncio

    c = module()

    async def get(path, params):
        return raw

    result = asyncio.run(c.paginate(get, "/fill", {}, "fillList", max_pages=2))
    assert result["status"] == expected
    assert result["pages"][0]["raw"] == raw


def test_transport_rejects_mutations_before_network():
    import asyncio

    import httpx

    c = module()
    calls = []

    async def handler(request):
        calls.append(request.method)
        return httpx.Response(200, json={"code": "00000", "data": []})

    async def run():
        async with httpx.AsyncClient(
            transport=c.GetOnlyTransport(httpx.MockTransport(handler))
        ) as client:
            await client.get("https://api.bitget.com/api/v2/mix/order/fill-history")
            for method in ("POST", "PUT", "DELETE", "PATCH"):
                with pytest.raises(ValueError, match="GET-only"):
                    await client.request(
                        method, "https://api.bitget.com/api/v2/mix/order/place-order"
                    )

    asyncio.run(run())
    assert calls == ["GET"]


def test_symbol_collection_covers_native_plans_orders_fills_positions():
    import asyncio

    c = module()
    calls = []

    async def get(path, params):
        calls.append((path, params))
        key = {
            "fill-history": "fillList",
            "fills": "fillList",
            "orders-history": "entrustedList",
            "history-position": "list",
            "orders-plan-history": "entrustedList",
        }[path.rsplit("/", 1)[1]]
        return {"code": "00000", "data": {key: [], "endId": ""}}

    result = asyncio.run(c.collect_symbol(get, "PUMPUSDT", 1, 2))
    assert len(result) == 7
    assert {p["planType"] for path, p in calls if "plan-history" in path} == {
        "normal_plan",
        "track_plan",
        "profit_loss",
    }
    assert all(p["startTime"] == "1" and p["endTime"] == "2" for _, p in calls)


def test_signed_reader_keeps_raw_successful_null_and_safe_error():
    import asyncio

    import httpx

    from fatty_trader.exchanges.bitget.client import BitgetRestClient

    c = module()
    seen = []

    async def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"code": "00000", "data": None})

    async def run():
        client = BitgetRestClient(
            "testkey",
            "testsecret",
            "testphrase",
            transport=c.GetOnlyTransport(httpx.MockTransport(handler)),
        )
        records = []
        reader = c.RawReader(client, records.append, delay=0)
        assert await reader.get("/api/v2/mix/order/fill-history", {"symbol": "PUMPUSDT"}) == {
            "code": "00000",
            "data": None,
        }
        assert records[0]["raw"]["data"] is None
        assert "headers" not in records[0]
        await client.aclose()

    asyncio.run(run())
    assert seen[0].method == "GET"
    assert seen[0].headers["ACCESS-KEY"] == "testkey"


def test_host_snapshot_is_read_only_private_and_gets_only_monitor(monkeypatch, tmp_path):
    import json
    import subprocess

    c = module()
    calls = []
    entry = {
        "client_order_id": "entry1",
        "provider_order_id": "123",
        "symbol": "PUMPUSDT",
        "created_at": "2026-10-01T00:00:00+00:00",
    }

    def run(args, **kwargs):
        calls.append((args, kwargs))
        if "postgres" in args:
            return subprocess.CompletedProcess(args, 0, json.dumps({"entries": [entry]}), "")
        return subprocess.CompletedProcess(
            args,
            0,
            json.dumps({"kind": "symbol-result", "symbol": "PUMPUSDT", "sources": []}) + "\n",
            "",
        )

    monkeypatch.setattr(c.subprocess, "run", run)
    c.run_host(Path("/production"), tmp_path, expected_entries=1)
    assert "BEGIN READ ONLY" in calls[0][1]["input"]
    assert "fatty_app" in calls[0][0] and "fatty_trader" in calls[0][0]
    assert (
        calls[1][0][-4:] == ["monitor-bitget", "/app/.venv/bin/python", "-"][-4:]
        or "monitor-bitget" in calls[1][0]
    )
    assert "dispatcher-bitget" not in calls[1][0]
    assert (tmp_path / "database.json").stat().st_mode & 0o777 == 0o600
    assert json.loads((tmp_path / "PUMPUSDT.json").read_text())["symbol"] == "PUMPUSDT"


def test_archived_plan_collection_targets_exact_entry_oids_and_execute_orders():
    import asyncio

    c = module()
    calls = []

    async def get(path, params):
        calls.append((path, dict(params)))
        if path.endswith("orders-plan-history"):
            if "idLessThan" in params:
                return {"code": "00000", "data": {"entrustedList": [], "endId": ""}}
            return {
                "code": "00000",
                "data": {
                    "entrustedList": [
                        {"orderId": "77", "clientOid": "entry1-sl", "executeOrderId": "88"}
                    ],
                    "endId": "77",
                },
            }
        return {"code": "00000", "data": {"fillList": [], "endId": ""}}

    result = asyncio.run(
        c.collect_archived_plans(get, [{"client_order_id": "entry1", "symbol": "PUMPUSDT"}], 1, 999)
    )
    assert {p.get("clientOid") for path, p in calls if "clientOid" in p} == {
        "entry1-sl",
        "entry1-tp",
    }
    assert any(path.endswith("detail") and p.get("orderId") == "88" for path, p in calls)
    assert any(path.endswith("fill-history") and p.get("orderId") == "88" for path, p in calls)
    assert result["execute_order_ids"] == ["88"]


def test_analysis_keeps_plan_chain_separate_from_epoch_ownership():
    c = module()
    entry = {
        "client_order_id": "entry1",
        "provider_order_id": "1",
        "symbol": "PUMPUSDT",
        "filled_qty": "2",
        "margin_reservation_id": None,
    }
    fill = {
        "provider_fill_id": "11",
        "client_order_id": "entry1",
        "quantity": "2",
        "price": "3",
        "fee": "-0.01",
        "realized_pnl": "0",
    }
    proof = {
        "sources": [
            {
                "path": "/api/v2/mix/order/fill-history",
                "status": "explicit-empty",
                "pages": [
                    {
                        "raw": {
                            "data": {
                                "fillList": [
                                    {
                                        "tradeId": "11",
                                        "orderId": "1",
                                        "symbol": "PUMPUSDT",
                                        "baseVolume": "2",
                                        "price": "3",
                                        "profit": "0",
                                        "feeDetail": [{"totalFee": "-0.01"}],
                                    }
                                ]
                            }
                        }
                    }
                ],
            },
            {
                "path": "/api/v2/mix/order/orders-history",
                "status": "explicit-empty",
                "pages": [
                    {
                        "raw": {
                            "data": {
                                "entrustedList": [
                                    {"orderId": "1", "clientOid": "entry1"},
                                    {"orderId": "2", "clientOid": "77", "reduceOnly": "YES"},
                                ]
                            }
                        }
                    }
                ],
            },
            {
                "path": "/api/v2/mix/position/history-position",
                "status": "explicit-empty",
                "pages": [{"raw": {"data": {"list": [{"positionId": "99", "ctime": "1000"}]}}}],
            },
            {
                "path": "/api/v2/mix/order/orders-plan-history",
                "status": "explicit-empty",
                "pages": [
                    {
                        "raw": {
                            "data": {
                                "entrustedList": [
                                    {
                                        "orderId": "77",
                                        "clientOid": "entry1-sl",
                                        "executeOrderId": "2",
                                    }
                                ]
                            }
                        }
                    }
                ],
            },
        ],
        "details": [],
        "archived": {"sources": []},
    }
    result = c.assess_entry(entry, [fill], proof)
    assert result["exact_entry_order_and_fill_identity"] is True
    assert c.assess_entry(entry, [], proof)["authenticated_entry_receipt"] is True
    assert result["entry_economics"]["fill-history"][0]["exact_durable_economics"] is True
    assert result["exact_named_plan_chains"][0]["executeOrderId"] == "2"
    assert result["full_epoch_ownership_provable"] is False
    assert "entry-to-position-epoch-binding-missing" in result["blockers"]
    proof["sources"][1]["pages"][0]["raw"]["data"]["entrustedList"][0]["clientOid"] = "other"
    assert c.assess_entry(entry, [fill], proof)["exact_entry_order_and_fill_identity"] is False


def test_private_artifact_keeps_decimal_precision(tmp_path):
    import json
    from decimal import Decimal

    c = module()
    value = Decimal("0.123456789012345678901")
    c.private_json(tmp_path / "receipt.json", {"price": value})
    assert json.loads((tmp_path / "receipt.json").read_text())["price"] == str(value)
    with pytest.raises(FileExistsError):
        c.private_json(tmp_path / "receipt.json", {})


def test_positive_durable_fee_matches_only_without_precision_rewrite():
    c = module()
    entry = {
        "client_order_id": "e",
        "provider_order_id": "1",
        "symbol": "PUMPUSDT",
        "filled_qty": "2",
    }
    fill = {
        "provider_fill_id": "11",
        "client_order_id": "e",
        "quantity": "2",
        "price": "3",
        "fee": "0.01",
        "realized_pnl": "0",
    }
    raw = {
        "tradeId": "11",
        "orderId": "1",
        "symbol": "PUMPUSDT",
        "baseVolume": "2",
        "price": "3",
        "profit": "0",
        "feeDetail": [{"totalFee": "-0.01"}],
    }
    proof = {
        "sources": [
            {
                "path": "/fill-history",
                "status": "explicit-empty",
                "pages": [{"raw": {"data": {"fillList": [raw]}}}],
            }
        ]
    }
    assert (
        c.assess_entry(entry, [fill], proof)["entry_economics"]["fill-history"][0][
            "exact_durable_economics"
        ]
        is True
    )
    raw["feeDetail"][0]["totalFee"] = "-0.010000001"
    assert (
        c.assess_entry(entry, [fill], proof)["entry_economics"]["fill-history"][0][
            "exact_durable_economics"
        ]
        is False
    )
    assert raw["feeDetail"][0]["totalFee"] == "-0.010000001"


def test_analyze_only_cli_never_calls_external_system(monkeypatch, tmp_path):
    c = module()
    calls = []
    monkeypatch.setattr(c, "analyze_artifacts", lambda path: calls.append(path) or [])
    monkeypatch.setattr(c, "run_host", lambda *args: pytest.fail("external access"))
    c.main(["--analyze-only", "--output", str(tmp_path)])
    assert calls == [tmp_path]


def test_window_escape_is_preserved_not_called_source_complete():
    import asyncio

    c = module()

    async def get(path, params):
        return {
            "code": "00000",
            "data": {"fillList": [{"cTime": "300", "tradeId": "8"}], "endId": "8"},
        }

    result = asyncio.run(
        c.paginate(get, "/fill", {"startTime": "1", "endTime": "2"}, "fillList", max_pages=1)
    )
    assert result["out_of_window_rows"] == [{"cTime": "300", "tradeId": "8"}]


def test_windows_cover_scope_once_and_never_exceed_week():
    c = module()
    week = 7 * 86400000
    assert c.windows(5, 2 * week + 9) == [
        (5, week + 4),
        (week + 5, 2 * week + 4),
        (2 * week + 5, 2 * week + 9),
    ]
    with pytest.raises(ValueError):
        c.windows(9, 5)
