import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from mcp.types import CallToolResult, TextContent


MODULE_PATH = Path(__file__).resolve().parents[1] / "container" / "tools.py"
SPEC = importlib.util.spec_from_file_location("codearkt_container_tools", MODULE_PATH)
assert SPEC is not None
assert SPEC.loader is not None
container_tools = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(container_tools)


def test_compose_arguments_serializes_dict_as_json_for_string_fields() -> None:
    tool_schema = SimpleNamespace(
        inputSchema={
            "type": "object",
            "properties": {
                "file_text": {"type": "string"},
                "path": {"type": "string"},
            },
        }
    )
    arguments = container_tools._compose_arguments(
        tool_schema,
        file_text={"a": 1, "b": [1, 2]},
        path="data.json",
    )
    assert json.loads(arguments["file_text"]) == {"a": 1, "b": [1, 2]}
    assert arguments["path"] == "data.json"


def test_extract_tool_error_reads_mcp_error_payload() -> None:
    result = CallToolResult(
        content=[TextContent(type="text", text="Error executing tool arxiv_search: broken")],
        structuredContent=None,
        isError=True,
    )
    assert (
        container_tools._extract_tool_error(result) == "Error executing tool arxiv_search: broken"
    )


def test_parse_arxiv_response_accepts_scalar_opensearch_fields() -> None:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>1</opensearch:totalResults>
  <opensearch:startIndex>0</opensearch:startIndex>
  <entry>
    <id>http://arxiv.org/abs/1234.5678v1</id>
    <title> Test Paper </title>
    <summary> Some abstract text. </summary>
    <author><name>Alice</name></author>
    <published>2024-01-02T00:00:00Z</published>
    <updated>2024-01-03T00:00:00Z</updated>
    <category term="cs.AI" />
    <arxiv:comment xmlns:arxiv="http://arxiv.org/schemas/atom">Demo</arxiv:comment>
  </entry>
</feed>
"""
    parsed = container_tools._parse_arxiv_response(xml, include_abstracts=True)
    assert parsed["total_count"] == 1
    assert parsed["offset"] == 0
    assert parsed["returned_count"] == 1
    assert parsed["results"][0]["id"] == "1234.5678v1"
    assert parsed["results"][0]["authors"] == "Alice"
    assert parsed["results"][0]["categories"] == "cs.AI"
    assert parsed["results"][0]["abstract"] == "Some abstract text."


def test_bind_tool_identity_attaches_session_and_agent() -> None:
    def tool(**kwargs):
        return kwargs

    bound = container_tools.bind_tool_identity(tool, session_id="session-1", agent_name="writer")

    assert bound()["session_id"] == "session-1"
    assert bound()["agent_name"] == "writer"


def test_bind_tool_identity_rejects_call_time_overrides() -> None:
    def tool(**kwargs):
        return kwargs

    bound = container_tools.bind_tool_identity(tool, session_id="session-1", agent_name="writer")

    result = bound(session_id="forged-session", agent_name="forged-agent")

    assert result["session_id"] == "session-1"
    assert result["agent_name"] == "writer"


def test_bind_tool_identity_keeps_managed_agent_in_parent_session() -> None:
    def agent_tool(query: str, session_id: str):
        return {"query": query, "session_id": session_id}

    bound = container_tools.bind_tool_identity(
        agent_tool,
        session_id="research__main__001",
        agent_name="manager",
    )

    try:
        result = bound(
            query="collect datasets",
            session_id="research__main__001__step1_librarian_datasets",
            agent_name="forged-manager",
        )
    except TypeError:
        result = None

    assert result == {
        "query": "collect datasets",
        "session_id": "research__main__001",
    }


def test_compose_arguments_drops_unaccepted_identity() -> None:
    schema = SimpleNamespace(
        inputSchema={"type": "object", "properties": {"query": {"type": "string"}}}
    )

    arguments = container_tools._compose_arguments(
        schema,
        query="test",
        session_id="session-1",
        agent_name="writer",
    )

    assert arguments == {"query": "test"}
