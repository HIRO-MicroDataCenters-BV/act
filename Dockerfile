# syntax=docker/dockerfile:1
FROM python:3.11-slim AS builder
RUN pip install --no-cache-dir uv==0.11.6

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY pyproject.toml uv.lock ./

ARG TARGETARCH

# arm64 requires clang with compiler-rt (SanitizerCoverage) to build atheris from source.
# Install LLVM 17 from the official apt repository which provides pre-built compiler-rt.
RUN if [ "$TARGETARCH" = "arm64" ]; then \
        apt-get update && \
        apt-get install -y --no-install-recommends wget gnupg lsb-release && \
        wget -qO /tmp/llvm.sh https://apt.llvm.org/llvm.sh && \
        chmod +x /tmp/llvm.sh && \
        /tmp/llvm.sh 18 && \
        apt-get install -y --no-install-recommends clang-18 libclang-rt-18-dev && \
        rm -rf /var/lib/apt/lists/* /tmp/llvm.sh; \
    fi

RUN if [ "$TARGETARCH" = "arm64" ]; then \
        CC=clang-18 CXX=clang++-18 CLANG_BIN=/usr/bin/clang-18 \
        uv sync --frozen --no-dev --no-install-project --compile-bytecode --extra fuzzing; \
    else \
        uv sync --frozen --no-dev --no-install-project --compile-bytecode --extra fuzzing; \
    fi

COPY act/ ./act/
RUN .venv/bin/python -m compileall -q act/

FROM python:3.11-slim AS runtime
COPY --from=builder /usr/local/bin/uv /usr/local/bin/uv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/act /app/act
COPY --from=builder /app/pyproject.toml /app/pyproject.toml
COPY --from=builder /app/uv.lock /app/uv.lock

WORKDIR /app

ENTRYPOINT ["python", "-m", "act.run"]
