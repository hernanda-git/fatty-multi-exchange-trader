"""Sanitized dashboard health telemetry."""

from __future__ import annotations

from collections.abc import Mapping

_READY_STATES = {"ready", "healthy", "ok"}


def build_health_report(environ: Mapping[str, str]) -> dict[str, object]:
    """Build a bounded report from allow-listed environment values only."""
    service = environ.get("SERVICE_NAME", "web")
    mode = environ.get("TRADER_MODE", "DEMO").upper()
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
    return {
        "status": status,
        "service": service,
        "mode": mode if mode in {"DEMO", "LIVE"} else "UNKNOWN",
        "live_execution_enabled": False,
        "orders_enabled": False,
        "components": components,
    }
