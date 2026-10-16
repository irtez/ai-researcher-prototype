"""Tests for Writer MCP tool routing and allowlists."""

from __future__ import annotations

import pytest
from codearkt.prompt_storage import PromptStorage

from holosophos.main_agent import MCP_CONFIG, compose_main_agent
from holosophos.settings import settings


WRITER_MCP_TOOLS = {
    "start_writer_check",
    "section_order",
    "content_placement",
    "paper_length",
    "finish_writer_check",
}


def test_writer_tools_server_is_mounted() -> None:
    assert MCP_CONFIG["mcpServers"]["writer_tools"] == {
        "url": settings.WRITER_TOOLS_MCP_URL,
        "transport": "streamable-http",
    }


def test_only_writer_allowlist_contains_writer_mcp_tools() -> None:
    assert WRITER_MCP_TOOLS <= set(settings.WRITER_TOOLS)
    assert WRITER_MCP_TOOLS.isdisjoint(settings.MANAGER_TOOLS)
    assert WRITER_MCP_TOOLS.isdisjoint(settings.REVIEWER_TOOLS)
    assert WRITER_MCP_TOOLS.isdisjoint(settings.LIBRARIAN_TOOLS)
    assert WRITER_MCP_TOOLS.isdisjoint(settings.PROPOSER_TOOLS)


def test_composed_writer_receives_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        PromptStorage,
        "compose",
        staticmethod(lambda *args, **kwargs: PromptStorage.default()),
        raising=False,
    )
    manager = compose_main_agent(included_agents=("writer",), tools=())
    writer = next(agent for agent in manager.managed_agents or [] if agent.name == "writer")

    assert WRITER_MCP_TOOLS <= set(writer.tool_names)
