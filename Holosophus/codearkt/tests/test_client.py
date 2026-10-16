import json
import logging
import pytest

import codearkt.client as client_module
from codearkt.client import (
    _compose_agent_url,
    _compose_base_url,
    _compose_cancel_url,
    query_agent,
    stop_agent,
)
from codearkt.event_bus import EventType
from codearkt.llm import ChatMessage
from codearkt.util import get_unique_id
from tests.conftest import MCPServerTest


class TestUrlComposition:
    def test_client_url_compose_base_url_with_http(self) -> None:
        url = _compose_base_url("http://localhost", 8000)
        assert url == "http://localhost:8000"

    def test_client_url_compose_base_url_without_http(self) -> None:
        url = _compose_base_url("localhost", 8000)
        assert url == "http://localhost:8000"

    def test_client_url_compose_base_url_with_https(self) -> None:
        url = _compose_base_url("https://example.com", 443)
        assert url == "https://example.com:443"

    def test_client_url_compose_agent_url(self) -> None:
        url = _compose_agent_url("localhost", 8000, "manager")
        assert url == "http://localhost:8000/agents/manager"

    def test_client_url_compose_agent_url_custom_agent(self) -> None:
        url = _compose_agent_url("http://example.com", 9000, "custom_agent")
        assert url == "http://example.com:9000/agents/custom_agent"

    def test_client_url_compose_cancel_url(self) -> None:
        url = _compose_cancel_url("localhost", 8000)
        assert url == "http://localhost:8000/agents/cancel"

    def test_client_url_compose_cancel_url_with_https(self) -> None:
        url = _compose_cancel_url("https://example.com", 443)
        assert url == "https://example.com:443/agents/cancel"


async def test_client_base(mcp_server_test: MCPServerTest) -> None:
    history = [ChatMessage(role="user", content="What is 432412421249 * 4332144219?")]
    output_text = ""
    session_id = get_unique_id()
    for event in query_agent(
        history,
        host=mcp_server_test.host,
        port=mcp_server_test.port,
        agent_name="nested_agent",
        session_id=session_id,
    ):
        if event.event_type == EventType.OUTPUT and event.content:
            output_text += event.content
    output_text = output_text.replace(",", "").replace(".", "")
    assert "1873272970937648109531" in output_text, output_text


async def test_client_stop(mcp_server_test: MCPServerTest) -> None:
    history = [ChatMessage(role="user", content="What is 432412421249 * 4332144219?")]
    output_text = ""
    session_id = get_unique_id()
    for event in query_agent(
        history,
        host=mcp_server_test.host,
        port=mcp_server_test.port,
        agent_name="nested_agent",
        session_id=session_id,
    ):
        if event.event_type == EventType.OUTPUT and event.content:
            output_text += event.content
        stop_agent(session_id, host=mcp_server_test.host, port=mcp_server_test.port)
    assert "1873272970937648109531" not in output_text



class FakeResponse:
    def __init__(self, chunks):
        self._chunks = chunks
    def raise_for_status(self) -> None:
        return None
    def iter_text(self):
        for chunk in self._chunks:
            yield chunk
class FakeStreamContext:
    def __init__(self, response: FakeResponse):
        self._response = response
    def __enter__(self) -> FakeResponse:
        return self._response
    def __exit__(self, exc_type, exc, tb) -> None:
        return None
def _event_json(
    *,
    session_id: str = "s1",
    agent_name: str = "manager",
    event_type: str = "output",
    content: str = "hello",
) -> str:
    return json.dumps(
        {
            "session_id": session_id,
            "agent_name": agent_name,
            "event_type": event_type,
            "content": content,
        }
    )
def test_query_agent_logs_warning_after_long_silence(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(client_module, "_validate_holosophos_required_env", lambda: None)
    response = FakeResponse(
        [
            _event_json(content="first"),
            _event_json(content="second"),
        ]
    )
    monkeypatch.setattr(
        client_module.httpx,
        "stream",
        lambda *args, **kwargs: FakeStreamContext(response),
    )
    monotonic_values = iter(
        [
            0.0,
            1.0,
            2.0,
            605.0,
            606.0,
        ]
    )
    monkeypatch.setattr(client_module.time, "monotonic", lambda: next(monotonic_values))
    with caplog.at_level(logging.WARNING, logger=client_module.logger.name):
        events = list(
            query_agent(
                [ChatMessage(role="user", content="hello")],
                session_id="s1",
                host="localhost",
                port=5055,
                agent_name="manager",
            )
        )
    assert len(events) == 2
    assert events[0].content == "first"
    assert events[1].content == "second"
    assert "No agent events received for" in caplog.text
    assert "session_id=s1" in caplog.text or "s1" in caplog.text