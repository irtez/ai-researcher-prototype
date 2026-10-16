"""Remote workspace routing: local file I/O, no SSH or cloud allocation."""

import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mle_kit_mcp"))
from mle_kit_mcp.tools import remote_gpu as remote
from mle_kit_mcp.tools.text_editor import text_editor


@pytest.fixture
def transport(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(remote.settings, "WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(remote.GPU_LEASE, "hold", nullcontext)
    monkeypatch.setattr(
        remote, "get_instance", lambda: remote.InstanceInfo(0, remote_home="/srv/lab")
    )
    monkeypatch.setattr(remote, "_session_gpu_index", lambda *args: 0)
    monkeypatch.setattr(
        remote,
        "run_command",
        lambda inst, cmd, **kw: (
            calls.append(("command", cmd))
            or SimpleNamespace(returncode=0, stdout="ok", stderr="")
        ),
    )
    monkeypatch.setattr(
        remote,
        "send_rsync",
        lambda inst, src, dst: (
            calls.append(("upload", src, dst))
            or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )
    monkeypatch.setattr(
        remote,
        "recieve_rsync",
        lambda inst, src, dst: (
            calls.append(("download", src, dst))
            or SimpleNamespace(returncode=0, stdout="", stderr="")
        ),
    )
    return tmp_path, calls


def test_remote_commands_use_distinct_session_directories(transport):
    _, calls = transport
    remote.remote_bash("python run.py", session_id="one")
    remote.remote_bash("python run.py", session_id="two")
    assert "/srv/lab/sessions/one" in calls[0][1]
    assert "/srv/lab/sessions/two" in calls[1][1]
    assert "cd " in calls[0][1]


def test_upload_and_download_preserve_relative_layout(transport):
    root, calls = transport
    (root / "one/inputs").mkdir(parents=True)
    (root / "one/inputs/table.csv").write_text("x\n1\n")
    remote.remote_upload("inputs/table.csv", session_id="one")
    remote.remote_download("mle/results/result.json", session_id="one")
    assert (
        "upload",
        str(root / "one/inputs/table.csv"),
        "/srv/lab/sessions/one/inputs",
    ) in calls
    assert (
        "download",
        "/srv/lab/sessions/one/mle/results/result.json",
        str(root / "one/mle/results"),
    ) in calls


def test_remote_editor_uses_local_session_copy(transport):
    root, calls = transport
    editor = remote.create_remote_text_editor(text_editor)
    editor(
        command="write", path="mle/code/run.py", file_text="print(1)", session_id="one"
    )
    assert (root / "one/mle/code/run.py").read_text() == "print(1)"
    assert (
        "upload",
        str(root / "one/mle/code/run.py"),
        "/srv/lab/sessions/one/mle/code",
    ) in calls
    assert not (root / "mle").exists()


@pytest.mark.parametrize(
    "path", ["../other/file", "/etc/passwd", "a/../../b", "C:\\file"]
)
def test_invalid_transfer_paths_rejected_before_cloud_access(
    transport, monkeypatch, path
):
    monkeypatch.setattr(
        remote, "get_instance", lambda: pytest.fail("must not allocate")
    )
    with pytest.raises(ValueError):
        remote.remote_download(path, session_id="one")
    with pytest.raises(ValueError):
        remote.remote_upload(path, session_id="one")


def test_remote_command_failure_is_not_reported_as_success(transport, monkeypatch):
    monkeypatch.setattr(
        remote,
        "run_command",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=2, stdout="partial", stderr="failed"
        ),
    )
    with pytest.raises(ValueError, match="2.*partial.*failed"):
        remote.remote_bash("false", session_id="one")


def test_remote_proxy_uses_session_directory(transport, monkeypatch):
    from mle_kit_mcp.tools import llm_proxy

    root, calls = transport
    monkeypatch.setattr(llm_proxy.settings, "OPENROUTER_API_KEY", "fixture-key")
    monkeypatch.setattr(
        llm_proxy,
        "_remote_get_instance",
        lambda: remote.InstanceInfo(0, remote_home="/srv/lab"),
    )
    monkeypatch.setattr(llm_proxy, "_remote_send_rsync", remote.send_rsync)
    monkeypatch.setattr(
        llm_proxy, "_write_proxy_script", lambda path: path.write_text("fixture")
    )
    monkeypatch.setattr(llm_proxy, "_remote_run_command", remote.run_command)
    llm_proxy.llm_proxy_remote("one")
    assert ("upload", str(root / "one/llm_proxy.py"), "/srv/lab/sessions/one") in calls
    assert any(
        "cd /srv/lab/sessions/one" in item[1] for item in calls if item[0] == "command"
    )
