"""App-level auth wired through the CodeArkt server: login, the run gate, and
owner-from-token (owner marker + per-account ledger)."""

from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from codearkt.codeact import CodeActAgent
from codearkt.event_bus import AgentEventBus
from codearkt.llm import LLM
from codearkt.server import get_main_app
from codearkt.settings import settings


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOLO_AUTH_USERS", "a.stepin:pw-one,m.mozikov:pw-two")
    monkeypatch.setenv("HOLO_AUTH_SECRET", "test-secret-please")
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "CAPACITY_ENABLED", True)


def _app(monkeypatch: pytest.MonkeyPatch) -> Any:
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


async def test_login_good_and_bad_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.post("/agents/login", json={"username": "a.stepin", "password": "pw-one"})
        assert ok.status_code == 200
        assert ok.json()["username"] == "a.stepin"
        assert ok.json()["token"]

        bad = await client.post("/agents/login", json={"username": "a.stepin", "password": "nope"})
        assert bad.status_code == 401


async def test_run_requires_valid_token(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    app = _app(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # No Authorization header -> 401.
        no_auth = await client.post(
            "/agents/root",
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "session_id": "sess-anon",
                "stream": False,
            },
        )
        assert no_auth.status_code == 401

        # Log in, then start a run with the token -> 200, owned by the login.
        token = (
            await client.post("/agents/login", json={"username": "a.stepin", "password": "pw-one"})
        ).json()["token"]
        ok = await client.post(
            "/agents/root",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "session_id": "sess-auth",
                "stream": False,
            },
        )
        assert ok.status_code == 200

        marker = tmp_path / "sess-auth" / ".holosophus_ui" / "owner"
        assert marker.read_text(encoding="utf-8").strip() == "a.stepin"


async def test_forged_x_holo_client_cannot_bypass_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # A forged owner header with no valid token must still be rejected.
        resp = await client.post(
            "/agents/root",
            headers={"X-Holo-Client": "a.stepin"},
            json={
                "messages": [{"role": "user", "content": "hi"}],
                "session_id": "sess-forge",
                "stream": False,
            },
        )
        assert resp.status_code == 401
