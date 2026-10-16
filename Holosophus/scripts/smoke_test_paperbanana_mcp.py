#!/usr/bin/env python3
"""Smoke test for the PaperBanana MCP service."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
from pathlib import Path

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import ImageContent


REQUIRED_TOOLS = {"generate_diagram", "generate_plot", "evaluate_diagram"}


def _normalize_url(url: str) -> str:
    clean = url.rstrip("/")
    if clean.endswith("/mcp"):
        return clean
    return f"{clean}/mcp"


async def _run(url: str, output_path: Path) -> None:
    async with streamablehttp_client(url, timeout=60, sse_read_timeout=900) as (
        read_stream,
        write_stream,
        _,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            missing = REQUIRED_TOOLS - tool_names
            if missing:
                raise RuntimeError(f"Missing MCP tools: {sorted(missing)}")

            result = await session.call_tool(
                "generate_plot",
                {
                    "data_json": json.dumps(
                        {
                            "x": ["baseline", "paperbanana"],
                            "y": [0.72, 0.81],
                        }
                    ),
                    "intent": "Create a clean bar chart comparing baseline and PaperBanana smoke scores.",
                    "iterations": 1,
                },
                read_timeout_seconds=None,
            )

            if result.isError:
                raise RuntimeError(f"Tool returned error: {result.content}")

            image_block = next(
                (block for block in result.content if isinstance(block, ImageContent)),
                None,
            )
            if image_block is None:
                raise RuntimeError(f"Expected ImageContent, got: {result.content}")

            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(base64.b64decode(image_block.data))
            print(f"Saved smoke image to {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:5060/mcp",
        help="PaperBanana MCP URL or base service URL.",
    )
    parser.add_argument(
        "--output",
        default="workdir/figures/paperbanana/paperbanana_smoke_plot.png",
        help="Path to save the generated smoke image.",
    )
    args = parser.parse_args()

    asyncio.run(_run(_normalize_url(args.url), Path(args.output)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
