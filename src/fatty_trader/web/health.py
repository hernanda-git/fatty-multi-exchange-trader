"""Sanitized dashboard health telemetry."""

from __future__ import annotations

from collections.abc import Mapping

_READY_STATES = {"ready", "healthy", "ok"}


def build_health_report(environ: Mapping[str, str]) -> dict[str, object]:
    """Build a bounded report from allow-listed environment values only."""
    service = environ.get("SERVICE_NAME", "web")
    mode = environ.get("TRADER_MODE", "DEMO").upper()
    venue_mode = mode
    if service in {"dispatcher-bitget", "monitor-bitget", "operator-bot"}:
        venue_mode = environ.get("BITGET_MODE", mode).upper()
    components: dict[str, str] = {}
    raw_components = environ.get("SERVICE_COMPONENTS", "")
    for item in raw_components.split(","):
        if "=" not in item:
            continue
        name, state = (part.strip() for part in item.split("=", 1))
        if name and state and len(name) <= 64 and len(state) <= 32:
            components[name] = state.lower()
    all_ready = all(state in _READY_STATES for state in components.values())
    status = "ok" if mode in {"DEMO", "LIVE"} and all_ready else "degraded"
    execution_enabled = (
        service in {"dispatcher-bitget", "monitor-bitget"}
        and environ.get("BITGET_EXECUTION_ENABLED", "0") == "1"
    )
    return {
        "status": status,
        "service": service,
        "mode": mode,
        "venue_mode": venue_mode,
        "live_execution_enabled": execution_enabled,
        "orders_enabled": execution_enabled,
        "components": components,
    }
