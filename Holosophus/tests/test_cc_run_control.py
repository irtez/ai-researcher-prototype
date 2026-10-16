"""Unit tests for scripts/cc_run_control.py — the pause/restart control channel.

Covers the SC3/SC4/SC6 file primitives in isolation (no SDK, no asyncio):
- durable session bookkeeping round-trip + missing/corrupt tolerance (SC3, SC5-fallback),
- the pause/resume/stop command channel: send, consume-once, garbage/empty tolerance (SC1, SC6),
- the Tier-2 checkpoint-stop flag lifecycle (SC4),
- the Tier-1 sandbox freeze descriptor round-trip + corrupt tolerance (SC6 forward-compat seam).
Design: docs/plans/2026-08-12-pause-restart-cc.md.
"""

import sys
import uuid
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import cc_run_control as ctl  # noqa: E402


# --- session id ---------------------------------------------------------------

def test_new_session_id_is_a_uuid() -> None:
    sid = ctl.new_session_id()
    # Must round-trip through uuid.UUID (the SDK --session-id contract requires a UUID).
    assert str(uuid.UUID(sid)) == sid


# --- durable session bookkeeping ----------------------------------------------

def test_session_info_round_trip(tmp_path: Path) -> None:
    sid = ctl.new_session_id()
    ctl.write_session_info(tmp_path, sid, "collect baselines", created="2026-01-01T00:00:00")
    info = ctl.read_session_info(tmp_path)
    assert info is not None
    assert info["session_id"] == sid
    assert info["question"] == "collect baselines"
    assert info["workdir"] == str(tmp_path)
    assert info["created"] == "2026-01-01T00:00:00"


def test_session_info_stamps_created_when_omitted(tmp_path: Path) -> None:
    ctl.write_session_info(tmp_path, ctl.new_session_id(), "q")
    info = ctl.read_session_info(tmp_path)
    assert info is not None and info["created"]  # some ISO timestamp got stamped


def test_read_session_info_missing_is_none(tmp_path: Path) -> None:
    assert ctl.read_session_info(tmp_path) is None


def test_read_session_info_corrupt_is_none(tmp_path: Path) -> None:
    d = ctl.ctl_dir(tmp_path)
    (d / ctl.SESSION_FILENAME).write_text("{not json", encoding="utf-8")
    assert ctl.read_session_info(tmp_path) is None


def test_read_session_info_without_id_is_none(tmp_path: Path) -> None:
    d = ctl.ctl_dir(tmp_path)
    (d / ctl.SESSION_FILENAME).write_text('{"workdir": "x"}', encoding="utf-8")
    assert ctl.read_session_info(tmp_path) is None


# --- command channel ----------------------------------------------------------

def test_send_and_poll_consumes_once(tmp_path: Path) -> None:
    ctl.send_command(tmp_path, ctl.CMD_PAUSE)
    assert ctl.poll_command(tmp_path) == "pause"
    # Read-then-clear: a second poll sees nothing.
    assert ctl.poll_command(tmp_path) is None


def test_poll_missing_is_none(tmp_path: Path) -> None:
    assert ctl.poll_command(tmp_path) is None


def test_poll_garbage_is_none_and_cleared(tmp_path: Path) -> None:
    d = ctl.ctl_dir(tmp_path)
    (d / ctl.COMMAND_FILENAME).write_text("frobnicate", encoding="utf-8")
    assert ctl.poll_command(tmp_path) is None
    # Garbage must not wedge the channel — the file is cleared even when invalid.
    assert not (d / ctl.COMMAND_FILENAME).exists()


def test_poll_is_whitespace_and_case_tolerant(tmp_path: Path) -> None:
    d = ctl.ctl_dir(tmp_path)
    (d / ctl.COMMAND_FILENAME).write_text("  RESUME\n", encoding="utf-8")
    assert ctl.poll_command(tmp_path) == "resume"


def test_send_rejects_unknown_command(tmp_path: Path) -> None:
    try:
        ctl.send_command(tmp_path, "explode")
    except ValueError:
        pass
    else:  # pragma: no cover - the assertion is the failure signal
        raise AssertionError("send_command must reject an unknown command")


def test_all_three_commands_round_trip(tmp_path: Path) -> None:
    for cmd in (ctl.CMD_PAUSE, ctl.CMD_RESUME, ctl.CMD_STOP):
        ctl.send_command(tmp_path, cmd)
        assert ctl.poll_command(tmp_path) == cmd


# --- Tier-2 checkpoint-stop flag ----------------------------------------------

def test_checkpoint_stop_lifecycle(tmp_path: Path) -> None:
    assert ctl.is_checkpoint_stop(tmp_path) is False
    ctl.set_checkpoint_stop(tmp_path)
    assert ctl.is_checkpoint_stop(tmp_path) is True
    ctl.clear_checkpoint_stop(tmp_path)
    assert ctl.is_checkpoint_stop(tmp_path) is False


def test_clear_checkpoint_stop_when_absent_is_noop(tmp_path: Path) -> None:
    ctl.clear_checkpoint_stop(tmp_path)  # must not raise
    assert ctl.is_checkpoint_stop(tmp_path) is False


# --- Tier-1 sandbox freeze descriptor -----------------------------------------

def test_sandbox_descriptor_round_trip(tmp_path: Path) -> None:
    ctl.write_sandbox_descriptor(tmp_path, {"container": "abc123"})
    assert ctl.read_sandbox_descriptor(tmp_path) == {"container": "abc123"}


def test_sandbox_descriptor_missing_is_none(tmp_path: Path) -> None:
    assert ctl.read_sandbox_descriptor(tmp_path) is None


def test_sandbox_descriptor_corrupt_is_none(tmp_path: Path) -> None:
    d = ctl.ctl_dir(tmp_path)
    (d / ctl.SANDBOX_FILENAME).write_text("[]", encoding="utf-8")  # not an object
    assert ctl.read_sandbox_descriptor(tmp_path) is None


# --- control dir is per-session (session-keyed, not global) --------------------

def test_control_dirs_are_isolated_per_workdir(tmp_path: Path) -> None:
    a, b = tmp_path / "sessA", tmp_path / "sessB"
    ctl.send_command(a, ctl.CMD_PAUSE)
    # Pausing session A must not touch session B's channel.
    assert ctl.poll_command(b) is None
    assert ctl.poll_command(a) == "pause"


# --- Tier-1 freeze (docker pause/unpause via an injected runner) ---------------

class _FakeProc:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode


def _recording_runner(calls: list, returncode: int = 0):
    def runner(args):
        calls.append(args)
        return _FakeProc(returncode)

    return runner


def test_freeze_calls_docker_pause(tmp_path: Path) -> None:
    ctl.write_sandbox_descriptor(tmp_path, {"container": "cid123"})
    calls: list = []
    assert ctl.freeze_sandbox(tmp_path, runner=_recording_runner(calls)) is True
    assert calls == [["docker", "pause", "cid123"]]


def test_unfreeze_calls_docker_unpause(tmp_path: Path) -> None:
    ctl.write_sandbox_descriptor(tmp_path, {"container": "cid123"})
    calls: list = []
    assert ctl.unfreeze_sandbox(tmp_path, runner=_recording_runner(calls)) is True
    assert calls == [["docker", "unpause", "cid123"]]


def test_freeze_without_descriptor_makes_no_docker_call(tmp_path: Path) -> None:
    calls: list = []
    assert ctl.freeze_sandbox(tmp_path, runner=_recording_runner(calls)) is False
    assert calls == []  # nothing to freeze → no docker invocation


def test_freeze_nonzero_returncode_is_false(tmp_path: Path) -> None:
    ctl.write_sandbox_descriptor(tmp_path, {"container": "cid"})
    assert ctl.freeze_sandbox(tmp_path, runner=lambda a: _FakeProc(1)) is False


def test_freeze_runner_exception_is_false(tmp_path: Path) -> None:
    ctl.write_sandbox_descriptor(tmp_path, {"container": "cid"})

    def boom(args):
        raise RuntimeError("docker not found")

    assert ctl.freeze_sandbox(tmp_path, runner=boom) is False
