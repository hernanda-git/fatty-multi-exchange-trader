FROM python:3.12-slim

ARG SOURCE_REVISION=unknown
LABEL org.opencontainers.image.revision=$SOURCE_REVISION \
      org.opencontainers.image.source="https://github.com/hernanda-git/fatty-multi-exchange-trader"

WORKDIR /app
RUN useradd --create-home --uid 10001 fatty
RUN apt-get update \
    && apt-get install -y --no-install-recommends nodejs npm \
    && npm install --global @openai/codex@0.153.0 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.12.8 /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY scripts ./scripts
RUN uv sync --frozen --no-dev
RUN chown -R fatty:fatty /app
USER fatty
EXPOSE 8080
CMD ["/app/.venv/bin/python", "-m", "uvicorn", "fatty_trader.main:app", "--host", "0.0.0.0", "--port", "8080"]
