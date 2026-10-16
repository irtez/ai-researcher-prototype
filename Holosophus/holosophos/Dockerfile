FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app/holosophos

# Install build dependencies if needed (e.g., for certain python packages)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first to leverage Docker cache
# Use --mount=type=cache to speed up uv sync
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=holosophos/uv.lock,target=/app/holosophos/uv.lock \
    --mount=type=bind,source=holosophos/pyproject.toml,target=/app/holosophos/pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# Copy application and local dependencies from monorepo
COPY holosophos /app/holosophos
COPY codearkt /app/codearkt

# Install the project itself
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev \
    && . /app/holosophos/.venv/bin/activate \
    && pip uninstall -y codearkt \
    && pip install --no-deps -e /app/codearkt

# Use the virtual environment's python/binaries
ENV PATH="/app/holosophos/.venv/bin:$PATH" \
    PYTHONPATH="/app/codearkt:${PYTHONPATH}"

EXPOSE 5055

ENV PHOENIX_URL=http://phoenix:6006 \
    PHOENIX_PROJECT_NAME=holosophos \
    ACADEMIA_MCP_URL=http://academia:5056/mcp \
    CODEARKT_EXECUTOR_URL=http://executor:8000

CMD ["python", "-m", "holosophos.server", "--port", "5055"]
