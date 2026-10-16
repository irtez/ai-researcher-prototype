"""FastMCP server exposing deterministic Writer analysis tools."""

from __future__ import annotations

import argparse
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from research_paper_registry import PaperRegistry, file_sha256

from holosophos.research_session import read_research_profile
from holosophos.settings import settings
from holosophos.tools.writer.check_state import CheckStore
from holosophos.tools.writer.content_placement import analyze_content_placement
from holosophos.tools.writer.mcp_paths import (
    normalize_report_paths,
    resolve_workspace_file,
)
from holosophos.tools.writer.paper_length import analyze_paper_length
from holosophos.tools.writer.section_order import analyze_section_order
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings


class StrictFastMCP(FastMCP):
    """Reject arguments that are absent from a tool's public schema."""

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        tool = self._tool_manager.get_tool(name)
        if tool is not None:
            properties = tool.parameters.get("properties", {})
            allowed = set(properties) if isinstance(properties, dict) else set()
            unexpected = sorted(set(arguments) - allowed)
            if unexpected:
                names = ", ".join(unexpected)
                raise ToolError(f"Unexpected arguments for {name}: {names}")
        return await super().call_tool(name, arguments)


def _apply_main_content_policy(
    report: dict[str, Any], research_profile: str = "general"
) -> dict[str, Any]:
    # Measure length for every profile. The writer checks any explicit user/venue
    # limit against the assignment; the server has no universal page minimum.
    pages: object = report.get("main_content_pages")
    if (
        isinstance(pages, bool)
        or not isinstance(pages, (int, float))
        or not math.isfinite(pages)
        or pages <= 0
    ):
        meets_minimum = False
    else:
        meets_minimum = "main_content_heading_not_found" not in report.get("diagnostics", [])
    if "main_content_heading_not_found" in report.get("diagnostics", []):
        report["main_content_verdict_reason"] = (
            "No main-body heading was recognized in the PDF. Use a visible heading "
            "such as Introduction / Введение or a numbered scientific section, "
            "recompile and rerun the checks. Heading detection is a structural check, "
            "not evidence that the article is substantively complete."
        )
    report.update(
        {
            "minimum_main_content_pages": None,
            "meets_main_content_minimum": meets_minimum,
            "main_content_page_deficit": None,
        }
    )
    return report


def create_server(
    workspace_dir: str | Path | None = None,
    store: CheckStore | None = None,
    *,
    host: str = "127.0.0.1",
    port: int | None = None,
) -> FastMCP:
    workspace = Path(workspace_dir or settings.WORKSPACE_DIR).resolve()
    registry = PaperRegistry(workspace)
    active_store = store or CheckStore(
        settings.WRITER_CHECK_STATE_DB_PATH,
        max_reruns=settings.WRITER_CHECK_MAX_RERUNS,
        max_tool_retries=settings.WRITER_CHECK_TOOL_MAX_RETRIES,
        retention_hours=settings.WRITER_CHECK_STATE_RETENTION_HOURS,
    )
    server = StrictFastMCP(
        "writer-tools",
        host=host,
        port=port or settings.WRITER_TOOLS_PORT,
        streamable_http_path="/mcp",
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=["127.0.0.1:*", "localhost:*", "writer-tools:*"],
        ),
    )

    @server.tool(structured_output=True)
    def start_writer_check(
        session_id: str, agent_name: str,
        tex_path: str = "paper.tex", pdf_path: str = "paper.pdf",
    ) -> dict[str, Any]:
        """Register this article's session-relative paths and start its checks.

        Analyzer calls reuse the registered paths. Missing files affect only the
        document checks and do not prevent the research session from finishing.
        """
        paths = registry.normalize_paths(session_id, tex_path, pdf_path)
        active_store.cleanup()
        state = active_store.start(session_id, agent_name, **paths)
        try:
            registry.register(session_id, state["check_run_id"], **paths)
        except (ValueError, OSError):
            active_store.finish(state["check_run_id"], session_id, agent_name, "abandoned")
            raise
        return state

    @server.tool(name="section_order", structured_output=True)
    def section_order_tool(
        check_run_id: str,
        pass_number: int,
        session_id: str,
        agent_name: str,
    ) -> dict[str, Any]:
        return _run_analyzer(
            store=active_store,
            workspace_dir=workspace,
            check_run_id=check_run_id,
            pass_number=pass_number,
            session_id=session_id,
            agent_name=agent_name,
            tool_name="section_order",
            suffix=".tex",
            analyzer=analyze_section_order,
        )

    @server.tool(name="content_placement", structured_output=True)
    def content_placement_tool(
        check_run_id: str,
        pass_number: int,
        session_id: str,
        agent_name: str,
    ) -> dict[str, Any]:
        return _run_analyzer(
            store=active_store,
            workspace_dir=workspace,
            check_run_id=check_run_id,
            pass_number=pass_number,
            session_id=session_id,
            agent_name=agent_name,
            tool_name="content_placement",
            suffix=".tex",
            analyzer=lambda path: analyze_content_placement(path, source_root=str(workspace / session_id)),
        )

    @server.tool(name="paper_length", structured_output=True)
    def paper_length_tool(
        check_run_id: str,
        pass_number: int,
        session_id: str,
        agent_name: str,
    ) -> dict[str, Any]:
        return _run_analyzer(
            store=active_store,
            workspace_dir=workspace,
            check_run_id=check_run_id,
            pass_number=pass_number,
            session_id=session_id,
            agent_name=agent_name,
            tool_name="paper_length",
            suffix=".pdf",
            analyzer=analyze_paper_length,
        )

    @server.tool(structured_output=True)
    def finish_writer_check(
        check_run_id: str,
        outcome: Literal["clean", "blocked", "abandoned"],
        session_id: str,
        agent_name: str,
    ) -> dict[str, Any]:
        state = active_store.get(check_run_id, session_id, agent_name)
        checked_fingerprints = {}
        if outcome == "clean":
            for item in active_store.checked_inputs(check_run_id, session_id, agent_name):
                key, suffix = ("pdf_path", ".pdf") if item["tool_name"] == "paper_length" else ("tex_path", ".tex")
                path = registry.resolve_file(session_id, state[key], suffix)
                if not path.is_file() or file_sha256(path) != item["fingerprint"]:
                    registry.update_status(session_id, check_run_id, "checking", "Article changed after checks")
                    raise ValueError(f"{state[key]} changed after checks; run the next complete pass before finishing clean")
                checked_fingerprints[suffix[1:]] = item["fingerprint"]
        state = active_store.finish(check_run_id, session_id, agent_name, outcome)
        registry.update_status(
            session_id, check_run_id, "ready" if outcome == "clean" else outcome,
            checked_fingerprints=checked_fingerprints if outcome == "clean" else None,
        )
        return state

    return server


def _run_analyzer(
    *,
    store: CheckStore,
    workspace_dir: Path,
    check_run_id: str,
    pass_number: int,
    session_id: str,
    agent_name: str,
    tool_name: str,
    suffix: str,
    analyzer: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    state = store.get(check_run_id, session_id, agent_name)
    registry = PaperRegistry(workspace_dir)
    selected_path = state["pdf_path" if suffix == ".pdf" else "tex_path"]
    logical_path = f"{session_id}/{selected_path}"
    try:
        registry.resolve_file(session_id, selected_path, suffix)
        resolved = resolve_workspace_file(workspace_dir, logical_path, suffix)
        report = analyzer(str(resolved.absolute))
        diagnostics = report.get("diagnostics", [])
        if any(
            item in diagnostics
            for item in ("pdf_missing", "pdf_parse_failed", "tex_missing", "tex_read_failed")
        ):
            raise ValueError("writer input could not be parsed")
    except (OSError, UnicodeError, ValueError) as exc:
        state = store.record_failure(check_run_id, session_id, agent_name, pass_number, tool_name)
        registry.update_status(session_id, check_run_id, "blocked" if state["status"] == "blocked" else "checking", f"{tool_name}: cannot read {selected_path}")
        raise ValueError(
            f"{tool_name} failed; retries={state['tool_failures'].get(tool_name, 0)}; verify or compile {selected_path} in this session"
        ) from exc

    normalized = normalize_report_paths(report, workspace_dir)
    length_kwargs: dict[str, Any] = {}
    if tool_name == "paper_length":
        normalized = _apply_main_content_policy(
            normalized, read_research_profile(workspace_dir / session_id)
        )
        length_kwargs = {
            "main_content_pages": normalized["main_content_pages"],
            "minimum_main_content_pages": normalized["minimum_main_content_pages"],
            "meets_main_content_minimum": normalized["meets_main_content_minimum"],
        }

    state = store.record_success(
        check_run_id,
        session_id,
        agent_name,
        pass_number,
        tool_name,
        resolved.relative,
        resolved.fingerprint,
        **length_kwargs,
    )
    normalized["check_state"] = state
    return normalized


def run(
    transport: Literal["stdio", "sse", "streamable-http"] = "streamable-http",
    host: str = "127.0.0.1",
    port: int | None = None,
) -> None:
    create_server(host=host, port=port).run(transport=transport)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--transport",
        default="streamable-http",
        choices=["stdio", "sse", "streamable-http"],
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    run(transport=args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
