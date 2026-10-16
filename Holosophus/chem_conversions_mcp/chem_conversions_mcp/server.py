from typing import Literal

import fire  # type: ignore
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from chem_conversions_mcp.converters import convert_chemical_representation


def run(
    host: str = "0.0.0.0",
    port: int = 5061,
    mount_path: str = "/",
    streamable_http_path: str = "/mcp",
    transport: Literal["stdio", "sse", "streamable-http"] = "streamable-http",
) -> None:
    server = FastMCP(
        "Chemical conversions MCP",
        stateless_http=True,
        streamable_http_path=streamable_http_path,
        mount_path=mount_path,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[
                "127.0.0.1:*",
                "localhost:*",
                "chem_conversions:*",
            ],
        ),
    )
    server.add_tool(convert_chemical_representation)
    server.settings.port = port
    server.settings.host = host
    server.run(transport=transport)


if __name__ == "__main__":
    fire.Fire(run)
