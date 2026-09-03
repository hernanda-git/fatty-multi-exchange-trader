from os import environ

from fastapi import FastAPI

from fatty_trader.web.health import build_health_report


def create_app() -> FastAPI:
    app = FastAPI(title="Fatty Multi-Exchange Trader", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, object]:
        return build_health_report(environ)

    @app.get("/health/telemetry")
    async def health_telemetry() -> dict[str, object]:
        return build_health_report(environ)

    return app
