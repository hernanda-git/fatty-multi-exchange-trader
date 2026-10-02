from collections.abc import Callable, Mapping
from os import environ
from typing import Any

from fastapi import FastAPI

from fatty_trader.web.health import build_health_report


def create_app(
    *,
    runtime_health_reader: Callable[[], Mapping[str, Mapping[str, Any]]] | None = None,
) -> FastAPI:
    app = FastAPI(title="Fatty Multi-Exchange Trader", version="0.1.0")

    def report() -> dict[str, object]:
        runtime = None
        if runtime_health_reader is not None:
            try:
                candidate = runtime_health_reader()
                if isinstance(candidate, Mapping):
                    runtime = candidate
            except Exception:
                # Do not leak provider/DB exception messages into telemetry.
                pass
        return build_health_report(environ, runtime_components=runtime)

    @app.get("/health")
    def health() -> dict[str, object]:
        return report()

    @app.get("/health/telemetry")
    def health_telemetry() -> dict[str, object]:
        return report()

    return app
