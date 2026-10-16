#!/usr/bin/env python3
"""Smoke test the Writer tools MCP service and its persisted check state."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Literal

import fitz
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


DEFAULT_URL = "http://127.0.0.1:5059/mcp"
DEFAULT_SESSION = "smoke__writer_tools"
REQUIRED_TOOLS = {
    "start_writer_check",
    "section_order",
    "content_placement",
    "paper_length",
    "finish_writer_check",
}


def _normalize_url(url: str) -> str:
    clean = url.rstrip("/")
    return clean if clean.endswith("/mcp") else f"{clean}/mcp"


def _structured(result: Any) -> dict[str, Any]:
    if result.isError:
        raise RuntimeError(f"MCP tool returned an error: {result.content}")
    structured = result.structuredContent
    if not isinstance(structured, dict):
        raise RuntimeError(f"Expected structured MCP output, got: {result.content}")
    if set(structured) == {"result"} and isinstance(structured["result"], dict):
        return structured["result"]
    return structured


def _prepare_inputs(workdir: Path, session_id: str) -> tuple[Path, Path, Path]:
    session_dir = workdir / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    tex_path = session_dir / "paper.tex"
    pdf_path = session_dir / "paper.pdf"
    metadata_path = session_dir / "check.json"
    tex_path.write_text(
        "\\section{Introduction}\n"
        "A deterministic Writer MCP smoke test.\n"
        "\\section{Method}\n"
        "The service checks this ordered paper.\n"
        "\\section{Results}\n"
        "The quality-check state persists across restarts.\n",
        encoding="utf-8",
    )

    pdf = fitz.open()
    for page_number in range(2):
        page = pdf.new_page(width=612, height=792)
        if page_number == 0:
            page.insert_text((72, 72), "Abstract", fontsize=16)
        page.insert_text(
            (72, 110),
            f"Writer tools MCP smoke-test main-content page {page_number + 1}.",
            fontsize=11,
        )
    references_page = pdf.new_page(width=612, height=792)
    references_page.insert_text((72, 72), "References", fontsize=16)
    pdf.save(pdf_path)
    pdf.close()
    return tex_path, pdf_path, metadata_path


async def _start(
    session: ClientSession,
    *,
    session_id: str,
    workdir: Path,
) -> None:
    _, _, metadata_path = _prepare_inputs(workdir, session_id)
    tools = await session.list_tools()
    tool_names = {tool.name for tool in tools.tools}
    if tool_names != REQUIRED_TOOLS:
        raise RuntimeError(
            f"Unexpected Writer MCP tools: expected {sorted(REQUIRED_TOOLS)}, "
            f"got {sorted(tool_names)}"
        )

    identity = {"session_id": session_id, "agent_name": "writer"}
    started = _structured(await session.call_tool("start_writer_check", identity))
    common = {
        **identity,
        "check_run_id": started["check_run_id"],
        "pass_number": started["current_pass"],
    }
    _structured(await session.call_tool("section_order", common))
    placement = _structured(await session.call_tool("content_placement", common))
    state = placement["check_state"]
    if state["completed_passes"] != 0 or state["current_pass"] != 1:
        raise RuntimeError(f"Unexpected partial-pass state: {state}")

    metadata_path.write_text(
        json.dumps(
            {
                "check_run_id": started["check_run_id"],
                "pass_number": started["current_pass"],
                "session_id": session_id,
                "agent_name": "writer",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Started Writer check {started['check_run_id']}; state saved to {metadata_path}")


async def _resume(
    session: ClientSession,
    *,
    session_id: str,
    workdir: Path,
) -> None:
    _, _, metadata_path = _prepare_inputs(workdir, session_id)
    if not metadata_path.is_file():
        raise RuntimeError(f"Missing start-phase metadata: {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("session_id") != session_id or metadata.get("agent_name") != "writer":
        raise RuntimeError(f"Smoke metadata identity does not match: {metadata}")

    common = {
        "check_run_id": metadata["check_run_id"],
        "pass_number": metadata["pass_number"],
        "session_id": session_id,
        "agent_name": "writer",
    }
    length = _structured(await session.call_tool("paper_length", common))
    state = length["check_state"]
    if state["completed_passes"] != 1 or state["quality_reruns_used"] != 0:
        raise RuntimeError(f"Unexpected completed-pass state: {state}")
    if length["minimum_main_content_pages"] is not None:
        raise RuntimeError(f"Unexpected minimum: {length}")
    if length["meets_main_content_minimum"] is not True:
        raise RuntimeError(f"The smoke paper has no measurable main content: {length}")
    if length["main_content_page_deficit"] is not None:
        raise RuntimeError(f"Unexpected length constraint on the default profile: {length}")
    if length["total_pages"] != 3:
        raise RuntimeError(f"Unexpected measured page count: {length}")

    finished = _structured(
        await session.call_tool(
            "finish_writer_check",
            {
                "check_run_id": metadata["check_run_id"],
                "outcome": "clean",
                "session_id": session_id,
                "agent_name": "writer",
            },
        )
    )
    if finished["status"] != "clean":
        raise RuntimeError(f"Writer check did not finish cleanly: {finished}")
    print(f"Resumed and finished Writer check {metadata['check_run_id']} cleanly")


async def _run(
    url: str,
    phase: Literal["start", "resume", "all"],
    session_id: str,
    workdir: Path,
) -> None:
    async with streamablehttp_client(url, timeout=60, sse_read_timeout=120) as (
        read_stream,
        write_stream,
        _,
    ):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            if phase in {"start", "all"}:
                await _start(session, session_id=session_id, workdir=workdir)
            if phase in {"resume", "all"}:
                await _resume(session, session_id=session_id, workdir=workdir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="Writer MCP URL.")
    parser.add_argument("--phase", choices=("start", "resume", "all"), default="all")
    parser.add_argument("--session-id", default=DEFAULT_SESSION)
    parser.add_argument("--workdir", type=Path, default=Path("workdir"))
    args = parser.parse_args()

    asyncio.run(
        _run(
            _normalize_url(args.url),
            args.phase,
            args.session_id,
            args.workdir.resolve(),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
