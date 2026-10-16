"""Native files and MCP execution resolve the same session without an LLM."""

import asyncio
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mle_kit_mcp"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from mle_kit_mcp import files
from session_tools import MleSessionToolGate


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(files.settings, "WORKSPACE_DIR", str(tmp_path))
    return tmp_path


@pytest.mark.parametrize(
    "sid", ["research_01abc", "research__сравнение_молекул__sonnet"]
)
def test_execution_and_editing_use_native_directory(workspace, monkeypatch, sid):
    module = importlib.import_module("mle_kit_mcp.tools.bash")
    editor = importlib.import_module("mle_kit_mcp.tools.text_editor")
    root = workspace / sid
    (root / "mle").mkdir(parents=True)
    (root / "mle/code.py").write_text("print('example')")
    captured = []

    class Container:
        id = "fake"

        def exec_run(self, command, **kwargs):
            captured.append(kwargs)
            return SimpleNamespace(exit_code=0, output=(b"example", b""))

    monkeypatch.setattr(module, "get_container", Container)
    assert "example" in module.bash("python code.py", cwd="mle", session_id=sid)
    assert captured[-1]["workdir"] == f"/workdir/{sid}/mle"
    assert captured[-1]["environment"]["HOME"] == f"/workdir/{sid}"
    assert (root / ".holosophus_ctl/sandbox.json").is_file()
    assert "example" in editor.text_editor("view", "mle/code.py", session_id=sid)
    module.bash("pwd", cwd=f"/workdir/{sid}/mle", session_id=sid)
    assert captured[-1]["workdir"] == f"/workdir/{sid}/mle"


def test_existing_duplicate_is_not_merged_or_selected(workspace):
    original = workspace / "сессия_1"
    legacy = workspace / "__1"
    original.mkdir()
    legacy.mkdir()
    (legacy / "old.txt").write_text("preserve")
    assert files.get_workspace_dir("сессия_1") == original
    assert (legacy / "old.txt").read_text() == "preserve"
    assert not (original / "old.txt").exists()


@pytest.mark.parametrize(
    "sid", ["../other", "/tmp/other", "..", "a/b", "a\\b", "a;echo hi"]
)
def test_invalid_session_ids_are_rejected(workspace, sid):
    with pytest.raises(ValueError):
        files.get_workspace_dir(sid)


def test_cwd_cannot_escape_session(workspace):
    for value in ["../other", "/workdir/other", "a/../../other"]:
        with pytest.raises(ValueError):
            files.resolve_session_path("session", value)
    (workspace / "outside").mkdir()
    (workspace / "session/link").symlink_to(workspace / "outside")
    with pytest.raises(ValueError):
        files.resolve_session_path("session", "link")


@pytest.mark.parametrize("name,key", [("bash", "cwd"), ("text_editor", "path")])
def test_common_hook_binds_session_and_translates_host_paths(workspace, name, key):
    gate = MleSessionToolGate("сессия", workspace / "сессия")
    output = asyncio.run(
        gate(
            {
                "tool_name": "mcp__mle_kit__" + name,
                "tool_input": {
                    "session_id": "other",
                    key: str(workspace / "сессия/mle"),
                },
            },
            None,
            None,
        )
    )
    assert output["hookSpecificOutput"]["updatedInput"] == {
        "session_id": "сессия",
        key: "mle",
    }
    assert asyncio.run(gate({"tool_name": "Read", "tool_input": {}}, None, None)) == {}
