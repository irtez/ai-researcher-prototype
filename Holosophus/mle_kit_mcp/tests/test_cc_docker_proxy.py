"""Local LLM proxy stays beside Bash; no proxy/model requests are made."""

import importlib
import json
from types import SimpleNamespace

import pytest


@pytest.fixture
def proxy(tmp_path, monkeypatch):
    module = importlib.import_module("mle_kit_mcp.tools.llm_proxy")
    monkeypatch.setattr(module, "get_workspace_dir", lambda *args: tmp_path)
    (tmp_path / ".holosophus_ctl").mkdir()
    return module


def test_proxy_selects_current_cc_container(proxy, tmp_path, monkeypatch):
    (tmp_path / ".holosophus_ctl/sandbox.json").write_text(
        json.dumps({"executor": "claude_bash", "container": "current"})
    )
    container = SimpleNamespace(
        labels={"holosophus.cc.session": "session"}, status="running"
    )
    selected = []

    def get(identifier):
        selected.append(identifier)
        return container

    monkeypatch.setattr(
        proxy,
        "get_docker_client",
        lambda: SimpleNamespace(containers=SimpleNamespace(get=get)),
    )
    assert proxy._local_proxy_container("session") is container
    assert selected == ["current"]
    with pytest.raises(ValueError, match="belong"):
        proxy._local_proxy_container("different")
    container.status = "exited"
    with pytest.raises(RuntimeError, match="not running"):
        proxy._local_proxy_container("session")


def test_cc_proxy_does_not_fall_back_to_legacy_container(proxy, tmp_path, monkeypatch):
    marker = tmp_path / ".holosophus_ui/orchestrator"
    marker.parent.mkdir()
    marker.write_text("claude_code")
    monkeypatch.setattr(
        proxy, "get_container", lambda: pytest.fail("Unexpected legacy container")
    )
    with pytest.raises(RuntimeError, match="not ready"):
        proxy._local_proxy_container("session")


def test_legacy_proxy_keeps_its_original_executor(proxy, monkeypatch):
    legacy = object()
    monkeypatch.setattr(proxy, "get_container", lambda: legacy)
    assert proxy._local_proxy_container("session") is legacy
