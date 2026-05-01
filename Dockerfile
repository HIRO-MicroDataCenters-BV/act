# syntax=docker/dockerfile:1
FROM python:3.11-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY cape-sdks/python/ ./cape-sdks/python/

ARG TARGETARCH
RUN if [ "$TARGETARCH" = "arm64" ]; then \
        apt-get update && apt-get install -y --no-install-recommends clang && rm -rf /var/lib/apt/lists/*; \
    fi

RUN uv sync --frozen --no-dev --no-install-project --compile-bytecode --extra fuzzing

COPY act/ ./act/
RUN .venv/bin/python -m compileall -q act/

FROM python:3.11-slim AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/act /app/act

WORKDIR /app

ENTRYPOINT ["python", "-m", "act.run"]
