"""Upload -> real CodeArkt HTTP endpoint -> agent input, without LLM or Docker."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest

from codearkt import server
from codearkt.event_bus import AgentEventBus
from codearkt.llm import ChatMessage
from holosophos.ui_api import create_app


@pytest.fixture
def apps(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    monkeypatch.delenv("HOLO_AUTH_USERS", raising=False)
    monkeypatch.delenv("HOLO_AUTH_SECRET", raising=False)
    seen = []
    class Agent:
        name = "manager"
        description = "Fixture manager"
        llm = SimpleNamespace()
        def get_all_agents(self):
            return [self]
        async def ainvoke(self, **kwargs):
            seen.append(kwargs)
            return "ok"
    agent = Agent()
    # Same integration hook installed by compose_main_agent.
    from holosophos.codearkt_inputs import prepare_input_files
    agent.input_files_preparer = prepare_input_files
    bus = AgentEventBus()
    app = server.get_agent_app(agent, "localhost", 9999, bus)
    return create_app(tmp_path), app, seen


def run(coro):
    return asyncio.run(coro)


async def upload(client, sid="research_fixture", owner="owner"):
    r = await client.post(f"/ui/sessions/{sid}/inputs", headers={"X-Holo-Client": owner},
        files=[("files", ("values.csv", b"x\n2\n4\n", "text/csv")),
               ("files", ("plot.png", b"image fixture", "image/png"))])
    assert r.status_code == 200, r.text
    return [item["path"] for item in r.json()["files"]]


async def request(ui, app, body, owner="owner"):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ui), base_url="http://test") as uc:
        paths = await upload(uc)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/manager", headers={"X-Holo-Client": owner}, json={
                "session_id": "research_fixture", "messages": [{"role": "user", "content": "Analyse attachments"}],
                "input_files": paths, **body})
        return response, uc


def test_upload_reaches_manager_and_locks_originals(apps, tmp_path):
    ui, app, seen = apps
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ui), base_url="http://test") as uc:
            paths = await upload(uc)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
                r = await ac.post("/manager", headers={"X-Holo-Client": "owner"}, json={
                    "session_id": "research_fixture", "messages": [{"role": "user", "content": "Analyse attachments"}],
                    "input_files": paths})
            assert r.status_code == 200, r.text
            content = seen[0]["messages"][-1].content
            assert "Analyse attachments" in content
            assert "inputs/values.csv" in content and "inputs/plot.png" in content
            assert "native Read" not in content
            assert (tmp_path / "research_fixture/inputs/values.csv").read_bytes() == b"x\n2\n4\n"
            retry = await uc.post("/ui/sessions/research_fixture/inputs", headers={"X-Holo-Client": "owner"},
                files={"files": ("values.csv", b"overwrite", "text/csv")})
            assert retry.status_code == 409
    run(scenario())


@pytest.mark.parametrize("path", ["../other/inputs/private.csv", "/tmp/private.csv", "inputs/missing.csv", "inputs/nested/x.csv", "inputs/x.exe"])
def test_invalid_paths_are_rejected_before_agent(apps, path):
    ui, app, seen = apps
    response, _ = run(request(ui, app, {"input_files": [path]}))
    assert response.status_code == 400
    assert not seen


def test_foreign_owner_cannot_start_with_uploaded_files(apps, tmp_path):
    ui, app, seen = apps
    response, _ = run(request(ui, app, {}, owner="stranger"))
    assert response.status_code == 404
    assert not seen
    assert (tmp_path / "research_fixture/.holosophus_ui/owner").read_text() == "owner"


def test_symlink_outside_session_is_rejected(apps, tmp_path):
    ui, app, seen = apps
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ui), base_url="http://test") as uc:
            await upload(uc)
        private = tmp_path / "private.csv"; private.write_text("private")
        path = tmp_path / "research_fixture/inputs/values.csv"; path.unlink(); path.symlink_to(private)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post("/manager", headers={"X-Holo-Client": "owner"}, json={
                "session_id": "research_fixture", "messages": [{"role": "user", "content": "Read"}],
                "input_files": ["inputs/values.csv"]})
        assert r.status_code == 400
        assert not seen
    run(scenario())


def test_empty_upload_preserves_legacy_request(apps):
    _, app, seen = apps
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post("/manager", json={"session_id": "legacy", "messages": [{"role": "user", "content": "Hello"}]})
        assert r.status_code == 200
        assert seen[0]["messages"] == [ChatMessage(role="user", content="Hello")]
    run(scenario())


def test_manager_composition_installs_input_preparer():
    from holosophos.main_agent import compose_main_agent
    from holosophos.codearkt_inputs import prepare_input_files
    agent = compose_main_agent()
    assert agent.input_files_preparer is prepare_input_files
    assert all(child.input_files_preparer is None for child in agent.managed_agents)
