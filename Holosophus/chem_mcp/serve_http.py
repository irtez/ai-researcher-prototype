"""HTTP entry point for the chem-tools MCP server.

The upstream ``mcp_server.main`` runs the server over stdio. For the
Holosophos docker stack we need a streamable-http transport so the
orchestrator can reach the server over the container network.

This file does not alter the tool registry — it reuses
``build_mcp_server`` from ``mcp_server`` and only sets host/port.
"""

import os

from mcp.server.transport_security import TransportSecuritySettings

from mcp_server import build_mcp_server


def main() -> None:
    host = os.environ.get("CHEM_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("CHEM_MCP_PORT", "5058"))

    server = build_mcp_server()
    server.settings.host = host
    server.settings.port = port
    server.settings.streamable_http_path = "/mcp"
    server.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "chem:*"],
    )
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
