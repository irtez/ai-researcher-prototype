"""Operator control on the CC runner (docs/plans/2026-08-13-ui-operator-control-cc.md).

Covers the thin HTTP control surface the UI drives — ``POST /agents/control`` (pause/resume/
stop/steer/inject), ``POST /agents/answer``, ``GET /agents/question/{id}`` — plus the two new
event helpers (``manager_end_event`` deferred close, ``rate_limit_wait_event`` heartbeat) and the
B6 suppression predicate. Endpoints are exercised through ``TestClient`` against a real temp
``WORKSPACE_DIR`` and the written control files are read back with ``cc_run_control`` (so this
tests the endpoint→file-channel path end to end, not a mock). The multi-turn "exactly one manager
agent_end" behavior through a live SDK stream is the HTTP smoke's job (Validation Plan).

Linux-only: importing ``cc_runner`` pulls ``codearkt`` (fcntl) + the Claude Agent SDK, so this
runs in the project's test container, not on a dev Windows box.
"""

import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import cc_run_control as ctl  # noqa: E402
import cc_runner  # noqa: E402
from codearkt.event_bus import EventType  # noqa: E402

# ── event helpers ──


def test_manager_end_event_shape() -> None:
    ev = cc_runner.cc_research_lib.manager_end_event("sess-x")
    assert ev.session_id == "sess-x"
    assert ev.agent_name == "manager"
    assert ev.event_type == EventType.AGENT_END
    assert ev.content is None


def test_is_manager_agent_end_predicate() -> None:
    # The per-turn manager close is suppressed; a sub-agent close / manager output is not.
    assert cc_runner._is_manager_agent_end(cc_runner.cc_research_lib.manager_end_event("s"))
    sub_end = cc_runner.AgentEvent(
        session_id="s", agent_name="mle_solver", event_type=EventType.AGENT_END, content=None
    )
    assert not cc_runner._is_manager_agent_end(sub_end)
    mgr_out = cc_runner.AgentEvent(
        session_id="s", agent_name="manager", event_type=EventType.OUTPUT, content="hi"
    )
    assert not cc_runner._is_manager_agent_end(mgr_out)


def test_rate_limit_wait_event_encodes_kind_and_remaining() -> None:
    ev = cc_runner.rate_limit_wait_event("sess-x", 123.7)
    assert ev.event_type == EventType.TOOL_RESPONSE
    assert ev.agent_name == "manager"
    payload = json.loads(ev.content)
    assert payload["kind"] == cc_runner.RATE_LIMIT_WAIT_KIND
    assert payload["resets_in_s"] == 124  # rounded


# ── HTTP control surface (real temp WORKSPACE_DIR, files read back) ──


def _client(monkeypatch, tmp_path: Path):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    return TestClient(cc_runner.app)


def _workdir(tmp_path: Path, sid: str) -> Path:
    return tmp_path.resolve() / sid


def test_control_pause_writes_command(monkeypatch, tmp_path: Path) -> None:
    with _client(monkeypatch, tmp_path) as client:
        resp = client.post("/agents/control", json={"session_id": "s1", "verb": "pause"})
    assert resp.status_code == 200
    assert resp.json()["verb"] == "pause"
    assert ctl.poll_command(_workdir(tmp_path, "s1")) == "pause"


def test_control_resume_and_stop_write_command(monkeypatch, tmp_path: Path) -> None:
    with _client(monkeypatch, tmp_path) as client:
        assert (
            client.post("/agents/control", json={"session_id": "s2", "verb": "resume"}).status_code
            == 200
        )
        assert ctl.poll_command(_workdir(tmp_path, "s2")) == "resume"
        assert (
            client.post("/agents/control", json={"session_id": "s2", "verb": "stop"}).status_code
            == 200
        )
        assert ctl.poll_command(_workdir(tmp_path, "s2")) == "stop"


def test_control_steer_writes_payload_and_command(monkeypatch, tmp_path: Path) -> None:
    with _client(monkeypatch, tmp_path) as client:
        resp = client.post(
            "/agents/control",
            json={"session_id": "s3", "verb": "steer", "text": "try dataset B", "mode": "next"},
        )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "next"
    wd = _workdir(tmp_path, "s3")
    assert ctl.poll_command(wd) == "steer"
    steer = ctl.read_steer(wd)
    assert steer == {"text": "try dataset B", "mode": "next"}


def test_control_steer_invalid_mode_falls_back_to_now(monkeypatch, tmp_path: Path) -> None:
    with _client(monkeypatch, tmp_path) as client:
        resp = client.post(
            "/agents/control",
            json={"session_id": "s4", "verb": "steer", "text": "go", "mode": "bogus"},
        )
    assert resp.status_code == 200
    assert resp.json()["mode"] == "now"
    assert ctl.read_steer(_workdir(tmp_path, "s4"))["mode"] == "now"


def test_control_steer_requires_text(monkeypatch, tmp_path: Path) -> None:
    with _client(monkeypatch, tmp_path) as client:
        resp = client.post(
            "/agents/control", json={"session_id": "s5", "verb": "steer", "text": "  "}
        )
    assert resp.status_code == 400


def test_control_inject_writes_payload_and_command(monkeypatch, tmp_path: Path) -> None:
    with _client(monkeypatch, tmp_path) as client:
        resp = client.post(
            "/agents/control", json={"session_id": "s6", "verb": "inject", "text": "hello"}
        )
    assert resp.status_code == 200
    wd = _workdir(tmp_path, "s6")
    assert ctl.poll_command(wd) == "inject"
    assert ctl.read_inject(wd) == "hello"


def test_control_unknown_verb_is_400(monkeypatch, tmp_path: Path) -> None:
    with _client(monkeypatch, tmp_path) as client:
        resp = client.post("/agents/control", json={"session_id": "s7", "verb": "explode"})
    assert resp.status_code == 400


def test_answer_writes_qid_matched_answer(monkeypatch, tmp_path: Path) -> None:
    wd = _workdir(tmp_path, "s8")
    ctl.write_question(wd, "q-1", "Which dataset?", "")
    with _client(monkeypatch, tmp_path) as client:
        resp = client.post(
            "/agents/answer", json={"session_id": "s8", "text": "MNIST", "qid": "q-1"}
        )
    assert resp.status_code == 200
    assert ctl.take_answer(wd, "q-1") == "MNIST"


def test_question_returns_204_when_none(monkeypatch, tmp_path: Path) -> None:
    with _client(monkeypatch, tmp_path) as client:
        resp = client.get("/agents/question/s9")
    assert resp.status_code == 204


def test_question_returns_pending_question(monkeypatch, tmp_path: Path) -> None:
    wd = _workdir(tmp_path, "s10")
    ctl.write_question(wd, "q-42", "Increase GPU?", "context here")
    with _client(monkeypatch, tmp_path) as client:
        resp = client.get("/agents/question/s10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["qid"] == "q-42"
    assert body["question"] == "Increase GPU?"
    assert body["context"] == "context here"
