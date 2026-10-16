"""Persistent per-session Docker execution behind Claude Code's native Bash.

Only the workspace, scratch files and read-only supervisor enter the container.
The host config/launcher remain outside it. No model API or MCP is used here.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager, suppress
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid

import yaml

SCRIPTS = Path(__file__).resolve().parent
DEFAULT_IMAGE = "phoenix120/holosophos_mle"
LABEL = "holosophus.cc.workspace"
LOG = logging.getLogger(__name__)
SUPERVISOR = "/opt/holosophus/command_supervisor.py"
MCP_BASH = "mcp__mle_kit__bash"
EXECUTION_PROMPT = yaml.safe_load(
    (SCRIPTS.parent / "holosophos/holosophos/prompts/shared/claude_code.yaml")
    .read_text(encoding="utf-8")
)["local_execution"]


def _birth(pid: int) -> str | None:
    try:
        # comm may contain spaces/parentheses. Field 22 is starttime.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def _atomic_json(path: Path, data: dict) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex)
    temporary.write_text(json.dumps(data))
    os.replace(temporary, path)


def _docker(args: list[str], *, check=True) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(
            "Docker executor is unavailable; start Docker and retry"
        ) from error
    if check and result.returncode:
        raise RuntimeError("Docker executor: " + result.stderr.strip()[:1500])
    return result


def _host_cli_path(workdir: Path, runtime: Path) -> str:
    """Native Read uses host helpers. Never search model-writable directories."""
    trusted = []
    for entry in os.environ.get("PATH", os.defpath).split(os.pathsep):
        if not Path(entry).is_absolute():
            continue
        lexical = Path(os.path.abspath(entry))
        if any(lexical.is_relative_to(root) for root in (workdir, runtime)):
            continue
        try:
            path = lexical.resolve()
        except (OSError, RuntimeError):
            continue
        if any(path.is_relative_to(root) for root in (workdir, runtime)):
            continue
        trusted.append(str(path))
    return os.pathsep.join(trusted) or os.defpath


class DockerBashSession:
    def __init__(self, workdir: Path, *, runtime_root: Path | None = None):
        self.workdir = workdir.resolve()
        self.key = hashlib.sha256(str(self.workdir).encode()).hexdigest()[:24]
        self.name = "holo-cc-" + self.key
        self.runtime = (
            runtime_root or SCRIPTS.parent / ".tmp/docker-executors"
        ).resolve() / self.key
        self.scratch = self.runtime / "scratch"
        self.figures = self.workdir.parent / "figures/paperbanana"
        self.config = self.runtime / "run.json"
        self.lock = None
        self.env: dict[str, str] = {}
        self.container = None

    def open(self):
        self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.scratch.mkdir(exist_ok=True)
        self.lock = (self.runtime / "run.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            self.lock = None
            raise RuntimeError(
                "This research session already has an active executor"
            ) from None
        try:
            self._prepare()
        except BaseException:
            self.close()
            raise
        return self

    def _prepare(self):
        self.figures.mkdir(parents=True, exist_ok=True)
        found = _docker(["inspect", self.name], check=False)
        info = json.loads(found.stdout)[0] if found.returncode == 0 else None
        # CC_EXECUTOR_IMAGE is the default for new sessions. A resumed session
        # keeps its existing container, installed packages and original image.
        image = info["Config"]["Image"] if info else os.environ.get("CC_EXECUTOR_IMAGE", DEFAULT_IMAGE)
        gpu = os.environ.get("LOCAL_GPU_DEVICES", "").strip()
        fingerprint = hashlib.sha256(
            json.dumps([image, gpu, str(self.runtime)]).encode()
        ).hexdigest()
        if found.returncode:
            if "no such" not in found.stderr.lower():
                raise RuntimeError("Docker executor: " + found.stderr.strip()[:1500])
            args = [
                "create",
                "--init",
                "--name",
                self.name,
                "--label",
                f"{LABEL}={self.key}",
                "--label",
                f"{LABEL}.config={fingerprint}",
                "--label",
                f"holosophus.cc.session={self.workdir.name}",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--env",
                f"HOME={self.workdir}",
                "--env",
                f"TMPDIR={self.scratch}",
                "--env",
                "PYTHONUNBUFFERED=1",
                "--workdir",
                str(self.workdir),
            ]
            for source, target, readonly in [
                (self.workdir, str(self.workdir), False),
                (self.figures, str(self.figures), True),
                (self.scratch, str(self.scratch), False),
                (SCRIPTS / "docker_bash_supervisor.py", SUPERVISOR, True),
            ]:
                if "," in str(source) or "," in target:
                    raise ValueError("Docker workspace paths cannot contain commas")
                args += [
                    "--mount",
                    f"type=bind,src={source},dst={target}"
                    + (",readonly" if readonly else ""),
                ]
            if gpu:
                args += ["--gpus", "all" if gpu == "all" else '"device=' + gpu + '"']
            args += [image, "tail", "-f", "/dev/null"]
            _docker(args)
            self.container = self.name
            _docker(["start", self.name])
        else:
            labels = info["Config"].get("Labels") or {}
            if (
                labels.get(LABEL) != self.key
                or labels.get(LABEL + ".config") != fingerprint
            ):
                raise RuntimeError(
                    "Executor configuration changed; use a new research session"
                )
            self.container = self.name
            # The session lock is held: any running container belongs to a stale
            # invocation. Restart clears orphan jobs while retaining its layer.
            if info["State"].get("Paused"):
                _docker(["unpause", self.name])
            if info["State"].get("Running"):
                _docker(["stop", "-t", "2", self.name])
            _docker(["start", self.name])
        self.container = self.name
        # A symlink (rather than a second bind mount) makes Bash's pwd -P return
        # the host-visible cwd recorded by Claude between tool calls.
        alias = f"/workdir/{self.workdir.name}"
        _docker(["exec", "--user", "0", self.name, "/bin/mkdir", "-p", "/workdir"])
        _docker(
            [
                "exec",
                "--user",
                "0",
                self.name,
                "/bin/ln",
                "-sfnT",
                str(self.workdir),
                alias,
            ]
        )
        info = json.loads(_docker(["inspect", self.name]).stdout)[0]
        container_env = dict(
            item.split("=", 1) for item in info["Config"]["Env"] if "=" in item
        )
        python = _docker(
            [
                "exec",
                self.name,
                "/bin/sh",
                "-c",
                "command -v python3 || command -v python",
            ]
        ).stdout.strip()
        if not python.startswith("/"):
            raise RuntimeError("The executor image must provide Python 3")
        image_path = container_env.get("PATH", "/usr/local/bin:/usr/bin:/bin")
        command_path = f"{self.workdir}/.local/bin:" + image_path
        config = {
            "token": uuid.uuid4().hex,
            "owner": os.getpid(),
            "birth": _birth(os.getpid()),
            "container": info["Id"],
            "workdir": str(self.workdir),
            "python": python,
            "docker": shutil.which("docker"),
            "scratch": str(self.scratch),
            "path": command_path,
        }
        _atomic_json(self.config, config)
        launcher = self.runtime / "bash-prefix"
        launcher.write_text(
            "#!/bin/sh\nexec "
            + " ".join(
                shlex.quote(str(part))
                for part in [
                    sys.executable,
                    SCRIPTS / "docker_bash.py",
                    "--config",
                    self.config,
                ]
            )
            + ' "$@"\n'
        )
        launcher.chmod(0o700)
        self.env = {
            "CLAUDE_CODE_SHELL_PREFIX": str(launcher),
            "CLAUDE_CODE_SHELL": "/bin/bash",
            "TMPDIR": str(self.scratch),
            "CLAUDE_CODE_TMPDIR": str(self.scratch),
            # Native tools need host helpers (e.g. pdftoppm). Bash receives its
            # own container PATH via run.json and docker exec, never this one.
            "PATH": _host_cli_path(self.workdir, self.runtime),
        }
        control = self.workdir / ".holosophus_ctl"
        control.mkdir(exist_ok=True)
        _atomic_json(
            control / "sandbox.json",
            {"container": info["Id"], "image": image, "executor": "claude_bash"},
        )

    def close(self):
        if self.lock is None:
            return
        try:
            self.config.unlink(missing_ok=True)
            if self.container:
                # Commands observe the missing lease. Unpause lets supervisors
                # process EOF, and stopping the dedicated container also reaps
                # descendants after a forcibly disconnected SDK client.
                for command in (
                    ["unpause", self.container],
                    ["stop", "-t", "2", self.container],
                ):
                    try:
                        outcome = _docker(command, check=False)
                        if command[0] == "stop" and outcome.returncode:
                            LOG.error(
                                "Could not stop Docker executor %s: %s",
                                self.name,
                                outcome.stderr.strip(),
                            )
                    except RuntimeError:
                        LOG.exception(
                            "Could not %s Docker executor %s", command[0], self.name
                        )
        finally:
            self.container = None
            self.lock.close()
            self.lock = None


@asynccontextmanager
async def executor_session(workdir: Path):
    session = DockerBashSession(workdir)
    # Preparation is bounded and happens before the SDK/model can use Bash.
    # Shield it so cancellation cannot leave an open executor after its caller exits.
    preparation = asyncio.create_task(asyncio.to_thread(session.open))
    try:
        await asyncio.shield(preparation)
        yield session
    finally:
        with suppress(Exception):
            await preparation
        await asyncio.to_thread(session.close)


class ExecutionGate:
    def __init__(self, workdir: Path):
        self.workdir = workdir.resolve()

    async def __call__(self, data, tool_use_id=None, context=None):
        name = data.get("tool_name")
        reason = None
        if name == MCP_BASH:
            reason = (
                "Use native Bash: it already executes in the session Docker container."
            )
        elif name in {"Write", "Edit", "NotebookEdit"}:
            args = data.get("tool_input") or {}
            value = args.get("file_path", args.get("notebook_path", ""))
            cwd = Path(data.get("cwd") or self.workdir)
            try:
                path = (cwd / value).resolve()
                if not path.is_relative_to(self.workdir):
                    reason = "Write research files inside the session workspace; host runtime files are managed by the system."
            except (OSError, ValueError, TypeError):
                reason = "Invalid workspace path."
        if reason:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        return {}


def configure_options(options: dict, workdir: Path, env: dict | None = None):
    from claude_agent_sdk import HookMatcher

    options["env"] = {**options.get("env", {}), **(env or {})}
    options["system_prompt"] += "\n\n" + EXECUTION_PROMPT
    options["disallowed_tools"] = list(
        dict.fromkeys([*options.get("disallowed_tools", []), MCP_BASH])
    )
    options.setdefault("hooks", {}).setdefault("PreToolUse", []).insert(
        0, HookMatcher(hooks=[ExecutionGate(workdir)])
    )
    for agent in options.get("agents", {}).values():
        if agent.tools is not None:
            agent.tools = [name for name in agent.tools if name != MCP_BASH]
        agent.disallowedTools = list(
            dict.fromkeys([*(agent.disallowedTools or []), MCP_BASH])
        )
        if agent.tools is None or "Bash" in agent.tools:
            agent.prompt += "\n\n" + EXECUTION_PROMPT


def run_command(config_path: Path, command: str) -> int:
    try:
        config = json.loads(config_path.read_text())
        workdir = Path(config["workdir"])
        cwd = Path.cwd().resolve()
        if not cwd.is_relative_to(workdir):
            raise ValueError("Bash working directory must stay inside this session")

        def alive():
            try:
                current = json.loads(config_path.read_text())
                return (
                    current["token"] == config["token"]
                    and _birth(config["owner"]) == config["birth"]
                )
            except (OSError, ValueError, KeyError):
                return False

        if not alive():
            raise ValueError("This research iteration has ended")
        stopped = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, lambda *_: stopped.set())
        process = subprocess.Popen(
            [
                config["docker"],
                "exec",
                "-i",
                "--workdir",
                str(cwd),
                "--env",
                "PATH=" + config["path"],
                config["container"],
                config["python"],
                SUPERVISOR,
                command,
            ],
            stdin=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            while process.poll() is None:
                if stopped.wait(0.1) or not alive():
                    break
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        return (
            143
            if stopped.is_set()
            else (
                process.returncode
                if process.returncode >= 0
                else 128 - process.returncode
            )
        )
    except (OSError, ValueError, KeyError) as error:
        print(f"Docker Bash: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("command")
    args = parser.parse_args()
    sys.exit(run_command(args.config, args.command))
