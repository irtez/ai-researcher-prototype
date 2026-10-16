FROM ghcr.io/astral-sh/uv:python3.12-bookworm

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libgomp1 \
        libxrender1 \
        libxext6 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock /app/
RUN uv sync --no-dev --no-install-project && rm -rf /root/.cache/uv

COPY . /app
RUN uv sync --no-dev && rm -rf /root/.cache/uv

ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT []
CMD ["python", "serve_http.py"]
