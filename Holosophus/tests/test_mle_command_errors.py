"""Execution status is independent of whether stdout or stderr is populated."""

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mle_kit_mcp"))
from mle_kit_mcp import files


@pytest.mark.parametrize(
    "code,stdout,stderr",
    [
        (7, b"partial output", b"calculation failed"),
        (127, b"OCI runtime exec failed", b""),
        (124, b"started", b""),
        (137, b"", b"killed"),
        (0, b"result", b"warning only"),
        (0, b"", b""),
    ],
)
def test_command_status_preserves_output(tmp_path, monkeypatch, code, stdout, stderr):
    module = importlib.import_module("mle_kit_mcp.tools.bash")
    monkeypatch.setattr(files.settings, "WORKSPACE_DIR", str(tmp_path))

    class Container:
        id = "fake"

        def exec_run(self, *args, **kwargs):
            return SimpleNamespace(exit_code=code, output=(stdout, stderr))

    monkeypatch.setattr(module, "get_container", Container)
    if code:
        with pytest.raises(RuntimeError, match=f"exit code {code}") as error:
            module.bash("command", session_id="test-session")
        text = str(error.value)
        if code == 124:
            assert "timed out" in text
        if code == 137:
            assert "was killed" in text
    else:
        text = module.bash("command", session_id="test-session")
    assert stdout.decode() in text and stderr.decode() in text
