"""Native Bash execution policy and supervisor, without a model."""

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import time
import threading
import zipfile
import shutil
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import docker_bash


def test_cancellation_during_preparation_waits_for_cleanup(tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()
    closed = []

    def prepare(self):
        started.set()
        assert release.wait(timeout=3)

    monkeypatch.setattr(docker_bash.DockerBashSession, "open", prepare)
    monkeypatch.setattr(
        docker_bash.DockerBashSession, "close", lambda self: closed.append(True)
    )

    async def scenario():
        async def run():
            async with docker_bash.executor_session(tmp_path):
                raise AssertionError("Cancelled preparation must not reach SDK")

        task = asyncio.create_task(run())
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert closed == [True]


def test_failed_preparation_releases_session_lock(tmp_path, monkeypatch):
    session = docker_bash.DockerBashSession(tmp_path, runtime_root=tmp_path / "runtime")

    def failure(self):
        raise RuntimeError("Docker failed")

    monkeypatch.setattr(docker_bash.DockerBashSession, "_prepare", failure)
    with pytest.raises(RuntimeError, match="Docker failed"):
        session.open()
    assert session.lock is None
    monkeypatch.setattr(docker_bash.DockerBashSession, "_prepare", lambda self: None)
    try:
        session.open()
        other = docker_bash.DockerBashSession(
            tmp_path, runtime_root=tmp_path / "runtime"
        )
        with pytest.raises(RuntimeError, match="active executor"):
            other.open()
        assert other.lock is None
    finally:
        session.close()


def test_gate_preserves_bash_but_blocks_duplicate_executor_and_host_writes(tmp_path):
    work = tmp_path / "session"
    work.mkdir()
    (work / "outside").symlink_to(tmp_path, target_is_directory=True)
    gate = docker_bash.ExecutionGate(work)

    def check(name, args):
        return asyncio.run(
            gate({"tool_name": name, "tool_input": args, "cwd": str(work)})
        )

    assert check("Bash", {"command": "python calculation.py"}) == {}
    assert check("Read", {"file_path": str(tmp_path / "instructions.md")}) == {}
    assert check("Write", {"file_path": str(work / "result.md")}) == {}
    for name, args in [
        ("mcp__mle_kit__bash", {"command": "true"}),
        ("Write", {"file_path": str(tmp_path / "runner.py")}),
        ("Edit", {"file_path": str(work / "outside/runner.py")}),
        ("NotebookEdit", {"notebook_path": "../runner.ipynb"}),
    ]:
        assert check(name, args)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_supervisor_returns_output_and_exit_code(tmp_path):
    proc = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "scripts/docker_bash_supervisor.py"),
            "printf output; printf failure >&2; exit 7",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.wait(timeout=3) == 7
    assert proc.stdout.read() == b"output"
    assert proc.stderr.read() == b"failure"
    proc.stdin.close()


def test_supervisor_stops_descendants_when_control_pipe_closes(tmp_path):
    started, leaked = tmp_path / "started", tmp_path / "leaked"
    command = f"touch '{started}'; (sleep 2; touch '{leaked}') & wait"
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts/docker_bash_supervisor.py"), command],
        stdin=subprocess.PIPE,
    )
    deadline = time.monotonic() + 3
    while not started.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert started.exists()
    proc.stdin.close()
    assert proc.wait(timeout=3) != 0
    time.sleep(2.1)
    assert not leaked.exists()


@pytest.mark.skipif(
    os.environ.get("RUN_DOCKER_TESTS") != "1", reason="explicit local Docker check"
)
def test_real_container_files_reuse_cancellation_and_session_cleanup(tmp_path):
    work = tmp_path / "session"
    work.mkdir()
    figures = tmp_path / "figures/paperbanana"
    figures.mkdir(parents=True)
    (figures / "image.txt").write_text("figure")
    (work / "figures").mkdir()
    (work / "figures/paperbanana").symlink_to("../../figures/paperbanana")
    with zipfile.ZipFile(work / "fixture_pkg-1.0-py3-none-any.whl", "w") as wheel:
        wheel.writestr("fixture_pkg.py", "value = 42\n")
        wheel.writestr(
            "fixture_pkg-1.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: fixture-pkg\nVersion: 1.0\n",
        )
        wheel.writestr(
            "fixture_pkg-1.0.dist-info/WHEEL",
            "Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        wheel.writestr("fixture_pkg-1.0.dist-info/RECORD", "")
    runtime = tmp_path / "runtime"
    session = docker_bash.DockerBashSession(work, runtime_root=runtime)

    def bash(command):
        return subprocess.run(
            [session.env["CLAUDE_CODE_SHELL_PREFIX"], command],
            cwd=work,
            env={**os.environ, **session.env},
            capture_output=True,
            text=True,
            timeout=10,
        )

    try:
        session.open()
        result = bash(
            "test -f /.dockerenv && python -c 'print(6*7)' && printf saved > result.txt"
        )
        assert result.returncode == 0, result.stderr
        assert "42" in result.stdout
        assert (work / "result.txt").read_text() == "saved"
        assert bash("printf persistent > /tmp/installed-marker").returncode == 0
        installed = bash(
            "python -m pip install --user --no-index --no-deps ./fixture_pkg-1.0-py3-none-any.whl"
        )
        assert installed.returncode == 0, installed.stderr
        assert bash("cat figures/paperbanana/image.txt").stdout.strip() == "figure"
        assert bash("echo bad > figures/paperbanana/image.txt").returncode != 0
        assert bash("cd /workdir/session && pwd -P").stdout.strip() == str(work)
        assert bash("exit 7").returncode == 7
        running = subprocess.Popen(
            [
                session.env["CLAUDE_CODE_SHELL_PREFIX"],
                "touch started; sleep 3; touch leaked",
            ],
            cwd=work,
            env={**os.environ, **session.env},
        )
        deadline = time.monotonic() + 5
        while not (work / "started").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (work / "started").exists()
        running.terminate()
        assert running.wait(timeout=4) != 0
        time.sleep(3.1)
        assert not (work / "leaked").exists()
        session.close()
        assert bash("touch outside_run").returncode != 0
        session.open()
        assert bash("test -f /tmp/installed-marker").returncode == 0
        assert (
            bash(
                "python -c 'import fixture_pkg; print(fixture_pkg.value)'"
            ).stdout.strip()
            == "42"
        )
        running = subprocess.Popen(
            [
                session.env["CLAUDE_CODE_SHELL_PREFIX"],
                "touch started2; sleep 4; touch leaked2",
            ],
            cwd=work,
            env={**os.environ, **session.env},
        )
        deadline = time.monotonic() + 5
        while not (work / "started2").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (work / "started2").exists()
        session.close()
        assert running.wait(timeout=4) != 0
        assert not (work / "leaked2").exists()
    finally:
        session.close()
        subprocess.run(["docker", "rm", "-f", session.name], capture_output=True)


@pytest.mark.skipif(
    os.environ.get("RUN_DOCKER_TESTS") != "1", reason="explicit local Docker check"
)
def test_pause_is_scoped_to_one_session(tmp_path):
    import cc_run_control as control

    sessions = []
    try:
        for name in ("first", "second"):
            work = tmp_path / name
            work.mkdir()
            session = docker_bash.DockerBashSession(
                work, runtime_root=tmp_path / "runtime"
            )
            sessions.append(session)
            session.open()
        first, second = sessions
        assert control.freeze_sandbox(first.workdir)
        result = subprocess.run(
            [second.env["CLAUDE_CODE_SHELL_PREFIX"], "echo independent"],
            cwd=second.workdir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0 and result.stdout.strip() == "independent"
        assert control.unfreeze_sandbox(first.workdir)
        result = subprocess.run(
            [first.env["CLAUDE_CODE_SHELL_PREFIX"], "echo resumed"],
            cwd=first.workdir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0 and result.stdout.strip() == "resumed"
    finally:
        for session in sessions:
            session.close()
            subprocess.run(["docker", "rm", "-f", session.name], capture_output=True)


@pytest.mark.skipif(os.environ.get("RUN_DOCKER_TESTS") != "1", reason="explicit local Docker check")
def test_host_file_helpers_keep_host_path_while_bash_uses_container_path(tmp_path, monkeypatch):
    work = tmp_path / "session"
    work.mkdir()
    host_bin = tmp_path / "host-tools"
    host_bin.mkdir()
    helper = host_bin / "fixture-pdf-helper"
    helper.write_text("#!/bin/sh\nprintf host-pdf-helper")
    helper.chmod(0o755)
    untrusted = work / "bin"
    untrusted.mkdir()
    (untrusted / helper.name).write_text("#!/bin/sh\nprintf untrusted")
    (untrusted / helper.name).chmod(0o755)
    alias = tmp_path / "untrusted-alias"
    alias.symlink_to(untrusted, target_is_directory=True)
    mutable_alias = work / "trusted-link"
    mutable_alias.symlink_to(host_bin, target_is_directory=True)
    original = os.environ.get("PATH", os.defpath)
    monkeypatch.setenv("PATH", f"{untrusted}:{alias}:{mutable_alias}::.:{host_bin}:{original}")
    session = docker_bash.DockerBashSession(work, runtime_root=tmp_path / "runtime")
    try:
        session.open()
        path = session.env["PATH"].split(os.pathsep)
        assert str(untrusted) not in path and str(alias) not in path
        mutable_alias.unlink()
        mutable_alias.symlink_to(untrusted, target_is_directory=True)
        assert "" not in path and "." not in path
        assert shutil.which(helper.name, path=session.env["PATH"]) == str(helper)
        result = subprocess.run([helper.name], cwd=work, env={**os.environ, **session.env}, capture_output=True, text=True)
        assert result.stdout == "host-pdf-helper"
        result = subprocess.run([session.env["CLAUDE_CODE_SHELL_PREFIX"], "test -f /.dockerenv && command -v python && ! command -v fixture-pdf-helper"], cwd=work, env={**os.environ, **session.env}, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
    finally:
        session.close()
        subprocess.run(["docker", "rm", "-f", session.name], capture_output=True)


@pytest.mark.skipif(os.environ.get("RUN_DOCKER_TESTS") != "1", reason="explicit local Docker check")
def test_default_image_update_preserves_existing_session_and_applies_to_new_sessions(tmp_path, monkeypatch):
    import json

    previous = os.environ.get("CC_EXECUTOR_IMAGE", docker_bash.DEFAULT_IMAGE)
    next_image = "holo-executor-test:" + uuid.uuid4().hex
    subprocess.run(["docker", "tag", previous, next_image], check=True)
    monkeypatch.setenv("LOCAL_GPU_DEVICES", "")
    sessions = []
    try:
        old_work = tmp_path / "old-session"
        old_work.mkdir()
        old = docker_bash.DockerBashSession(old_work, runtime_root=tmp_path / "runtime")
        sessions.append(old)
        old.open()
        old_id = json.loads(subprocess.check_output(["docker", "inspect", old.name]))[0]["Id"]
        subprocess.run([old.env["CLAUDE_CODE_SHELL_PREFIX"], "printf retained > /tmp/preserved-package"], cwd=old_work, check=True)
        old.close()
        monkeypatch.setenv("CC_EXECUTOR_IMAGE", next_image)
        old.open()
        assert json.loads(subprocess.check_output(["docker", "inspect", old.name]))[0]["Id"] == old_id
        result = subprocess.run([old.env["CLAUDE_CODE_SHELL_PREFIX"], "cat /tmp/preserved-package"], cwd=old_work, capture_output=True, text=True, check=True)
        assert result.stdout == "retained"
        assert json.loads((old_work / ".holosophus_ctl/sandbox.json").read_text())["image"] == previous
        old.close()

        new_work = tmp_path / "new-session"
        new_work.mkdir()
        new = docker_bash.DockerBashSession(new_work, runtime_root=tmp_path / "runtime")
        sessions.append(new)
        new.open()
        assert json.loads((new_work / ".holosophus_ctl/sandbox.json").read_text())["image"] == next_image
        new.close()

        # Retaining an old image does not bypass the GPU/runtime configuration guard.
        monkeypatch.setenv("LOCAL_GPU_DEVICES", "0")
        with pytest.raises(RuntimeError, match="configuration changed"):
            old.open()
    finally:
        for session in sessions:
            session.close()
            subprocess.run(["docker", "rm", "-f", session.name], capture_output=True)
        subprocess.run(["docker", "image", "rm", next_image], capture_output=True)
