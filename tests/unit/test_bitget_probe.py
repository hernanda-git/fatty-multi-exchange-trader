import asyncio

from fatty_trader.exchanges.bitget.probe import run_read_only_probe


class ProbeClient:
    async def get_server_time_ms(self):
        return 1

    async def get_account(self):
        return {"available": "secret-balance"}

    async def get_contracts(self):
        return [{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}]

    async def get_all_positions(self):
        return []

    async def get_pending_orders(self):
        return {"entrustedList": [], "endId": "secret-id"}

    async def get_fills(self):
        return {"fillList": []}


def test_read_only_probe_reports_only_sanitized_shapes_and_counts() -> None:
    result = asyncio.run(run_read_only_probe(ProbeClient()))
    assert result["ok"] is True
    assert result["checks"]["contracts"] == {"status": "PASS", "shape": "list", "count": 2}
    assert result["checks"]["account"] == {"status": "PASS", "shape": "dict", "count": 1}
    rendered = str(result)
    assert "secret-balance" not in rendered
    assert "secret-id" not in rendered
