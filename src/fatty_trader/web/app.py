from collections.abc import Awaitable, Callable, Mapping
from os import environ
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import Response

from fatty_trader.web.health import build_health_report


def create_app(
    *,
    runtime_health_reader: Callable[[], Mapping[str, Mapping[str, Any]]] | None = None,
) -> FastAPI:
    console_enabled = environ.get("CONSOLE_ENABLED", "0") == "1"
    app = FastAPI(
        title="Fatty Multi-Exchange Trader",
        version="0.1.0",
        docs_url=None if console_enabled else "/docs",
        redoc_url=None if console_enabled else "/redoc",
        openapi_url=None if console_enabled else "/openapi.json",
    )

    # Trust the configured deployment origin, never a client-supplied proxy
    # header. Local HTTP remains usable without pinning localhost to HTTPS.
    secure_console = (
        console_enabled and urlsplit(environ.get("CONSOLE_ORIGIN", "")).scheme == "https"
    )

    @app.middleware("http")
    async def transport_security(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if secure_console:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

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

    if console_enabled:
        from fatty_trader.console.api import mount_console

        mount_console(app, environ)

    return app
