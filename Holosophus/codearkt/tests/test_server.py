from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from codearkt.server import AgentRequest, get_main_app
from codearkt.codeact import CodeActAgent
from codearkt.llm import LLM
from codearkt.event_bus import AgentEventBus
from codearkt.settings import settings


async def test_agents_list_and_cancel_endpoint() -> None:
    agent = CodeActAgent(name="root", description="root", llm=LLM("gpt-4o"))
    app = get_main_app(
        agent,
        event_bus=AgentEventBus(),
        mcp_config=None,
        server_host=settings.DEFAULT_SERVER_HOST,
        server_port=0,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/agents/list")
        assert resp.status_code == 200
        cards = resp.json()
        assert any(card["name"] == "root" for card in cards)

        cancel = await client.post("/agents/cancel", json={"session_id": "nonexistent"})
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "cancelled"


def _stub_app(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> Any:
    """Build an agent app whose ainvoke is a no-op, with WORKSPACE_DIR redirected.

    Lets us assert the synchronous session-creation side effects (owner marker,
    figures bridge) that the /agents/<name> handler runs before the agent task,
    without firing a real LLM run.
    """
    monkeypatch.setenv("WORKSPACE_DIR", str(workspace))

    async def fake_ainvoke(self: CodeActAgent, *args: Any, **kwargs: Any) -> str:
        return "ok"

    monkeypatch.setattr(CodeActAgent, "ainvoke", fake_ainvoke)

    agent = CodeActAgent(name="root", description="root", llm=LLM("gpt-4o"))
    return get_main_app(
        agent,
        event_bus=AgentEventBus(),
        mcp_config=None,
        server_host=settings.DEFAULT_SERVER_HOST,
        server_port=0,
    )


async def test_agents_endpoint_writes_owner_marker_from_header(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = _stub_app(monkeypatch, tmp_path)
    session_id = "research__owner__model__001"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agents/root",
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "session_id": session_id,
                "stream": False,
            },
            headers={"X-Holo-Client": "client-A"},
        )
        assert resp.status_code == 200

    marker = tmp_path / session_id / ".holosophus_ui" / "owner"
    assert marker.is_file()
    assert marker.read_text(encoding="utf-8").strip() == "client-A"

    # The same session-creation handler also builds the paperbanana figures
    # bridge (SC1) before the agent task — guard against it being dropped.
    assert (tmp_path / session_id / "figures" / "paperbanana").is_symlink()


async def test_agents_endpoint_without_header_skips_owner_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = _stub_app(monkeypatch, tmp_path)
    session_id = "research__owner__model__002"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/agents/root",
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "session_id": session_id,
                "stream": False,
            },
        )
        assert resp.status_code == 200

    # No X-Holo-Client => legacy (untagged) session, no marker on disk.
    assert not (tmp_path / session_id / ".holosophus_ui" / "owner").exists()


def test_agent_request_orchestrator_defaults_to_codearkt() -> None:
    """SC1: a request body without an explicit orchestrator defaults to
    "codearkt" so the existing UI/smoke contract is backward-compatible."""
    request = AgentRequest(messages=[{"role": "user", "content": "hi"}])
    assert request.orchestrator == "codearkt"


def test_agent_request_model_name_defaults_to_none() -> None:
    """A request body without an explicit model_name defaults to None, so
    an unchanged client behaves exactly as before (LLM's own configured
    default is used)."""
    request = AgentRequest(messages=[{"role": "user", "content": "hi"}])
    assert request.model_name is None


def test_agent_request_model_name_round_trips() -> None:
    request = AgentRequest(
        messages=[{"role": "user", "content": "hi"}],
        model_name="deepseek/deepseek-v4-pro",
    )
    assert request.model_name == "deepseek/deepseek-v4-pro"


async def test_agents_endpoint_orchestrator_default_runs_and_rejects_foreign(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """SC1: the codearkt server runs a request that omits `orchestrator`
    (defaults to codearkt), but rejects a request explicitly addressed to the
    claude_code orchestrator with 400 -- guards against nginx routing desync."""
    app = _stub_app(monkeypatch, tmp_path)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Field omitted -> default "codearkt" -> runs as before (unchanged).
        default_resp = await client.post(
            "/agents/root",
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "session_id": "research__owner__model__003",
                "stream": False,
            },
        )
        assert default_resp.status_code == 200

        # Explicit foreign orchestrator reaching this server -> 400, not a run.
        foreign_resp = await client.post(
            "/agents/root",
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "session_id": "research__owner__model__004",
                "stream": False,
                "orchestrator": "claude_code",
            },
        )
        assert foreign_resp.status_code == 400
