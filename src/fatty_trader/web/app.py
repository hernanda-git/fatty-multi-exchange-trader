from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Fatty Multi-Exchange Trader", version="0.1.0")

    @app.get("/health")
    async def health() -> dict[str, bool | str]:
        return {
            "status": "ok",
            "mode": "PAPER",
            "live_execution_enabled": False,
            "orders_enabled": False,
        }

    return app
