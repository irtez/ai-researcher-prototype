FROM ghcr.io/astral-sh/uv:python3.11-alpine

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

COPY . /app
RUN uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT []
CMD ["python", "-m", "chem_conversions_mcp"]
