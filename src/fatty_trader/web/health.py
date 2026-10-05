"""Sanitized dashboard health telemetry."""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from typing import Any

_READY_STATES = {"ready", "healthy", "ok"}


def build_health_report(
    environ: Mapping[str, str],
    *,
    runtime_components: Mapping[str, Mapping[str, Any]] | None = None,
    required_components: tuple[str, ...] = ("postgres", "intake", "analyzer", "notification"),
    max_age_seconds: float = 120,
) -> dict[str, object]:
    """Separate static configuration, HTTP liveness, and fresh runtime evidence.

    Runtime ages must come from read-only progress/dependency probes, not env
    flags. Missing required evidence is never readiness.
    """
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
    config_ok = (
        mode in {"DEMO", "LIVE"}
        and venue_mode in {"DEMO", "LIVE"}
        and bool(components)
        and all(state in _READY_STATES for state in components.values())
    )
    runtime: dict[str, str] = {}
    for name in required_components:
        runtime_item = (runtime_components or {}).get(name, {})
        if not isinstance(runtime_item, Mapping) or not runtime_item:
            runtime[name] = "unknown"
            continue
        try:
            age_value: Any = runtime_item.get("age_seconds")
            age = float(age_value)
            fresh = isfinite(age) and 0 <= age <= max_age_seconds
        except (TypeError, ValueError, OverflowError):
            fresh = False
        runtime[name] = (
            "ready" if runtime_item.get("state") in _READY_STATES and fresh else "degraded"
        )
    readiness = (
        "unknown"
        if not runtime_components or not runtime
        else "ready"
        if all(state == "ready" for state in runtime.values())
        else "degraded"
    )
    status = "ok" if config_ok and readiness == "ready" else "degraded"
    execution_enabled = (
        service in {"dispatcher-bitget", "monitor-bitget", "web"}
        and mode == "LIVE"
        and environ.get("BITGET_MODE", mode).upper() == "LIVE"
        and environ.get("BITGET_EXECUTION_ENABLED", "0") == "1"
    )
    return {
        "status": status,
        "service": service,
        "mode": mode,
        "venue_mode": venue_mode,
        "live_execution_enabled": execution_enabled,
        # Web observes configuration, not dispatcher permission or kill switches.
        "orders_enabled": None if service == "web" else execution_enabled,
        "components": components,
        "configuration": {
            "execution_enabled": execution_enabled,
            "status": "ok" if config_ok else "degraded",
            "components": components,
            "evidence": "static environment only",
        },
        "liveness": {"status": "alive", "evidence": "HTTP handler responding"},
        "readiness": {
            "status": readiness,
            "components": runtime,
            "evidence": "runtime progress/dependency probes"
            if runtime_components
            else "not supplied",
        },
    }
