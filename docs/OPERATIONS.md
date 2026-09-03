# Operations status

## Local development

```bash
uv sync --all-groups
uv run pytest -q
uv run ruff format --check .
uv run ruff check .
uv run mypy src
```

## Deployment boundary

The committed Compose file is PAPER-only and binds the web service to loopback. It contains no named volumes; PostgreSQL persists below `./data/postgres`. `WEB_HOST_PORT` defaults to `18081`, avoiding the workstation's occupied port `8080`.

Deployment to `production-host`, Telegram listener authorization, Codex OAuth setup, exchange metadata probes, credentials, and all exchange/PAPER execution are deliberately not run from this workstation. A live order is explicitly out of scope until the independent go-live gates in the implementation plan are satisfied and a human operator approves a venue.
