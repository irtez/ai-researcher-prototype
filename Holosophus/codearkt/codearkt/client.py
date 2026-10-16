import os
import time 
import logging
from functools import lru_cache
from pathlib import Path
from typing import Iterator, List

import httpx
from pydantic import ValidationError

from codearkt.event_bus import AgentEvent
from codearkt.llm import ChatMessage
from codearkt.settings import settings

HEADERS = {"Content-Type": "application/json", "Accept": "application/x-ndjson"}
REQUEST_TYPE = "POST"
CONNECT_TIMEOUT = 10.0
REQUIRED_HOLOSOPHOS_KEYS = (
    "OPENROUTER_API_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
)

logger = logging.getLogger(__name__)
LONG_SILENCE_WARNING_SECONDS = 600.0
LONG_SILENCE_REPEAT_SECONDS = 600.0

def _compose_base_url(host: str, port: int) -> str:
    base_url = f"{host}:{port}"
    if not base_url.startswith("http"):
        base_url = f"http://{base_url}"
    return base_url


def _compose_agent_url(host: str, port: int, agent_name: str) -> str:
    base_url = _compose_base_url(host, port)
    return f"{base_url}/agents/{agent_name}"


def _compose_cancel_url(host: str, port: int) -> str:
    base_url = _compose_base_url(host, port)
    return f"{base_url}/agents/cancel"


def _parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        values[key] = value
    return values


@lru_cache(maxsize=1)
def _dotenv_values() -> dict[str, str]:
    # Find .env independent of the launch directory: repo-root (resolved from this
    # file) first as a fallback, then cwd-relative paths last so a local .env wins.
    here = Path(__file__).resolve()
    cwd = Path.cwd()
    merged: dict[str, str] = {}
    for path in (
        here.parents[2] / ".env",  # repo root, e.g. Holosophus/.env
        here.parents[1] / ".env",  # codearkt/.env
        cwd.parent / ".env",
        cwd / ".env",
    ):
        merged.update(_parse_dotenv(path))
    return merged


def _env_value(key: str) -> str:
    from_env = os.getenv(key, "")
    if from_env.strip():
        return from_env.strip()
    return _dotenv_values().get(key, "").strip()


def _validate_holosophos_required_env() -> None:
    missing = [key for key in REQUIRED_HOLOSOPHOS_KEYS if not _env_value(key)]
    if not missing:
        return
    joined = ", ".join(missing)
    raise ValueError(
        "Missing required env keys for Holosophos client: "
        f"{joined}. "
        "Set them in Holosophus/.env (see .env_example and README Langfuse section)."
    )


def query_agent(
    history: List[ChatMessage],
    *,
    session_id: str | None = None,
    host: str = settings.DEFAULT_SERVER_HOST,
    port: int = settings.DEFAULT_SERVER_PORT,
    agent_name: str = "manager",
) -> Iterator[AgentEvent]:
    _validate_holosophos_required_env()
    url = _compose_agent_url(host, port, agent_name)

    serialized_history = [m.model_dump() for m in history]
    payload = {"messages": serialized_history, "stream": True}
    if session_id is not None:
        payload["session_id"] = session_id

    timeout = httpx.Timeout(connect=CONNECT_TIMEOUT, pool=None, read=None, write=None)
    with httpx.stream(
        REQUEST_TYPE, url, json=payload, headers=HEADERS, timeout=timeout
    ) as response:
        response.raise_for_status()
        last_event_at = time.monotonic()
        next_warning_at = last_event_at + LONG_SILENCE_WARNING_SECONDS

        for chunk in response.iter_text():
            now = time.monotonic()
            if now >= next_warning_at:
                logger.warning(
                    "No agent events received for %.1f seconds "
                    "(session_id=%s, agent=%s, url=%s)",
                    now - last_event_at,
                    session_id,
                    agent_name,
                    url,
                )
                next_warning_at = now + LONG_SILENCE_REPEAT_SECONDS

            if not chunk:
                continue
            try:
                event = AgentEvent.model_validate_json(chunk)
            except ValidationError:
                continue
            last_event_at = time.monotonic()
            next_warning_at = last_event_at + LONG_SILENCE_WARNING_SECONDS
            yield event


def stop_agent(
    session_id: str,
    host: str = settings.DEFAULT_SERVER_HOST,
    port: int = settings.DEFAULT_SERVER_PORT,
) -> bool:
    url = _compose_cancel_url(host, port)
    payload = {"session_id": session_id}
    timeout = httpx.Timeout(connect=CONNECT_TIMEOUT, pool=None, read=None, write=None)
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
        return True
    except httpx.HTTPError:
        return False
