import os
import stat
import time
import shutil
import tempfile
import subprocess
import atexit
import signal
import inspect
import functools
import shlex
from pathlib import PurePosixPath
from pathlib import Path
from typing import List, Optional, Any, Callable, Tuple
from dataclasses import dataclass

from mle_kit_mcp.capacity import GPU_LEASE
from mle_kit_mcp.files import get_workspace_dir, normalize_session_id, resolve_session_path
from mle_kit_mcp.settings import settings

BASE_IMAGE = "phoenix120/holosophos_mle"
GLOBAL_TIMEOUT = 86400
VAST_AI_GREETING = """Welcome to vast.ai. If authentication fails, try again after a few seconds, and double check your ssh key.
Have fun!"""


@dataclass
class InstanceInfo:
    instance_id: int
    ip: str = ""
    port: int = 0
    username: str = ""
    ssh_key_path: str = ""
    gpu_name: str = ""
    start_time: int = 0
    remote_home: str = "/root"


_sdk: Optional[Any] = None
_instance_info: Optional[InstanceInfo] = None


def get_sdk() -> Any:
    global _sdk
    if not _sdk:
        from vastai_sdk import VastAI  # type: ignore

        _sdk = VastAI(api_key=settings.VAST_AI_KEY)
    return _sdk


def usable_private_key(path: str) -> str:
    """Return a path to ``path`` that ssh will accept, copying it if need be.

    ssh refuses a private key that is group/world-readable ("UNPROTECTED PRIVATE
    KEY FILE") and there is nothing we can do to the source: the static-host
    providers bind-mount the key read-only, and a Windows host cannot carry unix
    perms across a bind-mount at all -- it lands 0777. So copy it once to a 0600
    file we own. A key that is already private is used as-is, which keeps the
    Linux path a no-op. Non-files (the ``/dev/null`` fallback compose uses when
    no key is configured) pass through untouched so the caller's own missing-key
    diagnostics still fire.
    """
    if not path or not os.path.isfile(path):
        return path
    if not stat.S_IMODE(os.stat(path).st_mode) & 0o077:
        return path
    # Container-local tmp, never the bind-mounted workspace: the copy is a
    # private key and must not land in a host-visible (or syncable) folder.
    private = os.path.join(tempfile.gettempdir(), f"mle_kit_{os.path.basename(path)}_600")
    # Create with 0600 from the start -- never widen-then-narrow, which would
    # leave the key briefly readable.
    fd = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.close(fd)
    shutil.copyfile(path, private)
    os.chmod(private, 0o600)
    return private


def get_instance() -> InstanceInfo:
    signal.alarm(GLOBAL_TIMEOUT)
    global _instance_info
    if not _instance_info:
        if settings.REMOTE_PROVIDER == "cloudru":
            _instance_info = build_cloudru_instance()
        elif settings.REMOTE_PROVIDER == "selectel":
            _instance_info = build_selectel_instance()
        else:
            _instance_info = launch_instance(get_sdk(), settings.GPU_TYPE)

    assert _instance_info, "Failed to connect to a remote instance! Try again"
    return _instance_info


def cleanup_instance(signum: Optional[Any] = None, frame: Optional[Any] = None) -> None:
    global _instance_info
    signal.alarm(0)
    if (
        _instance_info
        and _sdk
        and settings.EXISTING_INSTANCE_ID is None
        and settings.REMOTE_PROVIDER not in ("cloudru", "selectel")
    ):
        print("Cleaning up...")
        try:
            _sdk.destroy_instance(id=_instance_info.instance_id)
            _instance_info = None
        except Exception:
            pass
    if signum == signal.SIGINT:
        raise KeyboardInterrupt()


atexit.register(cleanup_instance)
signal.signal(signal.SIGINT, cleanup_instance)
signal.signal(signal.SIGTERM, cleanup_instance)
signal.signal(signal.SIGALRM, cleanup_instance)


def wait_for_instance(vast_sdk: Any, instance_id: str, max_wait_time: int = 600) -> bool:
    print(f"Waiting for instance {instance_id} to be ready...")
    start_wait = int(time.time())
    instance_ready = False
    while time.time() - start_wait < max_wait_time:
        instance_details = vast_sdk.show_instance(id=instance_id)
        if (
            isinstance(instance_details, dict)
            and instance_details.get("actual_status") == "running"
        ):
            instance_ready = True
            print(f"Instance {instance_id} is running and ready.")
            break
        print(f"Instance {instance_id} not ready yet. Waiting...")
        time.sleep(15)
    return instance_ready


def get_offers(vast_sdk: Any, gpu_name: str) -> List[int]:
    params = [
        f"gpu_name={gpu_name}",
        "cuda_vers>=12.1",
        "num_gpus=1",
        "reliability > 0.99",
        "inet_up > 400",
        "inet_down > 400",
        "verified=True",
        "rentable=True",
        f"disk_space > {settings.DISK_SPACE - 1}",
    ]
    query = "  ".join(params)
    order = "score-"
    offers = vast_sdk.search_offers(query=query, order=order)
    assert offers
    return [int(o["id"]) for o in offers]


def run_command(
    instance: InstanceInfo,
    command: str,
    timeout: int = 60,
    gpu_index: Optional[int] = None,
) -> subprocess.CompletedProcess[str]:
    # Hard cap (spec SC2/SC6): when a card index is supplied, pin this remote
    # command to exactly one visible GPU so the session can never see more than
    # one card. Each call opens a fresh ssh (remote shell state is not
    # persistent across calls), so the pin is injected on *every* call.
    # run_command is the single choke point both orchestrators funnel through
    # (multi-user UI / CodeArkt and scripts/run_research.py both reach the same
    # remote_bash -> run_command), which is exactly why the cap lives here and
    # is inherited by both launch paths without a per-orchestrator branch.
    # gpu_index=None leaves the command un-pinned -- used by occupancy discovery
    # and SSH probes, which must see the whole host.
    remote_command = command
    if gpu_index is not None:
        remote_command = f"export CUDA_VISIBLE_DEVICES={gpu_index}; {command}"
    cmd = [
        "ssh",
        "-i",
        instance.ssh_key_path,
        "-p",
        str(instance.port),
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "TCPKeepAlive=yes",
        "-o",
        "LogLevel=ERROR",
        f"{instance.username}@{instance.ip}",
        remote_command,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.stdout:
            result.stdout = result.stdout.replace(VAST_AI_GREETING, "").strip()
        if result.stderr:
            result.stderr = result.stderr.replace(VAST_AI_GREETING, "").strip()
    except subprocess.TimeoutExpired as e:
        output = None
        if e.stdout:
            output_message = e.stdout.decode("utf-8").strip()
            output_message = output_message.replace(VAST_AI_GREETING, "").strip()
            if output_message:
                output = output_message
        error = (
            f"Command timed out after {timeout} seconds: {command};\n"
            f"You can increase the timeout by changing the parameter of the tool call.\n"
        )
        if e.stderr:
            error_message = e.stderr.decode("utf-8").strip()
            error_message = error_message.replace(VAST_AI_GREETING, "").strip()
            if error_message:
                error += f"Original stderr: {error_message}"
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=-9,
            stdout=output,
            stderr=error,
        )
    return result


def recieve_rsync(
    info: InstanceInfo, remote_path: str, local_path: str
) -> subprocess.CompletedProcess[str]:
    try:
        os.makedirs(local_path, exist_ok=True)
    except Exception:
        parent_dir = os.path.dirname(local_path) or "."
        os.makedirs(parent_dir, exist_ok=True)

    rsync_cmd = [
        "rsync",
        "-avz",
        "--protect-args",
        "-e",
        (
            f"ssh -i {shlex.quote(info.ssh_key_path)} -p {info.port} "
            "-o StrictHostKeyChecking=no -o LogLevel=ERROR"
        ),
        f"{info.username}@{info.ip}:{remote_path}",
        local_path,
    ]

    result = subprocess.run(rsync_cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return result

    # Fallback to scp when remote rsync is unavailable (e.g. cloud.ru ML Space
    # ships without it). scp goes through sshd's sftp subsystem and needs no
    # extra server-side binary.
    scp_result = _scp_recv(info, remote_path, local_path)
    if scp_result.returncode == 0:
        return scp_result
    raise Exception(
        f"Error syncing directory: {remote_path} to {local_path}. "
        f"rsync error: {result.stderr.strip()}; scp fallback error: {scp_result.stderr.strip()}"
    )


def _scp_recv(
    info: InstanceInfo, remote_path: str, local_path: str
) -> subprocess.CompletedProcess[str]:
    cmd = [
        "scp",
        "-r",
        "-i",
        info.ssh_key_path,
        "-P",
        str(info.port),
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "LogLevel=ERROR",
        f"{info.username}@{info.ip}:{remote_path}",
        local_path,
    ]
    return subprocess.run(cmd, capture_output=True, text=True)


def send_rsync(
    info: InstanceInfo, local_path: str, remote_path: str
) -> subprocess.CompletedProcess[str]:
    # Ensure remote destination directory exists before syncing
    rsync_path_arg = f"--rsync-path=mkdir -p {shlex.quote(remote_path)} && rsync"
    rsync_cmd = [
        "rsync",
        "-avz",
        "--protect-args",
        rsync_path_arg,
        "-e",
        (
            f"ssh -i {shlex.quote(info.ssh_key_path)} -p {info.port} "
            "-o StrictHostKeyChecking=no -o LogLevel=ERROR"
        ),
        local_path,
        f"{info.username}@{info.ip}:{remote_path}",
    ]

    result = subprocess.run(rsync_cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return result

    # Fallback to scp. Mirrors recieve_rsync — needed e.g. for cloud.ru where
    # remote rsync is not installed. We mkdir explicitly via ssh first since
    # scp has no equivalent of --rsync-path.
    mkdir_cmd = [
        "ssh",
        "-i",
        info.ssh_key_path,
        "-p",
        str(info.port),
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "LogLevel=ERROR",
        f"{info.username}@{info.ip}",
        f"mkdir -p {shlex.quote(remote_path)}",
    ]
    subprocess.run(mkdir_cmd, capture_output=True, text=True)
    scp_result = _scp_send(info, local_path, remote_path)
    if scp_result.returncode == 0:
        return scp_result
    raise Exception(
        f"Error syncing directory: {local_path} to {remote_path}. "
        f"rsync error: {result.stderr.strip()}; scp fallback error: {scp_result.stderr.strip()}"
    )


def _scp_send(
    info: InstanceInfo, local_path: str, remote_path: str
) -> subprocess.CompletedProcess[str]:
    cmd = [
        "scp",
        "-r",
        "-i",
        info.ssh_key_path,
        "-P",
        str(info.port),
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "LogLevel=ERROR",
        local_path,
        f"{info.username}@{info.ip}:{remote_path}",
    ]
    return subprocess.run(cmd, capture_output=True, text=True)


def check_instance(vast_sdk: Any, instance_id: int, ssh_key_path: str) -> Tuple[InstanceInfo, bool]:
    instance_details = vast_sdk.show_instance(id=instance_id)
    ssh_key_full_path = Path(ssh_key_path).expanduser()
    info = InstanceInfo(
        instance_id=instance_details.get("id"),
        ip=instance_details.get("ssh_host"),
        port=instance_details.get("ssh_port"),
        username="root",
        ssh_key_path=str(ssh_key_full_path),
        gpu_name=instance_details.get("gpu_name"),
        start_time=int(time.time()),
    )

    print(info)
    print(f"Checking SSH connection to {info.ip}:{info.port}...")
    max_attempts = 10
    is_okay = False
    for attempt in range(max_attempts):
        result = run_command(info, "echo 'SSH connection successful'")
        if result.returncode != 0:
            print(f"Waiting for SSH... {result.stderr}\n(Attempt {attempt+1}/{max_attempts})")
            time.sleep(30)
            continue
        if "SSH connection successful" in result.stdout:
            print("SSH connection established successfully!")
            is_okay = True
            break
        print(f"Waiting for SSH... (Attempt {attempt+1}/{max_attempts})")
        time.sleep(30)
    return info, is_okay


def build_cloudru_instance() -> InstanceInfo:
    missing = [
        k
        for k, v in {
            "CLOUDRU_SSH_HOST": settings.CLOUDRU_SSH_HOST,
            "CLOUDRU_SSH_USER": settings.CLOUDRU_SSH_USER,
            "CLOUDRU_SSH_KEY": settings.CLOUDRU_SSH_KEY,
        }.items()
        if not v
    ]
    assert not missing, f"REMOTE_PROVIDER=cloudru requires: {', '.join(missing)}"

    info = InstanceInfo(
        instance_id=0,
        ip=settings.CLOUDRU_SSH_HOST or "",
        port=settings.CLOUDRU_SSH_PORT,
        username=settings.CLOUDRU_SSH_USER or "",
        ssh_key_path=usable_private_key(settings.CLOUDRU_SSH_KEY or ""),
        gpu_name="cloudru",
        start_time=int(time.time()),
        remote_home=settings.CLOUDRU_REMOTE_HOME,
    )
    last_err = ""
    for _ in range(5):
        r = run_command(info, "echo cloudru-ok")
        if r.returncode == 0 and "cloudru-ok" in (r.stdout or ""):
            return info
        last_err = r.stderr or ""
        time.sleep(5)
    raise RuntimeError(f"cloud.ru SSH probe failed: {last_err}")


def _reconcile_gpu_slots(instance: InstanceInfo) -> None:
    """Lift the GPU capacity ceiling to the discovered free-card count (SC5).

    The shared Selectel host's allocatable-card count is not known at import
    time (it needs a live occupancy probe), so ``GPU_LEASE`` is built with the
    static single-box default and reconciled here, once, right after the first
    successful ``build_selectel_instance``. Best-effort: a discovery hiccup must
    not fail the instance build, so on any error the ceiling simply stays at its
    previous value. Runs the probe un-pinned (``discover_free_indices`` sees the
    whole host), independent of any per-session pin.
    """
    try:
        from mle_kit_mcp import gpu_registry
        from mle_kit_mcp.capacity import set_gpu_slots

        free = gpu_registry.discover_free_indices(instance)
        set_gpu_slots(len(free))
    except Exception:
        pass


def build_selectel_instance(reconcile: bool = True) -> InstanceInfo:
    """Build the InstanceInfo for the static Selectel multi-GPU SSH host.

    ``reconcile=False`` builds a connection without touching ``GPU_LEASE``. Read-only
    callers (telemetry) must pass it: reconciling resizes the real capacity
    semaphore to the free-card count, so an observer that reconciled would drop
    the ceiling to 0 whenever the host happens to be busy -- turning a
    fail-fast ``NoFreeGpuError`` into a run that blocks forever on the lease.


    Same integration shape as ``build_cloudru_instance`` (log into a running
    box, no Vast SDK, no teardown -- AP-09), differing only in the endpoint,
    the default SSH port (22, not 2222) and ``remote_home`` (``/home/m.mozikov``,
    routed through ``InstanceInfo.remote_home`` per AP-06). Validates the
    required Selectel env with a clear missing-list assert (no silent
    fall-through to the Vast branch), probes SSH with backoff, then reconciles
    the GPU capacity ceiling to the discovered free-card count.
    """
    missing = [
        k
        for k, v in {
            "SELECTEL_SSH_HOST": settings.SELECTEL_SSH_HOST,
            "SELECTEL_SSH_USER": settings.SELECTEL_SSH_USER,
            "SELECTEL_SSH_KEY": settings.SELECTEL_SSH_KEY,
        }.items()
        if not v
    ]
    assert not missing, f"REMOTE_PROVIDER=selectel requires: {', '.join(missing)}"

    info = InstanceInfo(
        instance_id=0,
        ip=settings.SELECTEL_SSH_HOST or "",
        port=settings.SELECTEL_SSH_PORT,
        username=settings.SELECTEL_SSH_USER or "",
        ssh_key_path=usable_private_key(settings.SELECTEL_SSH_KEY or ""),
        gpu_name="selectel",
        start_time=int(time.time()),
        remote_home=settings.SELECTEL_REMOTE_HOME,
    )
    last_err = ""
    for _ in range(5):
        r = run_command(info, "echo selectel-ok")
        if r.returncode == 0 and "selectel-ok" in (r.stdout or ""):
            if reconcile:
                _reconcile_gpu_slots(info)
            return info
        last_err = r.stderr or ""
        time.sleep(5)
    raise RuntimeError(f"Selectel SSH probe failed: {last_err}")


def launch_instance(vast_sdk: Any, gpu_name: str) -> Optional[InstanceInfo]:
    print(f"Selecting instance with {gpu_name}...")
    offer_ids = get_offers(vast_sdk, gpu_name)

    instance_id = None
    info: Optional[InstanceInfo] = None

    if settings.EXISTING_INSTANCE_ID and settings.EXISTING_SSH_KEY:
        instance_id = settings.EXISTING_INSTANCE_ID
        info, is_okay = check_instance(
            vast_sdk, instance_id, ssh_key_path=settings.EXISTING_SSH_KEY
        )
        if is_okay:
            return info

    for offer_id in offer_ids:
        print(f"Launching offer {offer_id}...")
        instance = vast_sdk.create_instance(id=offer_id, image=BASE_IMAGE, disk=settings.DISK_SPACE)
        if not instance["success"]:
            continue
        instance_id = instance["new_contract"]
        assert instance_id
        global _instance_info
        _instance_info = InstanceInfo(instance_id=instance_id)
        print(f"Instance launched successfully. ID: {instance_id}")
        is_ready = wait_for_instance(vast_sdk, instance_id)
        if not is_ready:
            print(f"Destroying instance {instance_id}...")
            vast_sdk.destroy_instance(id=instance_id)
            continue

        print("Attaching SSH key...")
        ssh_key_path = "~/.ssh/id_rsa"
        ssh_key_full_path = Path(ssh_key_path).expanduser()
        if not ssh_key_full_path.exists():
            print(f"Generating SSH key at {ssh_key_full_path}...")
            os.makedirs(ssh_key_full_path.parent, exist_ok=True)
            subprocess.run(
                [
                    "ssh-keygen",
                    "-t",
                    "rsa",
                    "-b",
                    "4096",
                    "-f",
                    str(ssh_key_full_path),
                    "-N",
                    "",
                ]
            )

        public_key = Path(f"{ssh_key_full_path}.pub").read_text().strip()
        vast_sdk.attach_ssh(instance_id=instance_id, ssh_key=public_key)
        info, is_okay = check_instance(vast_sdk, instance_id, ssh_key_path=str(ssh_key_full_path))
        if not is_okay:
            print(f"Destroying instance {instance_id}...")
            vast_sdk.destroy_instance(id=instance_id)
            continue

        break

    return info


def send_scripts() -> None:
    assert _instance_info
    for name in os.listdir(get_workspace_dir()):
        if name.endswith(".py"):
            send_rsync(
                _instance_info,
                f"{get_workspace_dir()}/{name}",
                _instance_info.remote_home,
            )


def _session_gpu_index(instance: InstanceInfo, session_id: str) -> int:
    """Resolve the single GPU card index this session's remote commands pin to.

    The hard cap itself (spec SC2/SC6) is provider-independent and enforced in
    ``run_command``; this only decides *which* card, keeping the enforcement
    path uniform across providers (no selectel-only branch in ``run_command``).

    * **Selectel** is a shared multi-GPU host, so each session leases a distinct
      *free* card via dynamic discovery (``gpu_registry.acquire_gpu_index``). A
      blank ``session_id`` still gets a free card pinned (fallback), so the
      session sees at most one GPU even without an identity.
    * **Single-card providers** (vast / cloudru) always pin index ``0`` -- a
      no-op that keeps the path uniform with no wasted ``nvidia-smi`` probe.
    """
    # ``str(...)`` compares defensively: the provider foundation (SC1) extends
    # the ``REMOTE_PROVIDER`` Literal to include ``"selectel"``; until then a
    # bare ``== "selectel"`` would read as a non-overlapping comparison. This
    # mirrors the ``getattr(settings, "SELECTEL_GPU_INDICES", ...)`` guard the
    # registry uses so the pinning path does not depend on SC1 landing first.
    if str(settings.REMOTE_PROVIDER) == "selectel":
        from mle_kit_mcp import gpu_registry

        return gpu_registry.acquire_gpu_index(session_id, instance)
    return 0


def _relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value or str(path) == ".":
        raise ValueError("Use a relative file path inside the session workspace")
    return str(path)


def _remote_root(instance: InstanceInfo, session_id: str) -> str:
    if not session_id:
        return instance.remote_home  # Compatibility for old standalone clients.
    return str(PurePosixPath(instance.remote_home) / "sessions" / normalize_session_id(session_id))


def _checked(result):
    if result.returncode:
        raise ValueError(f"Remote operation failed ({result.returncode}): {result.stdout or ''} {result.stderr or ''}")
    return result


def remote_upload(file_path: str, session_id: str = "") -> str:
    """Upload an existing local session file to the same relative remote path.

    Use for attached inputs and saved code before remote_bash. Pass the supplied
    session_id. file_path is relative to the local session root, e.g. inputs/data.csv.
    """
    relative = _relative_path(file_path)
    local = resolve_session_path(session_id, relative)
    if not local.is_file():
        raise ValueError("Upload source is not a file in this session")
    with GPU_LEASE.hold():
        instance = get_instance()
        destination = str(PurePosixPath(_remote_root(instance, session_id)) / PurePosixPath(relative).parent)
        _checked(send_rsync(instance, str(local), destination))
    return f"Uploaded {relative} to this session's remote workspace."


def remote_bash(command: str, timeout: int = 60, session_id: str = "") -> str:
    """
    Run commands in a bash shell on a remote machine with GPU cards.
    When invoking this tool, the contents of the "command" parameter does NOT need to be XML-escaped.
    You don't have access to the internet via this tool.
    You do have access to a mirror of common linux and python packages via apt and pip.
    Files persist across calls. Each command starts in this session's remote
    workspace (a new shell each time). Upload attached files with remote_upload;
    paths such as inputs/data.csv and mle/results/result.json are relative to
    that workspace. Use remote_download to make results visible locally.
    To inspect a particular line range of a file, e.g. lines 10-25, try 'sed -n 10,25p /path/to/the/file'.
    Please avoid commands that may produce a very large amount of output.
    Do not run commands in the background.
    You can use python3.

    For parallel research sessions that share this MCP container, pass
    `session_id` (the manager hands it down via the task description). It ties
    your remote commands to your session's own GPU card so two concurrent
    sessions never share a card. It must be part of this tool's signature (not
    just documented): CodeArkt filters call arguments by the tool's input
    schema, so a `session_id` outside the signature is silently dropped before
    it reaches the tool.

    Args:
        command: The bash command to run.
        timeout: Timeout for the command execution. 60 seconds by default. Set a higher value for heavy jobs.
        session_id: Optional session id identifying your run. Pass it whenever
            the manager included a Session ID in your task description so your
            remote commands stay pinned to your session's own GPU card.
    """

    assert timeout
    if session_id:
        normalize_session_id(session_id)
    with GPU_LEASE.hold():
        instance = get_instance()
        assert instance
        # `session_id` identifies this run's leased GPU card in the per-session
        # registry (mle_kit_mcp.gpu_registry). The pin is applied by the shared
        # run_command path below (CUDA_VISIBLE_DEVICES injection); accepting the
        # id in this tool's signature is the prerequisite that lets it survive
        # CodeArkt argument filtering. Because both orchestrators reach this same
        # remote_bash, both launch paths inherit the one-card cap (SC6).
        gpu_index = _session_gpu_index(instance, session_id)
        root = shlex.quote(_remote_root(instance, session_id))
        scoped = f"mkdir -p {root} && cd {root} && {command}" if session_id else command
        result = _checked(run_command(instance, scoped, timeout=timeout, gpu_index=gpu_index))
    output = ("Command stdout: " + result.stdout + "\n") if result.stdout else ""
    output += ("Command stderr: " + result.stderr) if result.stderr else ""
    if not output.strip():
        output = "No output from the command"
    return output


def remote_download(file_path: str, session_id: str = "") -> str:
    """
    Copies a file from a remote machine to the local work directory, preserving
    the remote subdirectory layout. So `remote_download("mle/foo.txt")` lands at
    `<workspace>/mle/foo.txt`, not `<workspace>/foo.txt`.

    For parallel research sessions that share this MCP container, pass
    `session_id` (the manager hands it down via the task description). The file
    then lands under `<workspace>/<session_id>/<remote-subdir>/<basename>`
    instead of the shared root, so two sessions cannot overwrite each other.

    Use it to download final artefacts of the runs.
    Args:
        file_path: Path to the file on a remote machine.
        session_id: Optional session id to namespace the local target under
            `<workspace>/<session_id>/`. Pass it whenever the manager included
            a Session ID in your task description.
    """
    relative = _relative_path(file_path)
    target = resolve_session_path(session_id, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    with GPU_LEASE.hold():
        instance = get_instance()
        source = str(PurePosixPath(_remote_root(instance, session_id)) / relative)
        _checked(recieve_rsync(instance, source, str(target.parent)))
    return f"Downloaded {relative} to this session's local workspace."


def create_remote_text_editor(
    text_editor_func: Callable[..., str],
) -> Callable[..., str]:
    @functools.wraps(text_editor_func)
    def wrapper(*args: Any, **kwargs: Any) -> str:
        args_dict = {k: v for k, v in kwargs.items()}
        if args:
            args_dict.update(dict(zip(("command", "path"), args)))
        path = _relative_path(args_dict["path"])
        command = args_dict["command"]
        session_id = args_dict.get("session_id") or ""
        local = resolve_session_path(session_id, path)
        local.parent.mkdir(parents=True, exist_ok=True)
        with GPU_LEASE.hold():
            instance = get_instance()
            remote = str(PurePosixPath(_remote_root(instance, session_id)) / path)
            if command != "write":
                _checked(recieve_rsync(instance, remote, str(local.parent)))
            args_dict["path"] = path
            result = text_editor_func(**args_dict)
            if command != "view":
                _checked(send_rsync(instance, str(local), str(PurePosixPath(remote).parent)))

        return result

    orig_sig = inspect.signature(text_editor_func)
    wrapper.__signature__ = orig_sig  # type: ignore
    if text_editor_func.__doc__:
        orig_doc = text_editor_func.__doc__
        new_doc = orig_doc.replace("text_editor", "remote_text_editor")
        wrapper.__doc__ = (
            "Executes on a remote machine with GPU.\nPlease use relative paths.\n" + new_doc
        )
        wrapper.__name__ = "remote_text_editor"
    return wrapper
