from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from fatty_trader.exchanges.bitget.client import BitgetApiError


class BitgetReadOnlyClient(Protocol):
    def get_server_time_ms(self) -> Awaitable[int]: ...
    def get_account(self) -> Awaitable[Any]: ...
    def get_contracts(self) -> Awaitable[Any]: ...
    def get_all_positions(self) -> Awaitable[Any]: ...
    def get_pending_orders(self) -> Awaitable[Any]: ...
    def get_fills(self) -> Awaitable[Any]: ...


def _shape(value: Any) -> dict[str, str | int]:
    if isinstance(value, dict):
        return {"status": "PASS", "shape": "dict", "count": len(value)}
    if isinstance(value, (list, tuple)):
        return {"status": "PASS", "shape": "list", "count": len(value)}
    return {"status": "PASS", "shape": type(value).__name__, "count": 0}


async def run_read_only_probe(client: BitgetReadOnlyClient) -> dict[str, Any]:
    """Run credentialed GET checks and return only endpoint outcome metadata."""
    checks: dict[str, dict[str, str | int]] = {}
    calls: tuple[tuple[str, Callable[[], Awaitable[Any]]], ...] = (
        ("server_time", client.get_server_time_ms),
        ("account", client.get_account),
        ("contracts", client.get_contracts),
        ("positions", client.get_all_positions),
        ("open_orders", client.get_pending_orders),
        ("fills", client.get_fills),
    )
    for name, call in calls:
        try:
            checks[name] = _shape(await call())
        except BitgetApiError as exc:
            checks[name] = {
                "status": "BLOCKED",
                "provider_code": exc.code or "UNKNOWN",
                "message": exc.provider_msg or "sanitized-client-error",
            }
        except Exception:
            checks[name] = {"status": "BLOCKED", "provider_code": "TRANSPORT"}
    return {"ok": all(check["status"] == "PASS" for check in checks.values()), "checks": checks}
