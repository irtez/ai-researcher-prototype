FROM python:3.12-slim

ARG PAPERBANANA_GIT_URL=https://github.com/llmsresearch/paperbanana.git
ARG PAPERBANANA_GIT_REF=v0.1.2

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt

RUN git clone "${PAPERBANANA_GIT_URL}" paperbanana \
    && cd paperbanana \
    && git checkout "${PAPERBANANA_GIT_REF}"

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir "/opt/paperbanana[mcp]"

# Create output directory
RUN mkdir -p /workdir/figures/paperbanana

EXPOSE 5060

# Run as streamable HTTP MCP server on /mcp
CMD ["fastmcp", "run", "-t", "streamable-http", "--host", "0.0.0.0", "--port", "5060", "--path", "/mcp", "/opt/paperbanana/mcp_server/server.py"]
