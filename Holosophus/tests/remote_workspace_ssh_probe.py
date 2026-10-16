"""Opt-in real SSH/rsync against an ephemeral local Docker node, no cloud/LLM."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
from contextlib import nullcontext
from unittest.mock import patch

if os.environ.get("RUN_DOCKER_TESTS") != "1":
    raise SystemExit(
        "Set RUN_DOCKER_TESTS=1; this starts a disposable local SSH container"
    )
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mle_kit_mcp"))
from mle_kit_mcp.tools import remote_gpu as remote
from mle_kit_mcp.tools.text_editor import text_editor

run = subprocess.run
with tempfile.TemporaryDirectory(prefix="remote-session-ssh-") as directory:
    work = Path(directory)
    key = work / "key"
    run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key)],
        check=True,
        capture_output=True,
    )
    public = key.with_suffix(".pub").read_text().strip()
    boot = (
        "apk add --no-cache openssh-server >/dev/null 2>&1 && ssh-keygen -A >/dev/null 2>&1 && "
        "mkdir -p /root/.ssh /srv/lab && chmod 700 /root/.ssh && "
        f"printf '%s\\n' {shlex.quote(public)} > /root/.ssh/authorized_keys && "
        "chmod 600 /root/.ssh/authorized_keys && "
        "exec /usr/sbin/sshd -D -e -o PermitRootLogin=prohibit-password -o PasswordAuthentication=no"
    )
    container = run(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "-p",
            "127.0.0.1::22",
            "--entrypoint",
            "sh",
            "ai-researcher-claude-mle_kit",
            "-c",
            boot,
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    try:
        port = (
            run(
                ["docker", "port", container, "22"],
                check=True,
                capture_output=True,
                text=True,
            )
            .stdout.strip()
            .rsplit(":", 1)[1]
        )
        instance = remote.InstanceInfo(
            0,
            ip="127.0.0.1",
            port=int(port),
            username="root",
            ssh_key_path=str(key),
            remote_home="/srv/lab",
        )

        def isolated_run(command, **kwargs):
            command = list(command)
            known = f"UserKnownHostsFile={work / 'known_hosts'}"
            if command[0] in {"ssh", "scp"}:
                command[1:1] = ["-o", known]
            elif command[0] == "rsync":
                index = command.index("-e") + 1
                command[index] += " -o " + shlex.quote(known)
            return run(command, **kwargs)

        with (
            patch.object(remote.subprocess, "run", isolated_run),
            patch.object(remote, "get_instance", return_value=instance),
            patch.object(remote, "_session_gpu_index", return_value=0),
            patch.object(remote.GPU_LEASE, "hold", nullcontext),
            patch.object(remote.settings, "WORKSPACE_DIR", str(work / "local")),
        ):
            for _ in range(45):
                response = remote.run_command(instance, "echo ready", timeout=2)
                if response.returncode == 0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("Local SSH container did not become ready")
            for sid, value in [("one", 7), ("two", 11)]:
                inputs = work / "local" / sid / "inputs"
                inputs.mkdir(parents=True)
                filename = "inputs/table 'quoted'.txt"
                (work / "local" / sid / filename).write_text(str(value))
                remote.remote_upload(filename, sid)
                editor = remote.create_remote_text_editor(text_editor)
                editor(
                    command="write",
                    path="mle/code/calc.py",
                    session_id=sid,
                    file_text="from pathlib import Path\nvalue=int(Path(\"inputs/table 'quoted'.txt\").read_text())\nPath('mle/results').mkdir(parents=True, exist_ok=True)\nPath('mle/results/out.txt').write_text(str(value*2))\nPath('mle/results/large.bin').write_bytes(b'x'*(11*1024*1024))\n",
                )
                remote.remote_bash("python3 mle/code/calc.py", session_id=sid)
                remote.remote_download("mle/results/out.txt", sid)
                remote.remote_download("mle/results/large.bin", sid)
                assert (
                    work / "local" / sid / "mle/results/out.txt"
                ).read_text() == str(value * 2)
                assert (
                    work / "local" / sid / "mle/results/large.bin"
                ).stat().st_size == 11 * 1024 * 1024
            try:
                remote.remote_bash("exit 7", session_id="one")
            except ValueError as error:
                assert "(7)" in str(error)
            else:
                raise AssertionError("Nonzero exit was not an error")
        print(
            json.dumps(
                {
                    "ssh": "passed",
                    "sessions": 2,
                    "quoted_paths": True,
                    "large_download_bytes": 11 * 1024 * 1024,
                    "exit_code": True,
                    "cloud_allocations": 0,
                    "model_requests": 0,
                }
            )
        )
    finally:
        run(["docker", "rm", "-f", container], capture_output=True)
