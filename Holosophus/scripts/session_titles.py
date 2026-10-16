"""One optional title attempt, separate from the research conversation."""

import asyncio
import logging
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml
from cc_config import cli_environment
from holosophos import research_session, model_catalog

logger = logging.getLogger(__name__)
TITLE_TIMEOUT_SECONDS = 45
TITLE_PROMPT = yaml.safe_load(
    (
        Path(__file__).resolve().parents[1]
        / "holosophos/holosophos/prompts/session_title.yaml"
    ).read_text(encoding="utf-8")
)["prompt"]


async def request_title(prompt: str, model_config: dict | None = None) -> str:
    from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
    from run_research import resolve_cli_path
    from holosophos import model_catalog

    configured = model_catalog.claude_options(model_config or model_catalog.resolve("claude_code"))

    # Neutral cwd + explicit prompt: no project files, instructions, MCP, tools,
    # research history or persisted helper conversation. Keep normal CLI OAuth.
    with TemporaryDirectory(prefix="ai-researcher-title-") as directory:
        options = ClaudeAgentOptions(
            cli_path=resolve_cli_path(),
            cwd=directory,
            model=configured["model"],
            system_prompt=TITLE_PROMPT,
            tools=[],
            mcp_servers={},
            agents={},
            setting_sources=[],
            permission_mode="dontAsk",
            max_turns=1,
            thinking=configured.get("thinking"),
            effort=configured.get("effort"),
            env={**cli_environment(), **configured["env"]},
            extra_args={
                "no-session-persistence": None,
                "strict-mcp-config": None,
                "disable-slash-commands": None,
            },
        )
        result = None
        async for message in query(prompt=prompt[:6000], options=options):
            if isinstance(message, ResultMessage):
                if message.is_error:
                    raise ValueError("Title request failed")
                result = message.result
        if not isinstance(result, str):
            raise ValueError("Title response is missing")
        return result


async def generate_title_once(workdir: Path, prompt: str) -> None:
    try:
        # Exclusive durable claim survives feedback, reconnect and runner restart.
        marker = research_session._marker(workdir, "title_attempted")
        try:
            with marker.open("x", encoding="utf-8") as stream:
                stream.write(str(time.time()))
        except FileExistsError:
            return
        fallback = research_session.read_marker(workdir, "title")
        title = (
            await asyncio.wait_for(
                request_title(prompt, model_config=model_catalog.session_selection(workdir)),
                TITLE_TIMEOUT_SECONDS
            )
        ).strip()
        title = title.strip('"«»').strip()
        if (
            not title
            or len(title) > 120
            or "\n" in title
            or "\r" in title
            or title.startswith(("#", "```"))
        ):
            raise ValueError("Invalid title")
        # Preserve an operator's replacement and do not recreate a deleted session.
        if research_session.read_marker(workdir, "title") != fallback:
            return
        path = research_session._marker(workdir, "title")
        temporary = path.with_name(".title.tmp")
        temporary.write_text(title, encoding="utf-8")
        temporary.replace(path)
    except (OSError, ValueError, TimeoutError):
        logger.info("Session title unavailable; keeping fallback for %s", workdir.name)
    except Exception:
        # Optional naming must not fail research or trigger another model request.
        logger.info(
            "Session title request failed; keeping fallback for %s", workdir.name
        )
    finally:
        # Do not leave the UI polling forever after a failure or cancellation.
        # A killed process is covered by title_is_pending's expiry instead.
        if "fallback" in locals():
            try:
                marker.write_text("done", encoding="utf-8")
            except OSError:
                pass


_tasks: dict[Path, asyncio.Task] = {}


def start_title_task(workdir: Path, prompt: str) -> asyncio.Task | None:
    workdir = workdir.resolve()
    try:
        if workdir in _tasks or research_session.read_marker(
            workdir, "title_attempted"
        ):
            return None
        if research_session.read_marker(workdir, "title") != (
            " ".join(prompt.split())[:120] or "Research task"
        ):
            return None  # Existing human-set name.
    except (OSError, ValueError):
        return None
    task = asyncio.create_task(generate_title_once(workdir, prompt))
    _tasks[workdir] = task
    task.add_done_callback(lambda _: _tasks.pop(workdir, None))
    return task


async def cancel_title_task(task: asyncio.Task | None) -> None:
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
