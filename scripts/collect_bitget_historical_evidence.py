"""Evidence only. Run host CLI; signed GETs execute only in monitor-bitget."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx


class RawReader:
    def __init__(self, client, emit, delay=0.2):
        self.client = client
        self.emit = emit
        self.delay = delay

    async def get(self, path, params):
        from fatty_trader.exchanges.bitget.client import build_signature, canonical_query_string

        query = canonical_query_string(params)
        stamp = str(int(time.time() * 1000))
        signature = build_signature(self.client._api_secret, stamp, "GET", path, query, "")
        headers = self.client._signed_headers(stamp, signature, include_demo_header=False)
        await asyncio.sleep(self.delay)
        response = await self.client._client.request(
            "GET", path + ("?" + query if query else ""), headers=headers
        )
        raw = response.json()
        self.emit(
            {
                "kind": "response",
                "at": datetime.now(UTC).isoformat(),
                "path": path,
                "params": params,
                "http_status": response.status_code,
                "raw": raw,
            }
        )
        return raw


class GetOnlyTransport(httpx.AsyncBaseTransport):
    """Method fence at the final network boundary, independent of client APIs."""

    def __init__(self, inner=None):
        self.inner = inner if inner is not None else httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request):
        if request.method != "GET":
            raise ValueError("GET-only evidence transport")
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()


def windows(start: int, end: int) -> list[tuple[int, int]]:
    if start > end:
        raise ValueError("invalid scope")
    result = []
    while start <= end:
        stop = min(end, start + 7 * 86400000 - 1)
        result.append((start, stop))
        start = stop + 1
    return result


async def paginate(get, path, params, list_key, *, max_pages=100):
    pages = []
    out_of_window = []
    cursor = None
    seen = set()
    status = "page-cap"
    for _ in range(max_pages):
        query = {**params, "limit": "100"}
        if cursor is not None:
            query["idLessThan"] = cursor
        raw = await get(path, query)
        pages.append({"params": query, "raw": raw})
        if not isinstance(raw, dict) or raw.get("code") != "00000":
            status = "provider-error"
            break
        data = raw.get("data")
        if data is None:
            status = "successful-null-unproven"
            break
        rows = data.get(list_key) if isinstance(data, dict) else None
        if (
            isinstance(data, dict)
            and list_key in data
            and rows is None
            and data.get("endId") is None
        ):
            status = "successful-null-list-unproven"
            break
        if not isinstance(rows, list):
            status = "malformed-list"
            break
        if not rows:
            status = "explicit-empty"
            break
        if "startTime" in params and "endTime" in params:
            for row in rows:
                stamp = row.get("cTime", row.get("ctime")) if isinstance(row, dict) else None
                if (
                    isinstance(stamp, str)
                    and stamp.isdigit()
                    and not int(params["startTime"]) <= int(stamp) <= int(params["endTime"])
                ):
                    out_of_window.append(row)
        cursor = data.get("endId")
        if not isinstance(cursor, str) or not cursor:
            status = "missing-cursor"
            break
        if cursor in seen:
            status = "repeated-cursor"
            break
        seen.add(cursor)
    return {
        "path": path,
        "scope": params,
        "status": status,
        "pages": pages,
        "out_of_window_rows": out_of_window,
    }


async def collect_symbol(get, symbol, start, end):
    results = []
    sources = [
        ("/api/v2/mix/order/fill-history", "fillList", {}),
        ("/api/v2/mix/order/fills", "fillList", {}),
        ("/api/v2/mix/order/orders-history", "entrustedList", {}),
        ("/api/v2/mix/position/history-position", "list", {}),
    ] + [
        ("/api/v2/mix/order/orders-plan-history", "entrustedList", {"planType": kind})
        for kind in ("normal_plan", "track_plan", "profit_loss")
    ]
    for left, right in windows(start, end):
        for path, key, extra in sources:
            params = {
                "productType": "USDT-FUTURES",
                "symbol": symbol,
                "startTime": str(left),
                "endTime": str(right),
                **extra,
            }
            results.append(await paginate(get, path, params, key))
    return results


async def collect_archived_plans(get, entries, start, end):
    symbol = entries[0]["symbol"]
    base = {
        "productType": "USDT-FUTURES",
        "symbol": symbol,
        "startTime": str(start),
        "endTime": str(end),
    }
    sources = []
    for kind in ("normal_plan", "track_plan", "profit_loss"):
        sources.append(
            await paginate(
                get,
                "/api/v2/mix/order/orders-plan-history",
                {**base, "planType": kind},
                "entrustedList",
            )
        )
    for entry in entries:
        for suffix in ("-sl", "-tp"):
            sources.append(
                await paginate(
                    get,
                    "/api/v2/mix/order/orders-plan-history",
                    {
                        **base,
                        "planType": "profit_loss",
                        "clientOid": entry["client_order_id"] + suffix,
                    },
                    "entrustedList",
                )
            )
    execute_ids = sorted(
        {
            row["executeOrderId"]
            for source in sources
            for page in source["pages"]
            for row in ((page["raw"].get("data") or {}).get("entrustedList") or [])
            if row.get("executeOrderId")
        }
    )
    details = []
    fills = []
    for order_id in execute_ids:
        query = {"productType": "USDT-FUTURES", "symbol": symbol, "orderId": order_id}
        details.append({"orderId": order_id, "raw": await get("/api/v2/mix/order/detail", query)})
        for left, right in windows(start, end):
            fills.append(
                await paginate(
                    get,
                    "/api/v2/mix/order/fill-history",
                    {**query, "startTime": str(left), "endTime": str(right)},
                    "fillList",
                )
            )
    return {
        "sources": sources,
        "execute_order_ids": execute_ids,
        "details": details,
        "fills": fills,
    }


SQL = """BEGIN READ ONLY;
SELECT json_build_object(
 'collected_at',clock_timestamp(),
 'entries',(SELECT json_agg(i ORDER BY i.created_at) FROM live_order_intents i
 LEFT JOIN bitget_margin_reservations r ON r.id=i.margin_reservation_id
 WHERE i.exchange='bitget' AND i.role='ENTRY' AND i.state='filled'
 AND (r.id IS NULL OR (r.state='consumed' AND r.environment IS NULL))),
 'intents',(SELECT json_agg(i) FROM live_order_intents i WHERE exchange='bitget'),
 'fills',(SELECT json_agg(f) FROM fills f WHERE exchange='bitget'),
 'reservations',(SELECT json_agg(r) FROM bitget_margin_reservations r),
 'legacy_bindings',(SELECT json_agg(b) FROM admission_legacy_bindings b),
 'close_bindings',(SELECT json_agg(b) FROM bitget_verified_close_bindings b),
 'fallbacks',(SELECT json_agg(f) FROM fallback_protection f WHERE exchange='bitget'));
COMMIT;
"""


def private_json(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(value, file, indent=2, default=str)


async def container_collect(entries, start, end):
    from fatty_trader.exchanges.bitget.client import BitgetRestClient

    def emit(record):
        print(json.dumps(record), flush=True)

    client = BitgetRestClient(
        os.environ["BITGET_API_KEY"],
        os.environ["BITGET_API_SECRET"],
        os.environ["BITGET_API_PASSPHRASE"],
        mode="LIVE",
        transport=GetOnlyTransport(),
    )
    reader = RawReader(client, emit)
    try:
        account = await reader.get("/api/v2/spot/account/info", {})
        if (
            account.get("code") != "00000"
            or not isinstance(account.get("data"), dict)
            or not account["data"].get("userId")
        ):
            raise ValueError("account provenance unavailable")
        symbol = entries[0]["symbol"]
        sources = await collect_symbol(reader.get, symbol, start, end)
        archived = await collect_archived_plans(reader.get, entries, start, end)
        details = []
        for entry in entries:
            query = {
                "productType": "USDT-FUTURES",
                "symbol": symbol,
                "orderId": entry["provider_order_id"],
            }
            details.append(
                {
                    "entry": entry["client_order_id"],
                    "raw": await reader.get("/api/v2/mix/order/detail", query),
                }
            )
        positions = await reader.get(
            "/api/v2/mix/position/all-position",
            {"productType": "USDT-FUTURES", "marginCoin": "USDT"},
        )
        emit(
            {
                "kind": "symbol-result",
                "symbol": symbol,
                "account": account,
                "environment": "LIVE",
                "start_ms": start,
                "end_ms": end,
                "sources": sources,
                "details": details,
                "archived": archived,
                "positions": positions,
                "collected_at": datetime.now(UTC).isoformat(),
            }
        )
    except Exception as exc:
        emit({"kind": "collection-error", "exception_class": type(exc).__name__})
    finally:
        await client.aclose()


def run_host(project, output, expected_entries=17):
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    prefix = ["docker", "compose", "--project-directory", str(project)]
    db = subprocess.run(
        prefix
        + [
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "fatty_app",
            "-d",
            "fatty_trader",
            "-Atq",
            "-v",
            "ON_ERROR_STOP=1",
        ],
        input=SQL,
        text=True,
        capture_output=True,
        timeout=60,
    )
    if db.returncode:
        raise RuntimeError("read-only DB snapshot failed")
    database = json.loads(db.stdout, parse_float=Decimal)
    private_json(output / "database.json", database)
    entries = database.get("entries") or []
    if len(entries) != expected_entries:
        raise ValueError("unexpected unresolved ENTRY count")
    source = Path(__file__).read_text().split('\nif __name__ == "__main__":')[0]
    end = int(datetime.now(UTC).timestamp() * 1000)
    for symbol in sorted({e["symbol"] for e in entries}):
        owners = [e for e in entries if e["symbol"] == symbol]
        start = (
            min(int(datetime.fromisoformat(e["created_at"]).timestamp() * 1000) for e in owners)
            - 7 * 86400000
        )
        probe = (
            source
            + "\nasyncio.run(container_collect("
            + repr(owners)
            + ","
            + str(start)
            + ","
            + str(end)
            + "))\n"
        )
        result = subprocess.run(
            prefix + ["exec", "-T", "monitor-bitget", "/app/.venv/bin/python", "-"],
            input=probe,
            text=True,
            capture_output=True,
            timeout=300,
        )
        records = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
        private_json(
            output / (symbol + "-responses.json"),
            {"returncode": result.returncode, "records": records},
        )
        final = [r for r in records if r.get("kind") == "symbol-result"]
        if final:
            private_json(output / (symbol + ".json"), final[-1])
        print(
            json.dumps({"symbol": symbol, "records": len(records), "collected": bool(final)}),
            flush=True,
        )
    return database


def source_rows(proof, endpoint):
    rows = {}
    sources = proof.get("sources", []) + proof.get("archived", {}).get("sources", [])
    for source in sources:
        if source["path"].rsplit("/", 1)[-1] != endpoint:
            continue
        for page in source["pages"]:
            data = page["raw"].get("data")
            if not isinstance(data, dict):
                continue
            for value in data.values():
                if isinstance(value, list):
                    for row in value:
                        if isinstance(row, dict):
                            rows[json.dumps(row, sort_keys=True)] = row
    return list(rows.values())


def assess_entry(entry, durable_fills, proof):
    oid = entry["client_order_id"]
    order_id = entry["provider_order_id"]
    durable = {f["provider_fill_id"]: f for f in durable_fills if f["client_order_id"] == oid}
    orders = source_rows(proof, "orders-history")
    entry_orders = [r for r in orders if r.get("orderId") == order_id]
    economics = {}
    identity = False
    for endpoint in ("fill-history", "fills"):
        fills = [
            f
            for f in source_rows(proof, endpoint)
            if f.get("orderId") == order_id and f.get("symbol") == entry["symbol"]
        ]
        receipts = []
        for f in fills:
            d = durable.get(f.get("tradeId"))
            match = False
            try:
                fees = f["feeDetail"]
                fee = sum(Decimal(v["totalFee"]) for v in fees)
                match = bool(
                    d
                    and Decimal(f["price"]) == Decimal(str(d["price"]))
                    and Decimal(f["baseVolume"]) == Decimal(str(d["quantity"]))
                    and abs(fee) == abs(Decimal(str(d["fee"])))
                    and Decimal(f["profit"]) == Decimal(str(d["realized_pnl"]))
                )
            except (KeyError, TypeError, ValueError, ArithmeticError):
                pass
            receipts.append({"raw": f, "durable": d, "exact_durable_economics": match})
        economics[endpoint] = receipts
        if endpoint == "fill-history":
            identity = bool(
                durable
                and {f.get("tradeId") for f in fills} == set(durable)
                and any(r.get("clientOid") == oid for r in entry_orders)
            )
            try:
                identity = identity and sum(Decimal(f["baseVolume"]) for f in fills) == Decimal(
                    str(entry["filled_qty"])
                )
            except (KeyError, TypeError, ValueError, ArithmeticError):
                identity = False
    provider_receipt = bool(
        economics["fill-history"] and any(r.get("clientOid") == oid for r in entry_orders)
    )
    try:
        provider_receipt = provider_receipt and sum(
            Decimal(r["raw"]["baseVolume"]) for r in economics["fill-history"]
        ) == Decimal(str(entry["filled_qty"]))
    except (KeyError, TypeError, ValueError, ArithmeticError):
        provider_receipt = False
    plans = [
        r
        for r in source_rows(proof, "orders-plan-history")
        if r.get("clientOid") in (oid + "-sl", oid + "-tp")
    ]
    chains = []
    for plan in plans:
        execute = plan.get("executeOrderId")
        chains.append(
            {
                "plan": plan,
                "executeOrderId": execute,
                "orders": [o for o in orders if execute and o.get("orderId") == execute],
                "fills": [
                    f
                    for f in source_rows(proof, "fill-history")
                    if execute and f.get("orderId") == execute
                ],
                "epoch_ownership_proven": False,
            }
        )
    emergency = [o for o in orders if o.get("clientOid") == oid + "-emergency"]
    unbound_orders = [o for o in orders if o.get("reduceOnly") == "YES"]
    blockers = [
        "entry-to-position-epoch-binding-missing",
        "close-to-position-epoch-binding-missing",
    ]
    if any(
        s["status"] != "explicit-empty"
        for s in proof.get("sources", []) + proof.get("archived", {}).get("sources", [])
    ):
        blockers.append("historical-terminal-contract-unproven")
    if not identity:
        blockers.append("exact-entry-provider-identity-unproven")
    if not plans and not emergency:
        blockers.append("exact-entry-to-close-client-or-plan-binding-missing")
    return {
        "entry": entry,
        "authenticated_entry_receipt": provider_receipt,
        "exact_entry_order_and_fill_identity": identity,
        "entry_orders": entry_orders,
        "entry_economics": economics,
        "exact_named_plan_chains": chains,
        "exact_named_emergency_close_orders": emergency,
        "unbound_symbol_close_orders": unbound_orders,
        "unbound_symbol_position_history": source_rows(proof, "history-position"),
        "full_epoch_ownership_provable": False,
        "verdict": "UNPROVEN",
        "blockers": blockers,
    }


def analyze_artifacts(output):
    database = json.loads((output / "database.json").read_text(), parse_float=Decimal)
    entries = database["entries"]
    if len(entries) != 17 or len({e["client_order_id"] for e in entries}) != 17:
        raise ValueError("expected seventeen unique ENTRY owners")
    verdicts = []
    for entry in entries:
        proof = json.loads((output / (entry["symbol"] + ".json")).read_text())
        verdict = assess_entry(entry, database["fills"] or [], proof)
        verdict["artifact"] = str(output / (entry["symbol"] + ".json"))
        verdicts.append(verdict)
    # Original DB numerics were parsed losslessly; serialize as strings, not float.
    fd = os.open(output / "per-entry-verdicts.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(
            {"entry_count": len(verdicts), "proven_count": 0, "entries": verdicts},
            stream,
            default=str,
            indent=2,
        )
    return verdicts


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--analyze-only", action="store_true")
    args = parser.parse_args(argv)
    if not args.analyze_only:
        if args.project is None:
            parser.error("--project required for authenticated collection")
        run_host(args.project, args.output)
    verdicts = analyze_artifacts(args.output)
    print(json.dumps({"entry_count": len(verdicts), "proven_count": 0, "verdict": "UNPROVEN"}))


if __name__ == "__main__":
    main()
