"""Writer MCP calls remain bound to the active runner session."""

import asyncio
import pytest
from session_tools import MleSessionToolGate


@pytest.mark.parametrize(
    "name",
    [
        "start_writer_check",
        "section_order",
        "content_placement",
        "paper_length",
        "finish_writer_check",
    ],
)
def test_writer_hook_binds_session_without_rewriting_document_paths(tmp_path, name):
    gate = MleSessionToolGate("research_actual", tmp_path / "research_actual")
    args = {
        "session_id": "external",
        "agent_name": "writer",
        "tex_path": "results/article.tex",
    }
    result = asyncio.run(
        gate({"tool_name": "mcp__writer_tools__" + name, "tool_input": args})
    )
    assert result["hookSpecificOutput"]["updatedInput"] == {
        **args,
        "session_id": "research_actual",
    }
