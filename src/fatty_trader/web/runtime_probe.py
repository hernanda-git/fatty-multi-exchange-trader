"""Read-only web runtime probes; configuration never supplies progress.

Worker files are read-only metadata. For separate containers, owner identity is
attested inside the worker over a dedicated Unix socket, never via host /proc.
"""

from __future__ import annotations

import json
import math
import socket
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import psycopg

from fatty_trader.worker_health import _process_identity, health_path

_WORKERS = {
    "intake": "intake",
    "analyzer": "analyzer",
    "notification": "notification-sender",
}


def _owner_is_alive(evidence: dict[str, Any], endpoint: str | None) -> bool:
    if endpoint is None:
        return bool(evidence["identity"] == _process_identity(int(evidence["pid"])))
    # No fallback to a namespace-local PID on socket failure: PID 1 in web
    # must never validate a dead sibling's PID 1. Token binds restarts too.
    challenge = uuid.uuid4().hex
    request = {key: evidence[key] for key in ("service", "pid", "identity", "owner_token")}
    request["challenge"] = challenge
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.2)
        connection.connect(endpoint)
        connection.sendall(json.dumps(request).encode() + b"\n")
        with connection.makefile("rb") as stream:
            response = json.loads(stream.readline(4096))
    return response["valid"] is True and response["challenge"] == challenge


def create_runtime_health_reader(
    environ: Mapping[str, str],
    *,
    connection_factory: Callable[..., Any] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Callable[[], dict[str, dict[str, Any]]]:
    """Probe on demand, without startup I/O, worker launches or cached readiness."""

    def read() -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {
            name: {"state": "failed", "age_seconds": None} for name in ("postgres", *_WORKERS)
        }
        try:
            started = float(clock())
            max_age = float(environ.get("WORKER_HEALTH_MAX_AGE_SECONDS", "180"))
            if (
                not math.isfinite(started)
                or started < 0
                or not math.isfinite(max_age)
                or max_age <= 0
            ):
                return result
        except (ValueError, TypeError, OverflowError):
            return result
        try:
            # libpq uses the real PG* environment, exactly as the service does.
            # Set read-only at connection establishment, before any SQL executes.
            with (
                (connection_factory or psycopg.connect)(
                    connect_timeout=2,
                    options="-c default_transaction_read_only=on -c statement_timeout=2000",
                ) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute("SELECT 1")
                if cursor.fetchone() == (1,):
                    result["postgres"] = {"state": "ready", "age_seconds": 0.0}
        except Exception:
            pass  # Never serialize connection strings or exception details.
        try:
            now = float(clock())
            if not math.isfinite(now) or now < started:
                return {name: {"state": "failed", "age_seconds": None} for name in result}
        except (ValueError, TypeError, OverflowError):
            return {name: {"state": "failed", "age_seconds": None} for name in result}
        if result["postgres"]["state"] == "ready":
            elapsed = now - started
            result["postgres"] = {
                "state": "ready" if elapsed <= 120 else "failed",
                "age_seconds": elapsed,
            }
        for component, service in _WORKERS.items():
            path = Path(
                environ.get(f"WEB_{component.upper()}_HEALTH_PATH", str(health_path(service, {})))
            )
            try:
                evidence = json.loads(path.read_text())
                owner_alive = _owner_is_alive(
                    evidence, environ.get(f"WEB_{component.upper()}_HEALTH_SOCKET_PATH")
                )
                checked_at = float(clock())
                age = checked_at - float(evidence["progress"])
                valid = (
                    evidence["service"] == service
                    and evidence["state"] == "ready"
                    and owner_alive
                    and math.isfinite(checked_at)
                    and checked_at >= now
                    and math.isfinite(age)
                    and 0 <= age <= min(max_age, 120)
                )
                result[component] = {
                    "state": "ready" if valid else "failed",
                    "age_seconds": age if math.isfinite(age) and age >= 0 else None,
                }
            except (OSError, ValueError, KeyError, TypeError, IndexError, OverflowError):
                pass
        return result

    return read
