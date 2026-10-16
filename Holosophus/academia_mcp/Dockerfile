FROM ghcr.io/astral-sh/uv:python3.12-bookworm

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

RUN apt-get update
RUN apt-get install -y \
    curl \
    build-essential \
    gcc \
    make \
    g++ \
    git \
    python3-dev \
    libffi-dev \
    openssl \
    perl \
    texlive \
    texlive-xetex \
    texlive-luatex \
    ghostscript \
    fontconfig \
    latexmk \
    texlive-latex-recommended \
    texlive-latex-extra \
    texlive-fonts-recommended \
    texlive-lang-cyrillic \
    cm-super \
    texlive-science

# Fail the image build if the ready-to-use Russian template cannot find its fonts.
RUN kpsewhich t2aenc.def && kpsewhich russianb.ldf && kpsewhich sfrm1000.pfb

COPY academia_mcp/pyproject.toml academia_mcp/uv.lock /app/
RUN uv sync --no-dev --no-install-project && rm -rf /root/.cache/uv

COPY academia_mcp /app
COPY holosophos/research_paper_registry.py /app/research_paper_registry.py
RUN uv sync --no-dev && uv pip install --force-reinstall 'cryptography<45' && rm -rf /root/.cache/uv

ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT []
CMD ["python", "-m", "academia_mcp"]
