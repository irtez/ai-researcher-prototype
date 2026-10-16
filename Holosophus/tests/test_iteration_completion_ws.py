"""Completion notifications use persisted history, without running an agent."""

import threading
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "holosophos"), str(ROOT / "codearkt")]
from codearkt import auth  # noqa: E402
from holosophos import research_iterations as history, research_session  # noqa: E402
from holosophos.ui_api import create_app  # noqa: E402


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.delenv("HOLO_AUTH_USERS", raising=False)
    monkeypatch.delenv("HOLO_AUTH_SECRET", raising=False)
    session = tmp_path / "research_ws_test"
    session.mkdir()
    research_session.check_session_owner(session, "owner", claim=True)
    history.start_iteration(session, kind="initial", user_message="Task", settings={})
    return session, TestClient(create_app(workspace_root=tmp_path))


def url(session, iteration="0001"):
    return f"/ui/ws/sessions/{session.name}/iterations/{iteration}/completion"


@pytest.mark.parametrize("status", ["complete", "error", "cancelled"])
def test_already_finished_sends_only_one_notification(fixture, status):
    session, client = fixture
    history.finish_iteration(session, "0001", status=status)
    with client.websocket_connect(url(session)) as ws:
        ws.send_json({"client_id": "owner"})
        assert ws.receive_json() == {"iteration_id": "0001", "status": status}
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1000


def test_waits_for_persisted_completion_without_changing_history(fixture, monkeypatch):
    session, client = fixture
    checked = threading.Event()
    original = history.get_iteration

    def observe(*args):
        result = original(*args)
        checked.set()
        return result

    monkeypatch.setattr(history, "get_iteration", observe)
    with client.websocket_connect(url(session)) as ws:
        ws.send_json({"client_id": "owner"})
        assert checked.wait(2)
        assert history.get_iteration(session, "0001")["status"] == "queued"
        event = history.append_event(session, "0001", {"event_type": "output", "content": "Message"})
        history.finish_iteration(session, "0001", status="complete")
        assert ws.receive_json() == {"iteration_id": "0001", "status": "complete"}
    assert history.read_iteration(session, "0001")["events"] == [event]


@pytest.mark.parametrize("identity,iteration", [({}, "0001"), ({"client_id": "other"}, "0001"), ({"client_id": "owner"}, "9999")])
def test_no_notification_for_missing_or_foreign_session(fixture, identity, iteration):
    session, client = fixture
    with client.websocket_connect(url(session, iteration)) as ws:
        ws.send_json(identity)
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 1008


def test_disconnect_stops_watcher(fixture, monkeypatch):
    session, client = fixture
    checked = threading.Event()
    original = history.get_iteration

    def observe(*args):
        checked.set()
        return original(*args)

    monkeypatch.setattr(history, "get_iteration", observe)
    with client.websocket_connect(url(session)) as ws:
        ws.send_json({"client_id": "owner"})
        assert checked.wait(2)
        ws.close()
    assert history.get_iteration(session, "0001")["status"] == "queued"


def test_auth_token_is_required_and_owns_subscription(fixture, monkeypatch):
    session, client = fixture
    monkeypatch.setenv("HOLO_AUTH_USERS", "owner:password,other:password")
    monkeypatch.setenv("HOLO_AUTH_SECRET", "ws-test-secret")
    history.finish_iteration(session, "0001", status="complete")
    for token in (None, "invalid", auth.mint_token("other")):
        with client.websocket_connect(url(session)) as ws:
            ws.send_json({"client_id": "owner", "token": token})
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
    with client.websocket_connect(url(session)) as ws:
        ws.send_json({"client_id": "spoofed", "token": auth.mint_token("owner")})
        assert ws.receive_json()["status"] == "complete"


def test_proxy_identity_overrides_browser_identity(fixture):
    session, client = fixture
    with client.websocket_connect(url(session), headers={"X-Holo-Client": "other"}) as ws:
        ws.send_json({"client_id": "owner"})
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
