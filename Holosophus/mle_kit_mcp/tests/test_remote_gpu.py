"""SC6 (Selectel provider spec): the one-GPU hard cap is enforced in the shared
``run_command`` layer, so *both* launch paths inherit it.

The enforcement point is a single choke: ``remote_bash`` -> ``run_command``.
Both orchestrators reach the identical ``remote_bash`` symbol -- the multi-user
UI / CodeArkt path and the ``scripts/run_research.py`` CLI path -- so there is
no per-orchestrator branch to keep in sync. A session's remote command always
carries exactly one ``CUDA_VISIBLE_DEVICES=<index>`` pin, meaning a session can
never see more than one card.

The SSH transport (``subprocess.run``) and ``get_instance`` are faked, so these
run without a live Selectel host. ``nvidia-smi`` occupancy discovery is faked as
canned CSV; the *actual* session command is captured and asserted to carry the
single pin.
"""

import os
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Set

import pytest

from mle_kit_mcp import gpu_registry
from mle_kit_mcp.tools import remote_gpu


def _plain_instance() -> remote_gpu.InstanceInfo:
    return remote_gpu.InstanceInfo(
        instance_id=0,
        ip="203.0.113.9",
        port=22,
        username="m.mozikov",
        ssh_key_path="/root/.ssh/selectel_key",
        remote_home="/home/m.mozikov",
    )


def _fake_subprocess_run(free: Set[int], session_cmds: List[str]) -> Any:
    """Fake ``subprocess.run`` for a shared 8-card host.

    ``--query-gpu`` commands are occupancy probes (host-wide or ``-i N`` point
    re-probe) and answer with canned CSV: a card in ``free`` reads free (``5``
    MiB, ``0``% util), everything else reads busy (external tenant). Any other
    command is the *session's real command* -- it must arrive pinned, so it is
    recorded into ``session_cmds`` for assertion.
    """
    free_set = set(free)

    def fake_run(cmd: List[str], **kwargs: Any) -> SimpleNamespace:
        remote = cmd[-1]
        if "--query-gpu" in remote:
            if " -i " in remote:
                idx = int(remote.rsplit(" -i ", 1)[1].strip())
                row = f"{idx}, 5, 0" if idx in free_set else f"{idx}, 71000, 100"
                return SimpleNamespace(returncode=0, stdout=row, stderr="")
            rows = [(f"{i}, 5, 0" if i in free_set else f"{i}, 71000, 100") for i in range(8)]
            return SimpleNamespace(returncode=0, stdout="\n".join(rows), stderr="")
        session_cmds.append(remote)
        return SimpleNamespace(
            returncode=0,
            stdout="GPU 0: NVIDIA B300 SXM6 AC (UUID: GPU-abc123)",
            stderr="",
        )

    return fake_run


@pytest.fixture(autouse=True)
def _clean_registry() -> Any:
    gpu_registry._gpu_index_by_session.clear()
    yield
    gpu_registry._gpu_index_by_session.clear()


@pytest.fixture
def _selectel(monkeypatch: pytest.MonkeyPatch) -> Any:
    # Deployment-time provider switch. The Literal gains "selectel" in SC1;
    # validate_assignment is off, so this monkeypatch stands in until then.
    monkeypatch.setattr(remote_gpu.settings, "REMOTE_PROVIDER", "selectel")
    yield


# --- run_command is the single enforcement choke point (SC6 structural) ------


def test_run_command_injects_exactly_one_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: Dict[str, str] = {}

    def fake_run(cmd: List[str], **kwargs: Any) -> SimpleNamespace:
        captured["remote"] = cmd[-1]
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(remote_gpu.subprocess, "run", fake_run)
    remote_gpu.run_command(_plain_instance(), "nvidia-smi -L", gpu_index=3)

    remote = captured["remote"]
    # Exactly one card pinned, and the original command is preserved after it.
    assert remote.count("CUDA_VISIBLE_DEVICES=") == 1
    assert "CUDA_VISIBLE_DEVICES=3" in remote
    assert remote.endswith("nvidia-smi -L")


def test_run_command_is_unpinned_without_index(monkeypatch: pytest.MonkeyPatch) -> None:
    # Discovery and SSH probes pass no index -- they must see the whole host,
    # so run_command must NOT inject a pin when gpu_index is None.
    captured: Dict[str, str] = {}

    def fake_run(cmd: List[str], **kwargs: Any) -> SimpleNamespace:
        captured["remote"] = cmd[-1]
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(remote_gpu.subprocess, "run", fake_run)
    remote_gpu.run_command(_plain_instance(), gpu_registry.NVIDIA_SMI_QUERY)

    assert "CUDA_VISIBLE_DEVICES" not in captured["remote"]


# --- both launch paths inherit the cap via the same remote_bash (SC6) --------


def test_both_launch_paths_pin_one_card_each(
    monkeypatch: pytest.MonkeyPatch, _selectel: Any
) -> None:
    # A run started from the multi-user UI (CodeArkt path) and a run started
    # from scripts/run_research.py (CLI path) both funnel through the identical
    # remote_bash -> run_command. Distinct session_ids stand in for the two
    # paths; the enforcement code is the same (no per-orchestrator branch),
    # which is exactly the point of putting the cap in shared mle_kit.
    session_cmds: List[str] = []
    monkeypatch.setattr(remote_gpu, "get_instance", lambda: _plain_instance())
    monkeypatch.setattr(remote_gpu.subprocess, "run", _fake_subprocess_run({2, 3, 4}, session_cmds))

    remote_gpu.remote_bash("nvidia-smi -L", session_id="ui-run-1")
    remote_gpu.remote_bash("nvidia-smi -L", session_id="cli-run-1")

    # Each path's real command carried exactly one pin.
    assert len(session_cmds) == 2
    assert all(c.count("CUDA_VISIBLE_DEVICES=") == 1 for c in session_cmds)

    ui_idx = gpu_registry._gpu_index_by_session["ui-run-1"]
    cli_idx = gpu_registry._gpu_index_by_session["cli-run-1"]
    assert f"CUDA_VISIBLE_DEVICES={ui_idx}" in session_cmds[0]
    assert f"CUDA_VISIBLE_DEVICES={cli_idx}" in session_cmds[1]

    # Two paths -> two distinct free cards, each session pinned to <= 1 GPU.
    assert ui_idx != cli_idx
    assert {ui_idx, cli_idx} <= {2, 3, 4}


def test_blank_session_id_still_pins_one_card(
    monkeypatch: pytest.MonkeyPatch, _selectel: Any
) -> None:
    # SC6 hard cap does not depend on session identity: even a blank session_id
    # gets exactly one free card pinned (fallback), so the session still sees
    # <= 1 GPU. The fallback lease is not tracked in the registry (it cannot be
    # released by id).
    session_cmds: List[str] = []
    monkeypatch.setattr(remote_gpu, "get_instance", lambda: _plain_instance())
    monkeypatch.setattr(remote_gpu.subprocess, "run", _fake_subprocess_run({5}, session_cmds))

    remote_gpu.remote_bash("nvidia-smi -L", session_id="")

    assert len(session_cmds) == 1
    assert session_cmds[0].count("CUDA_VISIBLE_DEVICES=") == 1
    assert "CUDA_VISIBLE_DEVICES=5" in session_cmds[0]
    assert gpu_registry._gpu_index_by_session == {}


def test_single_card_provider_pins_index_zero_without_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Uniform path: single-card providers (vast/cloudru) always pin index 0 --
    # a no-op that keeps run_command provider-agnostic -- and issue NO occupancy
    # discovery (only Selectel's shared host needs it).
    monkeypatch.setattr(remote_gpu.settings, "REMOTE_PROVIDER", "cloudru")
    all_remote: List[str] = []

    def fake_run(cmd: List[str], **kwargs: Any) -> SimpleNamespace:
        all_remote.append(cmd[-1])
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(remote_gpu, "get_instance", lambda: _plain_instance())
    monkeypatch.setattr(remote_gpu.subprocess, "run", fake_run)

    remote_gpu.remote_bash("nvidia-smi -L", session_id="s1")

    assert not any("--query-gpu" in r for r in all_remote)
    assert len(all_remote) == 1
    assert all_remote[0].count("CUDA_VISIBLE_DEVICES=") == 1
    assert "CUDA_VISIBLE_DEVICES=0" in all_remote[0]


# --- SC1: the Selectel provider foundation build_selectel_instance -----------


def _set_selectel_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remote_gpu.settings, "SELECTEL_SSH_HOST", "193.39.168.186")
    monkeypatch.setattr(remote_gpu.settings, "SELECTEL_SSH_PORT", 22)
    monkeypatch.setattr(remote_gpu.settings, "SELECTEL_SSH_USER", "m.mozikov")
    monkeypatch.setattr(remote_gpu.settings, "SELECTEL_SSH_KEY", "/root/.ssh/selectel_key")
    monkeypatch.setattr(remote_gpu.settings, "SELECTEL_REMOTE_HOME", "/home/m.mozikov")


def test_usable_private_key_passes_through_a_private_key(tmp_path: Any) -> None:
    key = tmp_path / "id_ok"
    key.write_text("KEY")
    key.chmod(0o600)
    # Already private: used as-is, so the Linux path stays a no-op.
    assert remote_gpu.usable_private_key(str(key)) == str(key)


def test_usable_private_key_copies_a_world_readable_key(tmp_path: Any) -> None:
    key = tmp_path / "id_open"
    key.write_text("KEY")
    key.chmod(0o777)
    # A Windows bind-mount lands the key like this and ssh would refuse it.
    result = remote_gpu.usable_private_key(str(key))
    assert result != str(key)
    assert Path(result).read_text() == "KEY"
    assert stat.S_IMODE(os.stat(result).st_mode) == 0o600


def test_usable_private_key_leaves_non_files_alone() -> None:
    # compose mounts /dev/null when no key is configured; the caller's own
    # missing-key diagnostics must still be what fires.
    assert remote_gpu.usable_private_key("/dev/null") == "/dev/null"
    assert remote_gpu.usable_private_key("") == ""


def test_build_selectel_instance_happy_builds_instance_and_probes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # SC1 (happy): a valid SELECTEL_* env yields an InstanceInfo for
    # 193.39.168.186:22 / m.mozikov / /home/m.mozikov, and the SSH echo-probe
    # returns `selectel-ok`. The un-pinned probe command is the whole point:
    # build/discovery must see the host, not a single pinned card.
    _set_selectel_env(monkeypatch)
    # Isolate the SC5 capacity reconcile (mutates the global GPU_LEASE) from the
    # provider build under test.
    monkeypatch.setattr(remote_gpu, "_reconcile_gpu_slots", lambda inst: None)
    # Likewise isolate key-permission normalisation: on a host where the
    # configured key actually exists (a real deployment, or CI inside the mle_kit
    # image) it returns a 0600 copy, which has nothing to do with what this test
    # asserts. Its own behaviour is covered by the usable_private_key tests.
    monkeypatch.setattr(remote_gpu, "usable_private_key", lambda path: path)

    probes: List[str] = []

    def fake_run(cmd: List[str], **kwargs: Any) -> SimpleNamespace:
        probes.append(cmd[-1])
        return SimpleNamespace(returncode=0, stdout="selectel-ok", stderr="")

    monkeypatch.setattr(remote_gpu.subprocess, "run", fake_run)

    info = remote_gpu.build_selectel_instance()

    assert info.ip == "193.39.168.186"
    assert info.port == 22
    assert info.username == "m.mozikov"
    assert info.remote_home == "/home/m.mozikov"
    assert info.gpu_name == "selectel"
    assert info.ssh_key_path == "/root/.ssh/selectel_key"
    # The probe ran, and it ran un-pinned (no CUDA_VISIBLE_DEVICES injected).
    assert probes and "echo selectel-ok" in probes[-1]
    assert all("CUDA_VISIBLE_DEVICES" not in p for p in probes)


def test_build_selectel_instance_missing_env_errors_without_vast_fallthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # SC1 (edge): missing required SELECTEL_* env raises a clear error listing
    # every missing key, and never falls through to the Vast branch -- get_sdk
    # must not be reached on the selectel path.
    monkeypatch.setattr(remote_gpu.settings, "SELECTEL_SSH_HOST", None)
    monkeypatch.setattr(remote_gpu.settings, "SELECTEL_SSH_USER", None)
    monkeypatch.setattr(remote_gpu.settings, "SELECTEL_SSH_KEY", None)

    def _boom() -> Any:
        raise AssertionError("vast get_sdk must not be reached on the selectel branch")

    monkeypatch.setattr(remote_gpu, "get_sdk", _boom)

    with pytest.raises(AssertionError) as exc_info:
        remote_gpu.build_selectel_instance()

    message = str(exc_info.value)
    assert "SELECTEL_SSH_HOST" in message
    assert "SELECTEL_SSH_USER" in message
    assert "SELECTEL_SSH_KEY" in message
