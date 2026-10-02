import httpx
import pytest

from fatty_trader.web.app import create_app
from fatty_trader.web.health import build_health_report

ENV = {"TRADER_MODE": "DEMO", "SERVICE_COMPONENTS": "postgres=ready,migrate=ready"}


def evidence():
    return {
        name: {"state": "ready", "age_seconds": 1}
        for name in ["postgres", "intake", "analyzer", "notification"]
    }


def test_static_configuration_never_proves_runtime_readiness():
    static = build_health_report(ENV)
    assert static["configuration"]["status"] == "ok"
    assert static["liveness"]["status"] == "alive"
    assert static["readiness"]["status"] == "unknown"
    assert static["status"] != "ok"
    empty = build_health_report({"TRADER_MODE": "DEMO"})
    assert empty["configuration"]["status"] != "ok"
    assert empty["status"] != "ok"


@pytest.mark.parametrize(
    "broken", ["empty", "stopped", "stalled", "missing_intake", "missing_notification", "db_failed"]
)
def test_runtime_readiness_requires_fresh_complete_components(broken):
    runtime = evidence()
    assert build_health_report(ENV, runtime_components=runtime)["readiness"]["status"] == "ready"
    if broken == "empty":
        runtime.clear()
    elif broken == "stopped":
        runtime["analyzer"]["state"] = "stopped"
    elif broken == "stalled":
        runtime["analyzer"]["age_seconds"] = 121
    elif broken == "missing_intake":
        del runtime["intake"]
    elif broken == "missing_notification":
        del runtime["notification"]
    elif broken == "db_failed":
        runtime["postgres"]["state"] = "failed"
    report = build_health_report(ENV, runtime_components=runtime)
    assert report["readiness"]["status"] != "ready"
    assert report["status"] != "ok"


@pytest.mark.parametrize("age", [None, -1, float("nan"), float("inf"), "bad"])
def test_invalid_freshness_is_not_ready(age):
    runtime = evidence()
    runtime["analyzer"]["age_seconds"] = age
    assert build_health_report(ENV, runtime_components=runtime)["readiness"]["status"] != "ready"


async def test_routes_use_runtime_reader_not_environment(monkeypatch):
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    runtime = evidence()
    calls = []

    def read():
        calls.append(True)
        return runtime

    transport = httpx.ASGITransport(app=create_app(runtime_health_reader=read))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/health")).json()["readiness"]["status"] == "ready"
        runtime["analyzer"]["state"] = "stopped"
        assert (await client.get("/health/telemetry")).json()["readiness"]["status"] == "degraded"
    assert len(calls) == 2


async def test_failed_runtime_reader_is_unknown(monkeypatch):
    def read():
        raise TimeoutError("do not disclose this detail")

    transport = httpx.ASGITransport(app=create_app(runtime_health_reader=read))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["readiness"]["status"] == "unknown"
    assert "do not disclose" not in response.text
