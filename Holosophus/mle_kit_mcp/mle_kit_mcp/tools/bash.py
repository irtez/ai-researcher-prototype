import atexit
import json
import signal
import shlex
import os
from typing import Optional, Any

from docker import from_env as docker_from_env  # type: ignore
from docker import DockerClient
from docker.models.containers import Container  # type: ignore
from docker.types import DeviceRequest  # type: ignore

from mle_kit_mcp.capacity import LOCAL_EXEC_LEASE
from mle_kit_mcp.files import get_host_workspace_dir, get_workspace_dir, normalize_session_id, resolve_session_path
from mle_kit_mcp.settings import settings


_container = None
_client = None

BASE_IMAGE = "phoenix120/holosophos_mle"
DOCKER_WORKSPACE_DIR_PATH = "/workdir"


def get_docker_client() -> DockerClient:
    global _client
    if not _client:
        _client = docker_from_env()
    return _client


def _local_gpu_device_requests() -> Optional[list[DeviceRequest]]:
    """Translate LOCAL_GPU_DEVICES into the docker-py equivalent of ``--gpus``.

    Returns None when unset, which is what keeps the sandbox CPU-only by
    default. Asking for a GPU the host can't provide fails container start
    loudly -- that is deliberate: the setting is opt-in, so a silent CPU
    fallback would leave an agent quietly training on the wrong device.
    """
    devices = settings.LOCAL_GPU_DEVICES.strip()
    if not devices:
        return None
    if devices == "all":
        return [DeviceRequest(count=-1, capabilities=[["gpu"]])]
    ids = [d.strip() for d in devices.split(",") if d.strip()]
    return [DeviceRequest(device_ids=ids, capabilities=[["gpu"]])]


def create_container() -> Container:
    client = get_docker_client()
    uid = os.getuid()
    gid = os.getgid()
    container = client.containers.run(
        BASE_IMAGE,
        "tail -f /dev/null",
        detach=True,
        remove=True,
        tty=True,
        stdin_open=True,
        user=f"{uid}:{gid}",
        device_requests=_local_gpu_device_requests(),
        environment={
            "HOME": DOCKER_WORKSPACE_DIR_PATH,
            "XDG_CACHE_HOME": f"{DOCKER_WORKSPACE_DIR_PATH}/.cache",
        },
        volumes={
            str(get_host_workspace_dir()): {
                "bind": DOCKER_WORKSPACE_DIR_PATH,
                "mode": "rw",
            }
        },
        working_dir=DOCKER_WORKSPACE_DIR_PATH,
    )
    return container


def _compose_exec_env(session_id: Optional[str]) -> dict[str, str]:
    assert (
        session_id and str(session_id).strip()
    ), "session_id is required for bash to keep workspace isolation."
    session_dir_name = normalize_session_id(session_id)
    # Create through WORKSPACE_DIR (this container's /workdir), never through
    # HOST_WORKSPACE_DIR -- the latter is a *host* path that only means anything
    # to the docker daemon. mkdir-ing it from in here silently builds a phantom
    # tree inside this container (on Windows "D:/..." is even a relative path)
    # while the real bind-mounted dir stays missing, and the sandbox then dies
    # with `OCI runtime exec failed: chdir to cwd ... no such file or directory`.
    get_workspace_dir(session_id)
    home_dir = f"{DOCKER_WORKSPACE_DIR_PATH}/{session_dir_name}"

    cache_dir = f"{home_dir}/.cache"
    config_dir = f"{home_dir}/.config"
    data_dir = f"{home_dir}/.local/share"
    pycache_dir = f"{home_dir}/__pycache__"
    texlive_dir = f"{home_dir}/.texlive"
    env = {
        "HOME": home_dir,
        "XDG_CACHE_HOME": cache_dir,
        "XDG_CONFIG_HOME": config_dir,
        "XDG_DATA_HOME": data_dir,
        "PYTHONPYCACHEPREFIX": pycache_dir,
        "PIP_CACHE_DIR": f"{cache_dir}/pip",
        "MPLCONFIGDIR": f"{config_dir}/matplotlib",
        "TEXMFVAR": f"{texlive_dir}/texmf-var",
        "TEXMFCONFIG": f"{texlive_dir}/texmf-config",
        "TEXMFHOME": f"{texlive_dir}/texmf-home",
    }
    return env


def get_container() -> Container:
    global _container
    if not _container:
        _container = create_container()
    return _container


def _publish_sandbox_descriptor(session_id: Optional[str], container: Container) -> None:
    """Publish this session's sandbox freeze descriptor for the pause controller (Tier-1).

    Writes ``<session workspace>/.holosophus_ctl/sandbox.json`` = ``{"container": <id>}`` so the
    host-side pause controller (scripts/cc_run_control.py) can ``docker pause`` this session's
    sandbox. Best-effort — a write failure must never break a tool call.
    """
    if not session_id:
        return
    try:
        ctl_dir = get_workspace_dir(session_id) / ".holosophus_ctl"
        ctl_dir.mkdir(parents=True, exist_ok=True)
        path = ctl_dir / "sandbox.json"
        tmp = path.with_name(".sandbox.json.tmp")
        tmp.write_text(
            json.dumps({"container": container.id, "image": BASE_IMAGE}), encoding="utf-8"
        )
        os.replace(tmp, path)
    except Exception:
        pass


def cleanup_container(signum: Optional[Any] = None, frame: Optional[Any] = None) -> None:
    global _container
    if _container:
        try:
            _container.remove(force=True)
            _container = None
        except Exception:
            pass
    if signum == signal.SIGINT:
        raise KeyboardInterrupt()


atexit.register(cleanup_container)
signal.signal(signal.SIGINT, cleanup_container)
signal.signal(signal.SIGTERM, cleanup_container)


def bash(
    command: str,
    cwd: Optional[str] = None,
    timeout: int = 60,
    session_id: Optional[str] = None,
) -> str:
    """
    Run commands in a bash shell.
    When invoking this tool, the contents of the "command" parameter does NOT need to be XML-escaped.
    You don't have access to the internet via this tool.
    You do have access to a mirror of common linux and python packages via apt and pip.
    State is persistent across command calls and discussions with the user.
    To inspect a particular line range of a file, e.g. lines 10-25, try 'sed -n 10,25p /path/to/the/file'.
    Please avoid commands that may produce a very large amount of output.

    Args:
        command: The bash command to run.
        cwd: Directory relative to the current session (default: its root).
            Absolute /workdir/<session_id>/... paths inside this session also work.
        timeout: Timeout for the command execution in seconds. Kills after this. 60 seconds by default.
        session_id: Optional session id to isolate work under `workdir/<session_id>`.
    """
    assert timeout and timeout > 0, "Timeout must be set and greater than 0"
    exec_env = _compose_exec_env(session_id)
    directory = resolve_session_path(session_id, cwd)
    if not directory.is_dir():
        raise ValueError("Working directory does not exist in this session")
    workdir = f"{DOCKER_WORKSPACE_DIR_PATH}/{directory.relative_to(get_workspace_dir()).as_posix()}"

    wrapped = f"bash -lc {shlex.quote(command)}"
    final_command = f"timeout --signal=TERM --kill-after=5s {int(timeout)}s {wrapped}"

    with LOCAL_EXEC_LEASE.hold():
        container = get_container()
        _publish_sandbox_descriptor(session_id, container)
        result = container.exec_run(
            ["bash", "-lc", final_command],
            workdir=workdir,
            environment=exec_env,
            stdout=True,
            stderr=True,
            demux=True,
        )
    stdout_bytes, stderr_bytes = (
        result.output if isinstance(result.output, tuple) else (result.output, b"")
    )

    if result.exit_code == 124:
        timeout_msg = (
            f"Command timed out after {int(timeout)} seconds: {command};\n"
            f"You can increase the timeout by changing the parameter of the tool call."
        )
        stderr_bytes = (stderr_bytes or b"") + timeout_msg.encode("utf-8")
    elif result.exit_code == 137:
        stderr_bytes = (
            stderr_bytes or b""
        ) + b"\nCommand was killed; this may be a timeout or an external termination."

    stdout_text = (stdout_bytes or b"").decode("utf-8", errors="replace").strip()
    stderr_text = (stderr_bytes or b"").decode("utf-8", errors="replace").strip()

    output_parts = []
    if stdout_text:
        output_parts.append("Command stdout: " + stdout_text)
    if stderr_text:
        output_parts.append("Command stderr: " + stderr_text)
    output = ("\n".join(output_parts)).strip()
    if not output:
        output = "No output from the command"
    if result.exit_code != 0:
        # FastMCP converts tool exceptions to isError=true. Preserve the command
        # output so the agent can diagnose/recover without failing the run.
        raise RuntimeError(f"Command failed (exit code {result.exit_code}).\n{output}")
    return output
