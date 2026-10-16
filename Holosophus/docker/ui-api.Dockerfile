FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app/holosophos

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    fontconfig \
    fonts-dejavu-core \
    fonts-liberation2 \
    fonts-noto-core \
    git \
    libreoffice-core \
    libreoffice-impress \
    libreoffice-writer \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=holosophos/uv.lock,target=/app/holosophos/uv.lock \
    --mount=type=bind,source=holosophos/pyproject.toml,target=/app/holosophos/pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

COPY holosophos /app/holosophos
COPY codearkt/codearkt/auth.py /tmp/codearkt-auth.py

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# codearkt 1.8.20 does not yet ship auth.py. Install the exact module used by
# the runner so both services verify the same signed bearer-token protocol.
RUN /app/holosophos/.venv/bin/python -c "import pathlib, shutil, codearkt; shutil.copyfile('/tmp/codearkt-auth.py', pathlib.Path(next(iter(codearkt.__path__))) / 'auth.py')"

ENV PATH="/app/holosophos/.venv/bin:$PATH" \
    WORKSPACE_DIR=/workdir

EXPOSE 5058

HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5058/ui/health', timeout=2).read()"]

CMD ["python", "-m", "holosophos.ui_api", "--host", "0.0.0.0", "--port", "5058"]
