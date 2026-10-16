"""Small no-LLM client for chemistry MCP discovery and direct tool calls."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def inspect_server(
    url: str,
    *,
    tool_name: str | None = None,
    arguments: dict[str, Any] | None = None,
    include_schemas: bool = False,
) -> None:
    async with streamablehttp_client(url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            tool_listing: list[object]
            if include_schemas:
                tool_listing = [
                    {"name": tool.name, "inputSchema": tool.inputSchema}
                    for tool in tools.tools
                ]
            else:
                tool_listing = [tool.name for tool in tools.tools]
            print(json.dumps({"tools": tool_listing}, indent=2))
            if tool_name is not None:
                result = await session.call_tool(tool_name, arguments or {})
                print(json.dumps(result.model_dump(mode="json"), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--tool")
    parser.add_argument("--arguments", default="{}")
    parser.add_argument("--schemas", action="store_true")
    args = parser.parse_args()

    arguments = json.loads(args.arguments)
    if not isinstance(arguments, dict):
        raise ValueError("--arguments must decode to a JSON object.")
    asyncio.run(
        inspect_server(
            args.url,
            tool_name=args.tool,
            arguments=arguments,
            include_schemas=args.schemas,
        )
    )


if __name__ == "__main__":
    main()
