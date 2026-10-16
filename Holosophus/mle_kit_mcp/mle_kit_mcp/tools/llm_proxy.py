import json
import time
import random
import shlex
from pathlib import Path

from mle_kit_mcp.settings import settings

from mle_kit_mcp.tools.bash import get_container, get_docker_client
from mle_kit_mcp.files import get_workspace_dir, normalize_session_id

from mle_kit_mcp.tools.remote_gpu import (
    get_instance as _remote_get_instance,
    run_command as _remote_run_command,
    send_rsync as _remote_send_rsync,
    _remote_root,
)

INPUT_SCRIPT_FILE_NAME = "llm_proxy.py"
OUTPUT_SCRIPT_FILE_NAME = "llm_proxy.py"
DEPENDENCIES = "fastapi uvicorn httpx openai fire"
START_TIMEOUT = 30


def _write_proxy_script(script_path: Path) -> None:
    source_script_path = Path(__file__).parent.parent / INPUT_SCRIPT_FILE_NAME
    script = source_script_path.read_text()
    script_path.write_text(script)


def _local_proxy_container(session_id: str):
    """Place the proxy beside the session's actual local command executor."""
    root = get_workspace_dir(session_id)
    descriptor = root / ".holosophus_ctl/sandbox.json"
    try:
        info = json.loads(descriptor.read_text())
    except (OSError, ValueError):
        info = {}
    if info.get("executor") == "claude_bash":
        container = get_docker_client().containers.get(info["container"])
        if container.labels.get("holosophus.cc.session") != session_id:
            raise ValueError("Docker executor does not belong to this session")
        if container.status != "running":
            raise RuntimeError("The session Docker executor is not running")
        return container
    marker = root / ".holosophus_ui/orchestrator"
    if marker.exists() and marker.read_text().strip() == "claude_code":
        raise RuntimeError("The session Docker executor is not ready; retry after runner preparation")
    return get_container()  # Existing CodeArkt / standalone MLE clients.


def llm_proxy_local(session_id: str | None = None) -> str:
    """
    Start a lightweight OpenRouter proxy in the session's local Docker executor.

    Returns a JSON string with url and scope.
    The url is reachable from local Bash (native Claude Bash or MLE bash) as localhost.
    It runs a standard OpenAI compatible server, so you can use it with any OpenAI compatible client.
    You can use all models available on OpenRouter, for instance:
    - openai/gpt-5-mini
    - google/gemini-2.5-pro
    - anthropic/claude-sonnet-4
    """

    assert (
        session_id and str(session_id).strip()
    ), "session_id is required for llm_proxy_local to keep workspace isolation."
    normalized_session_id = normalize_session_id(session_id)
    session_root = f"/workdir/{normalized_session_id}"

    api_key = settings.OPENROUTER_API_KEY
    assert api_key, "Set OPENROUTER_API_KEY in the environment before starting the proxy."

    script_path = get_workspace_dir(session_id=session_id) / OUTPUT_SCRIPT_FILE_NAME
    _write_proxy_script(script_path)

    container = _local_proxy_container(normalized_session_id)
    setup_cmd = (
        f"mkdir -p {normalized_session_id}/.cache "
        f"{normalized_session_id}/.config "
        f"{normalized_session_id}/.local/share "
        f"{normalized_session_id}/__pycache__ "
        f"{normalized_session_id}/.texlive"
    )
    container.exec_run(["bash", "-lc", setup_cmd], workdir="/workdir")

    env_prefix = (
        f"HOME={session_root} "
        f"XDG_CACHE_HOME={session_root}/.cache "
        f"XDG_CONFIG_HOME={session_root}/.config "
        f"XDG_DATA_HOME={session_root}/.local/share "
        f"PYTHONPYCACHEPREFIX={session_root}/__pycache__ "
        f"PIP_CACHE_DIR={session_root}/.cache/pip "
        f"MPLCONFIGDIR={session_root}/.config/matplotlib "
        f"TEXMFVAR={session_root}/.texlive/texmf-var "
        f"TEXMFCONFIG={session_root}/.texlive/texmf-config "
        f"TEXMFHOME={session_root}/.texlive/texmf-home "
    )

    dependencies_cmd = f"{env_prefix}python -m pip install --quiet --no-input {DEPENDENCIES}"
    container.exec_run(["bash", "-lc", dependencies_cmd], workdir="/workdir")

    chosen_port = random.randint(5000, 6000)
    launch_cmd = (
        f"{env_prefix}"
        f"OPENROUTER_API_KEY={shlex.quote(api_key)} "
        f"nohup python {normalized_session_id}/{OUTPUT_SCRIPT_FILE_NAME} "
        f"--host 127.0.0.1 --port {chosen_port} "
        f"> {normalized_session_id}/llm_proxy.log 2>&1 "
        f"& echo $! > {normalized_session_id}/llm_proxy.pid"
    )
    container.exec_run(["bash", "-lc", launch_cmd], workdir="/workdir")

    health_cmd = f'import httpx; print(httpx.get("http://127.0.0.1:{chosen_port}/health").json())'
    start_time = time.time()
    while time.time() - start_time < START_TIMEOUT:
        result = container.exec_run(["python", "-c", health_cmd])
        if result.exit_code == 0 and "ok" in result.output.decode("utf-8").strip():
            break
        time.sleep(1)
    else:
        raise Exception("Failed to start the proxy")

    return json.dumps(
        {
            "url": f"http://127.0.0.1:{chosen_port}/v1/chat/completions",
            "scope": "bash-container",
        }
    )


def llm_proxy_remote(session_id: str | None = None) -> str:
    """
    Start a lightweight OpenRouter proxy on the remote GPU machine.

    Returns a JSON string with url and scope.
    The url is reachable from inside the remote machine as localhost.
    It runs a standard OpenAI compatible server, so you can use it with any OpenAI compatible client.
    You can use all models available on OpenRouter, for instance:
    - openai/gpt-5-mini
    - google/gemini-2.5-pro
    - anthropic/claude-sonnet-4
    """

    assert (
        session_id and str(session_id).strip()
    ), "session_id is required for llm_proxy_remote to keep workspace isolation."

    api_key = settings.OPENROUTER_API_KEY
    assert api_key, "Set OPENROUTER_API_KEY in the environment before starting the proxy."

    instance = _remote_get_instance()
    script_path = get_workspace_dir(session_id=session_id) / OUTPUT_SCRIPT_FILE_NAME
    _write_proxy_script(script_path)
    remote_root = _remote_root(instance, session_id)
    _remote_send_rsync(instance, str(script_path), remote_root)

    chosen_port = random.randint(5000, 6000)
    dependencies_cmd = f"python3 -m pip install -q --no-input {DEPENDENCIES}"
    _remote_run_command(instance, dependencies_cmd, timeout=300)

    launch_cmd = (
        f"cd {shlex.quote(remote_root)} && "
        f"OPENROUTER_API_KEY={shlex.quote(api_key)} "
        f"nohup python {OUTPUT_SCRIPT_FILE_NAME} "
        f"--host 127.0.0.1 --port {chosen_port} "
        f"> openrouter_proxy.log 2>&1 "
        f"& echo $! > {shlex.quote(str(Path(remote_root) / 'openrouter_proxy.pid'))}"
    )
    _remote_run_command(instance, launch_cmd, timeout=60)

    health_cmd = f'import httpx; print(httpx.get("http://127.0.0.1:{chosen_port}/health").json())'
    start_time = time.time()
    while time.time() - start_time < START_TIMEOUT:
        result = _remote_run_command(instance, f"python -c '{health_cmd}'", timeout=10)
        if result.returncode == 0 and "ok" in result.stdout.strip():
            break
        time.sleep(1)
    else:
        raise Exception("Failed to start the proxy")

    return json.dumps(
        {
            "url": f"http://127.0.0.1:{chosen_port}/v1/chat/completions",
            "scope": "remote-gpu",
        }
    )
