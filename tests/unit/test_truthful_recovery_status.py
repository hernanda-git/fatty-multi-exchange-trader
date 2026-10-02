from types import SimpleNamespace

import httpx
import psycopg

from fatty_trader.operator.command_parser import CloseCommand
from fatty_trader.operator.live_commands import OperatorCommandService
from fatty_trader.recovery_webhook import create_recovery_app


def test_close_all_preserves_unresolved_state():
    service = object.__new__(OperatorCommandService)
    service._require_confirmation = False
    service._gw = SimpleNamespace(close_all=lambda: {"count": 2, "state": "reconciliation-pending"})
    message = service._on_close(CloseCommand(target="all"))
    assert "reconciliation-pending" in message


async def test_inventory_is_not_claimed_as_reconciliation(monkeypatch):
    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, query):
            assert query.lstrip().startswith("SELECT")

        def fetchall(self):
            return [
                (
                    "bitget",
                    "stable-oid",
                    "BTCUSDT",
                    "LONG",
                    "entry",
                    "unknown",
                    1,
                    0,
                    None,
                    None,
                    None,
                    [],
                )
            ]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def cursor(self):
            return Cursor()

    monkeypatch.setattr(psycopg, "connect", lambda _: Connection())
    app = create_recovery_app(api_key="testonly", db_dsn="fake-test")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/recover/intents", headers={"Authorization": "Bearer testonly"}
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["reconciled"] == []
    assert payload["read_only"] is True
    assert payload["candidates"] == ["stable-oid"]
