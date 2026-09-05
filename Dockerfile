FROM python:3.12-slim

WORKDIR /app
RUN useradd --create-home --uid 10001 fatty
COPY --from=ghcr.io/astral-sh/uv:0.12.8 /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY scripts ./scripts
RUN uv sync --frozen --no-dev
RUN chown -R fatty:fatty /app
USER fatty
EXPOSE 8080
CMD ["/app/.venv/bin/python", "-m", "uvicorn", "fatty_trader.main:app", "--host", "0.0.0.0", "--port", "8080"]
