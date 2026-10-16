#!/usr/bin/env python3
"""Run a research pipeline via Claude Agent SDK.

All agents from .claude/agents/ are available. Manager prompt from
holosophos/holosophos/prompts/system.yaml (adapted for Claude Code).
MCP servers from config/claude-code.yaml. Traces go to Langfuse v3 via langsmith bridge.

Usage:
    python scripts/run_research.py "Your research question"
    python scripts/run_research.py  # default question
"""

import asyncio
import json
import atexit
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings
from cc_config import cli_environment, load_mcp_servers

PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")

# A placeholder ANTHROPIC_API_KEY (empty or "...") overrides the Claude Code
# subscription credentials stored in macOS keychain — the spawned CLI then fails
# with "Invalid API key". Drop the var so the CLI falls back to keychain.
for _k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
    _v = os.environ.get(_k, "")
    if not _v or _v.strip() in {"", "...", "your_key_here", "replace_me"}:
        os.environ.pop(_k, None)


def resolve_cli_path() -> str | None:
    """Which ``claude`` binary the SDK should spawn.

    The SDK searches ``options.cli_path`` → the copy bundled inside the
    ``claude_agent_sdk`` wheel → ``PATH``. Because the bundle wins over ``PATH``,
    a newer CLI installed on the host is never reached, and the version gap is
    not only about features: **model aliases are resolved by the CLI**, so a
    stale bundle silently pins ``model="opus"`` to whichever Opus was current
    when that wheel shipped (2.1.126 resolves it to ``claude-opus-4-7``, while
    2.1.234 resolves it to ``claude-opus-5``).

    Prefer an explicit ``CLAUDE_CLI_PATH``, else the first ``claude`` on ``PATH``
    -- but never a batch shim. On Windows ``shutil.which`` finds ``claude.CMD``,
    a wrapper whose newline handling destroys multi-line arguments: CMD reads a
    newline as a command separator, so the manager prompt never arrives intact
    and the SDK waits out its whole handshake before reporting ``Control request
    timeout: initialize``. Measured 2026-08-20 on a 4210-char prompt: 70.2s and a
    timeout through ``claude.CMD``, 3.6s and success through ``claude.exe``.
    (Kept in step with ``scripts/cc_critic_runner.py::resolve_cli``, which needs
    the same rule but must not import this module -- importing it runs
    ``load_dotenv`` and the agent-sync side effects.)

    Returning ``None`` is the SDK's own default and falls back to the bundle, so
    a host with no installed CLI keeps working.
    """
    override = os.environ.get("CLAUDE_CLI_PATH", "").strip()
    if override:
        return override
    found = shutil.which("claude")
    if not found:
        return None
    if Path(found).suffix.lower() not in {".cmd", ".bat", ".ps1"}:
        return found
    real = (
        Path(found).parent
        / "node_modules"
        / "@anthropic-ai"
        / "claude-code"
        / "bin"
        / "claude.exe"
    )
    return str(real) if real.is_file() else found


# ── Config from .env ──


class Config(BaseSettings):
    # Langfuse v3 (langsmith bridge)
    langfuse_public_key_v3: str = ""
    langfuse_secret_key_v3: str = ""
    langfuse_base_url_v3: str = "http://localhost:3051"

    # MCP
    academia_port: int = 5056

    class Config:
        env_file = str(PROJECT_DIR / ".env")
        extra = "ignore"


cfg = Config()


# ── Agent definitions from .claude/agents/*.md ──

AGENTS_DIR = PROJECT_DIR / ".claude" / "agents"


def load_agent(name: str) -> dict:
    """Load agent .md, return parsed frontmatter + body.

    Fields returned: description, prompt, model, maxTurns, tools, mcpServers, disallowedTools.
    Sentinel ``model: inherit`` is mapped to None (SDK uses parent model).
    """
    import yaml

    path = AGENTS_DIR / f"{name}.md"
    text = path.read_text(encoding="utf-8")
    meta: dict = {}
    if text.startswith("---"):
        end = text.index("---", 3)
        meta = yaml.safe_load(text[3:end]) or {}
        text = text[end + 3 :].strip()

    model = meta.get("model")
    if model == "inherit":
        model = None

    return {
        "description": meta.get("description", name),
        "prompt": text,
        "model": model,
        "maxTurns": meta.get("maxTurns"),
        "tools": meta.get("tools"),
        "mcpServers": meta.get("mcpServers"),
        "disallowedTools": meta.get("disallowedTools"),
    }


def iter_research_agent_paths() -> list:
    """Research-runtime agent .md files in .claude/agents/.

    Excludes the `dev-` prefix, which is reserved for hand-written
    developer-harness agents (committed, see .gitignore). The research manager
    must never be able to delegate to a dev-harness agent, so they are filtered
    out here at the single source of truth for "which agents the manager sees".
    """
    return [p for p in sorted(AGENTS_DIR.glob("*.md")) if not p.stem.startswith("dev-")]


def load_all_agents() -> dict:
    """Load all research agents from .claude/agents/ (dev-* excluded)."""
    agents = {}
    for path in iter_research_agent_paths():
        name = path.stem  # e.g. "librarian", "mle-solver"
        agents[name] = load_agent(name)
    return agents


# ── Manager prompt (canonical source: holosophos/holosophos/prompts/system.yaml) ──


def load_manager_prompt(include_hitl: bool = False) -> str:
    """Build the Claude Code manager prompt from system.yaml's canon + claude_code sections.

    The same YAML feeds CodeArkt (via PromptStorage.compose) — keeping a single source
    of truth. We assemble: canon.role + canon.guidelines + canon.output_format
    + claude_code.prompt_extras. No regex-stripping of CodeArkt artifacts needed.

    ``include_hitl`` appends ``claude_code.hitl_prompt_extras`` (the conditioned ask_human rule).
    It is added ONLY when --hitl is on, so a normal run's prompt is byte-identical to today's
    (docs/plans/2026-08-12-hitl-cc.md).
    """
    import yaml

    prompts_path = PROJECT_DIR / "holosophos" / "holosophos" / "prompts" / "system.yaml"
    data = yaml.safe_load(prompts_path.read_text(encoding="utf-8"))
    canon = data.get("canon") or {}
    cc = data.get("claude_code") or {}
    sections = [
        canon.get("role", ""),
        canon.get("guidelines", ""),
        canon.get("output_format", ""),
        cc.get("prompt_extras", ""),
    ]
    if include_hitl:
        sections.append(cc.get("hitl_prompt_extras", ""))
    return "\n\n".join(s.strip() for s in sections if s and s.strip())


def pick_mle_kit_port() -> int:
    """Pick available mle_kit port via file lock. Returns 5057 if free, 5058 otherwise."""
    lock_dir = PROJECT_DIR / "workdir" / ".mle_locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    for port in (5057, 5058):
        lock_file = lock_dir / f"mle_{port}.lock"
        try:
            fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            atexit.register(lambda p=lock_file: p.unlink(missing_ok=True))
            return port
        except FileExistsError:
            continue
    return 5057


async def probe_mcp_servers(mcp_servers: dict, strict: bool = False) -> dict:
    """Probe each MCP server's ``tools/list`` endpoint, return {server_name: [tool_names]}.

    Reuses the MCP SDK pattern from ``codearkt/codearkt/tools.py``. Individual
    server failures print a warning and yield an empty list; with ``strict=True``
    any failure raises RuntimeError before any LLM cost is incurred.
    """
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    print("Preflight: probing MCP servers ...", flush=True)
    results: dict = {}
    failures: list = []

    for name, server_cfg in mcp_servers.items():
        url = server_cfg.get("url", "")
        headers = server_cfg.get("headers") or None
        try:
            async with streamablehttp_client(
                url, headers=headers, timeout=10, sse_read_timeout=30
            ) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    resp = await session.list_tools()
                    tools = [t.name for t in resp.tools]
                    results[name] = tools
                    print(f"  {name:<13} ✓ {len(tools)} tools", flush=True)
        except BaseException as e:  # ExceptionGroup is BaseException, not Exception
            # Unwrap ExceptionGroup so the real cause is visible (mcp client raises via anyio TaskGroup).
            cause = e
            while isinstance(cause, BaseExceptionGroup) and cause.exceptions:
                cause = cause.exceptions[0]
            failures.append((name, repr(cause)))
            results[name] = []
            print(
                f"  {name:<13} ✗ {cause.__class__.__name__}: {str(cause)[:100]}",
                flush=True,
            )

    if failures and strict:
        raise RuntimeError(
            f"Preflight failed for {len(failures)} MCP server(s): "
            + ", ".join(n for n, _ in failures)
        )
    if failures:
        print(
            f"  WARNING: {len(failures)} server(s) unreachable; agents using them will fail",
            flush=True,
        )

    return results


# ── Main ──

DEFAULT_QUESTION = (
    'Find recent (2025-2026) papers on "AI agents for science, multi-agent systems '
    'for scientific research". Understand the key ideas. Propose 3 topics for a new paper.'
)

# Continuation prompt issued after a durable --resume: the SDK has already replayed the full
# conversation, so the manager just needs a nudge to pick up where it left off (not the task again).
RESUME_NUDGE = (
    "Resume this run. Review the conversation so far and the artifacts already in your working "
    "directory (mle/results/, mle/checkpoints/, mle/data/), determine exactly "
    "where you left off, and continue the task from there. Do not restart or redo work that is "
    "already complete."
)

# Wrapper for an operator's mid-run steering message (HITL steer/inject), so the manager reads it as
# out-of-band human guidance rather than a new task. docs/plans/2026-08-13-steer-feedback-cc.md.
STEER_PREFIX = (
    "[OPERATOR MESSAGE — live steering from the human running this session. Take it into account "
    "and revise the affected plan and results; continue the task using completed work.]\n"
)
INJECT_PREFIX = STEER_PREFIX  # back-compat alias (inject == steer-now)


class _NeverError(Exception):
    """Placeholder exception used only if the SDK error classes can't be imported (never raised)."""


@dataclass
class RateLimitHit:
    """A detected subscription usage-limit that ended a turn.

    ``resets_at`` is a Unix epoch (seconds) or ``None`` (→ back off). ``rate_limit_type`` is the SDK
    enum (``five_hour``/``seven_day``/...). docs/plans/2026-08-13-rate-limit-resume-cc.md.
    """

    resets_at: float | None = None
    rate_limit_type: str | None = None


def _rate_hit(rate_info, err_text, run_control) -> "RateLimitHit | None":
    """Classify a turn-ending failure as a rate limit, structured-signal first.

    Primary: the SDK's typed ``RateLimitInfo`` with ``status == "rejected"`` (trustworthy epoch + type).
    Fallback: ``run_control.parse_rate_limit(err_text)`` over error/exception text. Returns ``None`` when
    it is not a usage limit — the caller then re-raises / treats the turn as a normal end (never swallows).
    """
    if rate_info is not None and getattr(rate_info, "status", None) == "rejected":
        return RateLimitHit(
            getattr(rate_info, "resets_at", None),
            getattr(rate_info, "rate_limit_type", None),
        )
    parsed = run_control.parse_rate_limit(err_text or "")
    if parsed is not None:
        return RateLimitHit(parsed["resets_at"], parsed["rate_limit_type"])
    return None


def build_hitl_server(workdir: Path, approvals=None, *, iteration_id=None):
    """Shared manager-only clarification/approval tools for UI and CLI."""
    import approval_control

    return approval_control.build_hitl_server(
        workdir, approvals=approvals, iteration_id=iteration_id
    )


def _render_manager_message(
    msg, start: float, workdir: Path, *, include_result_output=True
) -> bool:
    """Print one SDK message without modifying the user-facing document."""
    mt = type(msg).__name__
    ptui = getattr(msg, "parent_tool_use_id", None)

    if mt == "AssistantMessage" and not ptui:
        for b in msg.content or []:
            bt = type(b).__name__
            if bt == "TextBlock" and b.text:
                print(f"  [mgr] {b.text}", flush=True)
            elif bt == "ToolUseBlock":
                desc = (
                    b.input.get("description", "") if isinstance(b.input, dict) else ""
                )
                agent = (
                    b.input.get("subagent_type", "")
                    if isinstance(b.input, dict)
                    else ""
                )
                label = agent or desc
                print(f'  [mgr] → {b.name} "{label[:80]}"', flush=True)
    elif mt == "TaskNotificationMessage":
        status = getattr(msg, "status", "?")
        summary = (getattr(msg, "summary", "") or "")[:120]
        print(f"  [{status}] {summary}", flush=True)
    elif mt == "ResultMessage":
        cost = getattr(msg, "total_cost_usd", None)
        turns = getattr(msg, "num_turns", 0)
        dur = time.time() - start
        result = getattr(msg, "result", "") or ""
        cost_str = f"${cost:.2f}" if cost else "?"
        print(f"\n{'=' * 60}", flush=True)
        print(f"  Done: {turns} turns, {dur:.0f}s, {cost_str}", flush=True)
        print(f"{'=' * 60}\n", flush=True)
        if include_result_output:
            print(result[:3000], flush=True)
        return True
    return False


async def _hold_until_resume(
    ctl_state: dict, run_control, workdir: Path, poll_interval: float, unfreeze
):
    """Block while soft-paused until 'resume' or 'stop'.

    Returns the next query on resume: the operator's feedback (``STEER_PREFIX + text``) if a steer was
    written while paused — the pause→review→resume flow — otherwise the generic ``RESUME_NUDGE``. Returns
    ``None`` on 'stop'. The concurrent poller keeps consuming the control file into ``ctl_state`` while we
    hold, so a steer's text lands in ``ctl_state["pending_steer"]`` (its 'now' interrupt is a harmless
    no-op with no active turn).
    """
    while True:
        await asyncio.sleep(poll_interval)
        cmd = ctl_state["cmd"]
        if cmd == "resume":
            ctl_state["cmd"] = None
            unfreeze(workdir)  # Tier-1 thaw (no-op if we never froze / no sandbox)
            run_control.clear_checkpoint_stop(workdir)
            steer_text = ctl_state.pop("pending_steer", None)
            if steer_text:
                print("  [ctl] resuming with operator feedback.", flush=True)
                return STEER_PREFIX + steer_text
            print("  [ctl] resuming.", flush=True)
            return RESUME_NUDGE
        if cmd == "stop":
            ctl_state["cmd"] = None
            run_control.set_checkpoint_stop(workdir)
            print(
                "  [ctl] stop while paused — exit. Resume later with --resume.",
                flush=True,
            )
            return None


async def _drive_with_pause_control(
    client,
    first_query: str,
    session_name: str,
    workdir: Path,
    start: float,
    render=_render_manager_message,
    poll_interval: float = 1.0,
    freeze=None,
    unfreeze=None,
    completion=None,
) -> "RateLimitHit | None":
    """Run the manager, honoring pause/resume/stop from the ``.holosophus_ctl/`` control channel.

    - ``pause``  → `interrupt()` the current turn, then hold (process idle/alive) until resume/stop.
    - ``resume`` → continue in-process with a nudge (the SDK subprocess retains full context).
    - ``stop``   → set the Tier-2 ``checkpoint_stop`` flag, end the turn, exit cleanly (durably
      resumable via ``--resume``).

    A concurrent poller drives ``interrupt()`` so a pause lands even while the agent is blocked
    awaiting a long tool call (the sandbox freeze that also covers that case is a separate layer).

    Returns ``None`` on natural completion / operator stop, or a ``RateLimitHit`` when the subscription
    usage limit ends a turn (detected via the SDK's ``RateLimitEvent`` or, on a dead subprocess, the
    error text) — the caller (main) then waits for the reset and reconnects via ``--resume``.
    """
    sys.path.insert(0, str(PROJECT_DIR / "scripts"))
    import cc_run_control as run_control

    try:
        from claude_agent_sdk import (
            RateLimitEvent,
        )  # typed rate-limit event on the message stream
        from claude_agent_sdk import ProcessError, CLIJSONDecodeError, ClaudeSDKError
    except Exception:  # noqa: BLE001 — keep the driver importable even if the SDK surface shifts
        RateLimitEvent = ()  # isinstance(x, ()) is always False
        ProcessError = CLIJSONDecodeError = ClaudeSDKError = _NeverError

    if freeze is None:
        freeze = run_control.freeze_sandbox
    if unfreeze is None:
        unfreeze = run_control.unfreeze_sandbox

    run_control.clear_checkpoint_stop(workdir)
    ctl_state = {"cmd": None}
    stop_poll = asyncio.Event()

    async def _interrupt() -> None:
        if ctl_state.get("sdk_idle"):
            return  # No model turn is active; the reader is woken by operator state below.
        try:
            await client.interrupt()
        except Exception as e:  # noqa: BLE001 — interrupt with no active turn is fine
            print(f"  [ctl] interrupt failed: {e}", flush=True)

    async def _poller() -> None:
        while not stop_poll.is_set():
            cmd = run_control.poll_command(workdir)
            if completion is not None and cmd in {"pause", "stop"}:
                completion.defer_completion = True
            if cmd == "steer":
                # Non-terminal steering with a break mode. Stash the text (single slot: latest wins);
                # mode "now" interrupts the current step, "next" waits for the natural turn boundary.
                s = run_control.read_steer(workdir)
                if s and s.get("text"):
                    if completion is not None:
                        completion.defer_completion = True
                    ctl_state["pending_steer"] = s["text"]
                    if s.get("mode") == "now":
                        await _interrupt()
            elif cmd == "inject":
                # Alias: inject == steer-now (reads inject_msg). Empty payload → ignore (no interrupt).
                text = run_control.read_inject(workdir)
                if text:
                    if completion is not None:
                        completion.defer_completion = True
                    ctl_state["pending_steer"] = text
                    await _interrupt()
            elif cmd:
                ctl_state["cmd"] = cmd
                if cmd == "stop":
                    import approval_control

                    # Revoke the pending decision before waiting on SDK teardown:
                    # a slow interrupt must not allow its timer to grant approval.
                    approval_control.cancel_pending(workdir)
                    run_control.set_checkpoint_stop(
                        workdir
                    )  # ask chunked jobs to checkpoint+exit
                if cmd in ("pause", "stop"):
                    await _interrupt()
                if cmd == "pause" and freeze(workdir):
                    print(
                        "  [ctl] sandbox frozen (docker pause) — job halted in place.",
                        flush=True,
                    )
            try:
                await asyncio.wait_for(stop_poll.wait(), timeout=poll_interval)
            except asyncio.TimeoutError:
                pass

    poll_task = asyncio.create_task(_poller())
    try:
        pending = first_query
        while True:
            if completion is not None:
                completion.begin_turn()
            rate_info = (
                None  # latest SDK RateLimitInfo seen this turn (structured detector)
            )
            result_msg = (
                None  # terminal ResultMessage (for the error-text fallback detector)
            )
            try:
                ctl_state["sdk_idle"] = pending is None
                if pending is not None:
                    await client.query(pending, session_id=session_name)

                async def receive_turn():
                    nonlocal rate_info, result_msg
                    async for msg in client.receive_response():
                        if isinstance(msg, RateLimitEvent):
                            rate_info = msg.rate_limit_info
                            st = getattr(rate_info, "status", "?")
                            if st != "allowed":
                                if completion is not None and st == "rejected":
                                    completion.defer_completion = True
                                print(f"  [ratelimit] status={st} type={getattr(rate_info, 'rate_limit_type', None)}", flush=True)
                            continue
                        if type(msg).__name__ == "TaskNotificationMessage" or (
                            type(msg).__name__ == "AssistantMessage"
                            and not getattr(msg, "parent_tool_use_id", None)
                        ):
                            ctl_state["sdk_idle"] = False
                        render(msg, start, workdir)
                        if type(msg).__name__ == "ResultMessage":
                            result_msg = msg

                reader = asyncio.create_task(receive_turn())
                operator_wake = False
                try:
                    while not reader.done():
                        await asyncio.wait({reader}, timeout=poll_interval)
                        if ctl_state.get("sdk_idle") and (
                            ctl_state.get("cmd") in {"pause", "stop"} or ctl_state.get("pending_steer")
                        ):
                            operator_wake = True
                            reader.cancel()
                            break
                    await reader
                except asyncio.CancelledError:
                    if not operator_wake or asyncio.current_task().cancelling():
                        raise
                finally:
                    if not reader.done():
                        reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)
            except (ProcessError, CLIJSONDecodeError, ClaudeSDKError) as e:
                # Subprocess died / stream errored. If it's the usage limit, hand it up; else re-raise.
                hit = _rate_hit(
                    rate_info, f"{e} {getattr(e, 'stderr', '') or ''}", run_control
                )
                if hit is None:
                    raise
                return hit
            # Turn ended: a control interrupt, natural completion, or an error result.
            # Precedence: operator stop/pause → subscription limit → steer delivery → completion.
            cmd = ctl_state["cmd"]
            ctl_state["cmd"] = None
            if cmd == "stop":
                print(
                    "  [ctl] stop — checkpointing; exit. Resume later with --resume.",
                    flush=True,
                )
                return None
            if cmd == "pause":
                print(
                    "  [ctl] paused — holding. Write 'resume' or 'stop' to the control file.",
                    flush=True,
                )
                pending = await _hold_until_resume(
                    ctl_state, run_control, workdir, poll_interval, unfreeze
                )
                if pending is None:  # stop while held
                    return None
                continue
            # No operator stop/pause: did the subscription usage limit end the turn?
            err_text = (
                getattr(result_msg, "result", "") or ""
                if result_msg is not None and getattr(result_msg, "is_error", False)
                else ""
            )
            hit = _rate_hit(rate_info, err_text, run_control)
            if hit is not None:
                return hit
            # Steer delivery (same path for --now, which interrupted, and --next, which waited here).
            steer_text = ctl_state.pop("pending_steer", None)
            if steer_text:
                print(
                    "  [ctl] steer — delivering operator steering message.", flush=True
                )
                pending = STEER_PREFIX + steer_text
                continue  # non-terminal: re-query with the human's message, keep going
            if completion is not None and (completion.activity.active or completion.waiting_at_stop) and not getattr(result_msg, "is_error", False):
                if result_msg is None:
                    raise RuntimeError("SDK stream ended before background agents completed")
                # receive_response stops at one ResultMessage, not the whole research task.
                # Read the next native completion/continuation without another model query.
                pending = None
                continue
            if completion is not None:
                completion.end_turn()
            return None  # natural completion (or an empty interrupted turn) — do not re-query
    finally:
        stop_poll.set()
        await poll_task


# Heartbeat cadence during a rate-limit hold. Must stay well under nginx's 3600 s
# `proxy_read_timeout` on /agents/ so a UI run's open stream is not idle-timed out
# (nginx `proxy_buffering off` lets each tick flush immediately). Module-level so tests
# can force it and ops can tune it.
_RATE_LIMIT_HEARTBEAT_S = 60.0


async def _wait_for_reset(
    workdir: Path,
    resets_at: float | None,
    rate_limit_type: str | None,
    run_control,
    buffer_s: float,
    backoff_s: float,
    poll_interval: float = 5.0,
    on_wait_tick=None,
) -> bool:
    """Hold until the usage window resets, interruptibly (main-level; no concurrent poller running).

    Waits until ``resets_at + buffer_s`` (or ``now + backoff_s`` when the reset time is unknown).
    Returns ``True`` to retry (window elapsed, or operator wrote ``resume``), ``False`` to exit durably
    (operator wrote ``stop``). A ``resume`` clears any stale ``checkpoint_stop`` (mirrors the pause hold).

    ``on_wait_tick(remaining_s)`` — optional heartbeat fired on a **dedicated 60 s gate** (distinct
    from the 300 s stdout countdown). The CLI passes nothing (stdout print only); the UI runner passes
    a callback that enqueues a ``waiting_rate_limit`` event, so a multi-hour hold flushes through the
    open stream well under nginx's 3600 s idle cap. Best-effort: a callback error must not break the
    wait.
    """
    now = time.time()
    if resets_at:
        target = float(resets_at) + buffer_s
        if target <= now:
            target = now + 5.0  # reset already passed → brief settle, then retry
    else:
        target = now + backoff_s
    print(
        f"  [ratelimit] {rate_limit_type or 'usage'} limit — auto-waiting ~{max(0.0, (target - now) / 60.0):.0f} "
        "min until reset, then resuming via --resume. (write 'stop' to exit, 'resume' to retry now)",
        flush=True,
    )
    last_log = now
    last_heartbeat = now
    while True:
        now = time.time()
        if now >= target:
            return True
        cmd = run_control.poll_command(workdir)
        if cmd == "stop":
            run_control.set_checkpoint_stop(workdir)
            return False
        if cmd == "resume":
            run_control.clear_checkpoint_stop(workdir)
            print("  [ratelimit] resume — retrying now.", flush=True)
            return True
        if now - last_log >= 300:  # countdown every 5 min
            print(
                f"  [ratelimit] ~{(target - now) / 60.0:.0f} min until reset ...",
                flush=True,
            )
            last_log = now
        if (
            on_wait_tick is not None and now - last_heartbeat >= _RATE_LIMIT_HEARTBEAT_S
        ):  # keep UI stream alive
            try:
                on_wait_tick(max(0.0, target - now))
            except Exception as e:  # noqa: BLE001 — a heartbeat error must not break the wait
                print(f"  [ratelimit] wait-tick callback error: {e}", flush=True)
            last_heartbeat = now
        await asyncio.sleep(min(poll_interval, max(1.0, target - now)))


def _resume_command_hint(workdir: Path) -> str:
    """The exact command to resume this run later (printed on --no-auto-wait / weekly cap / give-up)."""
    return f"uv run python scripts/run_research.py --workdir {workdir} --resume"


async def _drive_across_limits(
    client_factory,
    workdir: Path,
    session_name: str,
    start: float,
    run_control,
    *,
    first_query: str,
    start_resume: bool = False,
    no_auto_wait: bool = False,
    buffer_s: float = 60.0,
    backoff_s: float = 600.0,
    max_waits: int = 24,
    render=_render_manager_message,
    poll_interval: float = 1.0,
    freeze=None,
    unfreeze=None,
    on_wait_tick=None,
    driver=_drive_with_pause_control,
    waiter=_wait_for_reset,
    completion=None,
) -> None:
    """Own the client lifecycle across subscription usage limits (SC3–SC6).

    Runs the driver under a fresh client; on a ``RateLimitHit`` it marks + waits for the reset
    (interruptibly), then reconnects in resume mode (``--resume`` replays the transcript). ``seven_day``
    caps and ``no_auto_wait`` print the resume command and exit; repeated limits are capped at
    ``max_waits``. ``client_factory(resume: bool)`` returns an ``async with``-able client for the mode.

    ``render``/``poll_interval``/``freeze``/``unfreeze`` are forwarded verbatim to the inner driver,
    and ``on_wait_tick`` to the waiter — all default to the CLI behavior (stdout render, real sandbox
    freeze, print-only wait), so existing CLI callers and tests are unaffected. The UI runner passes an
    event-queue ``render`` and a ``waiting_rate_limit`` ``on_wait_tick`` to reach the browser.
    """
    pending = first_query
    resume = start_resume
    waits = 0
    while True:
        # A resumed CLI attempt is no longer in the previous process's wait.
        run_control.clear_rate_limit(workdir)
        async with client_factory(resume) as client:
            # Positional order mirrors _drive_with_pause_control's signature
            # (render, poll_interval, freeze, unfreeze) so injected test fakes
            # with a ``*a`` catch-all keep working unchanged (SC6).
            hit = await driver(
                client,
                pending,
                session_name,
                workdir,
                start,
                render,
                poll_interval,
                freeze,
                unfreeze,
                **({"completion": completion} if completion is not None else {}),
            )
        if hit is None:
            return  # natural completion or operator stop
        if completion is not None:
            completion.close_background_connection()
        run_control.write_rate_limit(workdir, hit.resets_at, hit.rate_limit_type)
        reset_hint = _resume_command_hint(workdir)
        rtype = hit.rate_limit_type or "usage"
        weekly = (hit.rate_limit_type or "").startswith("seven_day")
        if no_auto_wait or weekly:
            why = (
                "weekly cap — impractical to wait in-process"
                if weekly
                else "--no-auto-wait"
            )
            when = (
                datetime.fromtimestamp(hit.resets_at).isoformat(timespec="minutes")
                if hit.resets_at
                else "unknown"
            )
            print(
                f"\nRate limit hit ({rtype}; {why}). Resets: {when}.\n"
                f"Resume later with:\n  {reset_hint}",
                flush=True,
            )
            return
        if waits >= max_waits:
            print(
                f"\nRate limit hit {max_waits}× — giving up to avoid an unbounded loop.\n"
                f"Resume later with:\n  {reset_hint}",
                flush=True,
            )
            return
        waits += 1
        proceed = await waiter(
            workdir,
            hit.resets_at,
            hit.rate_limit_type,
            run_control,
            buffer_s,
            backoff_s,
            on_wait_tick=on_wait_tick,
        )
        run_control.clear_rate_limit(workdir)
        if not proceed:  # operator wrote 'stop' during the wait
            print(
                "  [ratelimit] stopped during wait — exiting. Resume later with --resume.",
                flush=True,
            )
            return
        resume = True
        pending = (
            completion.query(RESUME_NUDGE, mode="resume")
            if completion
            else RESUME_NUDGE
        )


def dump_prompts(
    research_profile: str = "general", *, workdir: Path | None = None, approvals=None
) -> None:
    """Print manager_prompt and every sub-agent prompt; do not contact LLM/MCP."""
    sys.path.insert(0, str(PROJECT_DIR / "scripts"))
    from sync_claude_agents import ensure_synced
    import approval_control
    from result_contract import RESULT_PROMPT

    policy = approval_control.normalize_policy(approvals)
    extra = "\n\n" + RESULT_PROMPT
    if policy.enabled:
        extra += "\n\n" + approval_control.APPROVAL_RULES
        print("ITERATION APPROVAL CONTEXT (sent as a new message)")
        print(approval_control.approval_prompt(workdir or Path.cwd(), policy))

    if research_profile == "chemistry":
        import research_profiles

        print("MANAGER (chemistry)")
        print(
            research_profiles.load_chemistry_manager_prompt(
                include_hitl=policy.enabled
            ).replace("{{SESSION_NAME}}", "<dump-session>")
            + extra
        )
        for name, agent in research_profiles.load_chemistry_agents().items():
            print(f"\nSUB-AGENT: {name}\n{agent['prompt']}")
        return
    ensure_synced()
    manager = (
        load_manager_prompt(include_hitl=policy.enabled).replace(
            "{{SESSION_NAME}}", "<dump-session>"
        )
        + extra
    )
    print("=" * 70)
    print("MANAGER (system_prompt for ClaudeSDKClient)")
    print(f"source: holosophos/holosophos/prompts/system.yaml  ({len(manager)} chars)")
    print("=" * 70)
    print(manager)

    for path in iter_research_agent_paths():
        text = path.read_text(encoding="utf-8")
        print()
        print("=" * 70)
        print(f"SUB-AGENT: {path.stem}")
        print(f"source: {path.relative_to(PROJECT_DIR)}  ({len(text)} chars total)")
        print("=" * 70)
        print(text)


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run research pipeline")
    parser.add_argument("--effort", default=None, help="Effort allowed by the selected model configuration")
    parser.add_argument("--compute-mode", choices=("Local", "GPU", "Auto"), default=None)
    parser.add_argument(
        "--research-profile",
        choices=("general", "chemistry"),
        default=None,
        help="Research domain; resume uses the saved session profile",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Claude model alias or ID (default opus; resume uses saved model)",
    )
    parser.add_argument(
        "question", nargs="*", default=[DEFAULT_QUESTION], help="Research question"
    )
    parser.add_argument(
        "--prompt-file",
        type=str,
        default=None,
        help="Read the research question from a file instead of argv. Avoids shell "
        "quoting/substitution pitfalls (e.g. cmd.exe does not expand $(...), so a "
        'bare "$(Get-Content ...)" is passed literally as the question).',
    )
    parser.add_argument(
        "--workdir",
        type=str,
        default=None,
        help="Reuse existing workdir (continue previous run)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Durably resume the run in --workdir: replay the real SDK conversation (via the "
        "persisted session id in .holosophus_ctl/session.json) instead of a cold re-run. "
        "Requires --workdir; falls back to a cold re-run if no session id is found.",
    )
    parser.add_argument(
        "--strict-preflight",
        action="store_true",
        help="Abort if any MCP server fails the tools/list probe",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=300,
        help="Override the manager's hard turn cap (default 300)",
    )
    parser.add_argument(
        "--hitl",
        action="store_true",
        help="Require sources and plan approval, without automatic approval.",
    )
    parser.add_argument(
        "--approvals",
        help='Approval settings as JSON: {"sources":true,"plan":false,"auto_approve_after_seconds":null}. Overrides --hitl.',
    )
    parser.add_argument(
        "--no-auto-wait",
        action="store_true",
        help="On a subscription usage limit, do NOT auto-wait for the reset; print the reset time + "
        "the exact --resume command and exit (for cron/supervisor-driven resume). Default: auto-wait "
        "until the 5-hour window renews, then resume in place.",
    )
    parser.add_argument(
        "--dump-prompts",
        action="store_true",
        help="Print manager_prompt and every sub-agent prompt, then exit "
        "without contacting Langfuse, MCP, or the LLM",
    )
    args = parser.parse_args()
    if args.prompt_file:
        question = Path(args.prompt_file).read_text(encoding="utf-8").strip()
    else:
        question = " ".join(args.question)

    import research_profiles
    import approval_control
    from holosophos import research_session, research_iterations, model_catalog
    from result_contract import RESULT_PROMPT, prepare_cli_iteration

    requested_workdir = None
    if args.workdir:
        requested_workdir = Path(args.workdir)
        if not requested_workdir.is_absolute():
            requested_workdir = PROJECT_DIR / requested_workdir
    try:
        profile = (
            research_session.resolve_research_profile(
                requested_workdir, args.research_profile
            )
            if requested_workdir is not None
            else args.research_profile or "general"
        )
    except ValueError as error:
        parser.error(str(error))
    model = (
        args.model
        or (
            research_session.read_marker(requested_workdir, "model")
            if requested_workdir
            else None
        )
        or research_session.CC_DEFAULT_MODEL
    )

    saved_settings = (
        research_session.read_session_settings(requested_workdir)
        if args.resume and requested_workdir
        else {}
    )
    if not args.dump_prompts:
        selection = (model_catalog.session_selection(requested_workdir) if args.resume and requested_workdir
                     else model_catalog.resolve("claude_code", args.model, args.effort))
        if args.resume and ((args.model and args.model != selection["id"])
                            or (args.effort and args.effort != selection.get("effort"))):
            parser.error("Model and effort are fixed for this session")
        model = selection["id"]
        configured_model = model_catalog.claude_options(selection)
    try:
        approval_data = (
            json.loads(args.approvals)
            if args.approvals is not None
            else (None if args.hitl else saved_settings.get("approvals"))
        )
        policy = approval_control.normalize_policy(
            approval_data, hitl=args.hitl or bool(saved_settings.get("hitl", False))
        )
    except ValueError as error:
        parser.error(str(error))

    if args.dump_prompts:
        dump_prompts(profile, workdir=requested_workdir, approvals=policy)
        return
    workspace_root = Path(os.environ.get("WORKSPACE_DIR") or PROJECT_DIR / "workdir").resolve()
    if requested_workdir is not None:
        try:
            expected_dir = research_session.session_directory(workspace_root, requested_workdir.name)
            if requested_workdir.resolve() != expected_dir.resolve():
                raise ValueError("--workdir must be a session directory in shared WORKSPACE_DIR")
        except ValueError as error:
            parser.error(str(error))
    if profile == "chemistry":
        await research_profiles.check_chemistry_services()

    # Both entry points share the same optional Langfuse/Phoenix tracing setup.
    sys.path.insert(0, str(PROJECT_DIR / "codearkt"))
    import cc_research_lib

    langfuse, propagate_attributes = cc_research_lib.init_langfuse_tracing()

    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
    from claude_agent_sdk.types import AgentDefinition, ThinkingConfigAdaptive

    # ── Session naming / workdir ──
    if args.workdir:
        workdir = Path(args.workdir)
        if not workdir.is_absolute():
            workdir = PROJECT_DIR / workdir
        session_name = workdir.name
    else:
        session_name = research_session.new_session_id()
        workdir = workspace_root / session_name
    workdir.mkdir(parents=True, exist_ok=True)
    if not args.resume:
        research_session.initialize_display_metadata(workdir, question)
    research_session.save_research_profile(workdir, profile)
    research_session.save_session_model(workdir, model)
    model_catalog.save_selection(workdir, selection)
    session_settings = {
        "compute_mode": research_profiles.compute_mode(
            profile, saved_settings.get("compute_mode") if args.resume else args.compute_mode
        ),
        "approvals": policy.model_dump(),
        "model_label": selection["label"],
        "effort": selection.get("effort"),
    }

    # ── Durable session id for pause/restart (docs/plans/2026-08-12-pause-restart-cc.md) ──
    # A NEW run pins a chosen UUID as options.session_id (→ CLI --session-id) and persists it,
    # so --resume can later replay the *real* SDK conversation instead of the lossy cold
    # re-run. --resume reads that id back and sets options.resume instead.
    sys.path.insert(0, str(PROJECT_DIR / "scripts"))
    import cc_run_control as run_control

    sdk_session_id = None  # NEW run  → ClaudeAgentOptions(session_id=...)
    sdk_resume_id = None  # --resume → ClaudeAgentOptions(resume=...)
    if args.resume:
        info = run_control.read_session_info(workdir)
        if info:
            sdk_resume_id = info["session_id"]
            print(f"Resume:   replaying SDK session {sdk_resume_id}", flush=True)
        else:
            raise ValueError(
                "Saved SDK conversation is missing; cannot resume this session"
            )
    if sdk_resume_id is None:
        sdk_session_id = run_control.new_session_id()
        run_control.write_session_info(workdir, sdk_session_id, question)

    # ── Paperbanana: bridge its hardcoded global OUTPUT_DIR into session cwd ──
    # paperbanana MCP writes to /workdir/figures/paperbanana/ (compose env, no
    # session awareness in tool input schema). Symlink that shared dir under
    # the session workdir so writer can reference figures with paths relative
    # to its cwd (e.g. ``figures/paperbanana/run_<id>/final_output.png``).
    #
    # Mirrors codearkt.session_id.ensure_session_scaffold and
    # cc_research_lib.ensure_figures_symlink 1:1: a RELATIVE target (so the tree
    # resolves identically on the host and inside every container that mounts
    # ./workdir at /workdir — an absolute host path would dangle in-container),
    # and best-effort (never raises). On Windows, creating a symlink needs
    # Developer Mode / elevation; without it we skip the bridge rather than abort
    # the whole run at startup — paperbanana figures then just aren't reachable
    # via session-relative paths (the feature degrades; the run continues), same
    # as the CodeArkt and CC-UI-runner paths already do.
    try:
        pb_global = PROJECT_DIR / "workdir" / "figures" / "paperbanana"
        pb_global.mkdir(parents=True, exist_ok=True)
        session_figs = workdir / "figures"
        session_figs.mkdir(parents=True, exist_ok=True)
        session_pb = session_figs / "paperbanana"
        if not session_pb.exists() and not session_pb.is_symlink():
            rel_target = os.path.relpath(pb_global, start=session_pb.parent)
            session_pb.symlink_to(rel_target, target_is_directory=True)
    except OSError:
        pass

    # ── Regenerate .claude/agents/*.md from holosophos/holosophos/prompts/*.yaml ──
    # Single source of truth is the YAML; the .md files are gitignored artifacts
    # rebuilt every run so the SDK always reads what the YAML currently says.
    sys.path.insert(0, str(PROJECT_DIR / "scripts"))
    from sync_claude_agents import ensure_synced

    # ── Load agents + MCP + prompt ──
    if profile == "chemistry":
        raw_agents = research_profiles.load_chemistry_agents(session_settings["compute_mode"])
        manager_prompt = research_profiles.load_chemistry_manager_prompt(
            include_hitl=policy.enabled
        )
    else:
        ensure_synced()
        raw_agents = load_all_agents()
        manager_prompt = load_manager_prompt(include_hitl=policy.enabled)
    manager_prompt += "\n\n" + RESULT_PROMPT
    manager_prompt += "\n\n" + research_profiles.execution_prompt(session_settings["compute_mode"])
    raw_agents = cc_research_lib.filter_agents_by_compute_mode(raw_agents, session_settings["compute_mode"])
    if policy.enabled:
        manager_prompt += "\n\n" + approval_control.APPROVAL_RULES
    mcp_servers = load_mcp_servers()
    if profile == "chemistry":
        mcp_servers = research_profiles.chemistry_mcp_servers(mcp_servers)
    manager_prompt = manager_prompt.replace("{{SESSION_NAME}}", session_name)

    # ── MLE Kit port selection (parallel run support) ──
    # Chemistry shares the configured session-aware local service. The legacy
    # alternate port can belong to ui-api in this stack.
    if profile != "chemistry":
        mle_port = pick_mle_kit_port()
        if "mle_kit" in mcp_servers:
            mcp_servers["mle_kit"]["url"] = f"http://localhost:{mle_port}/mcp"

    # ── AlphaXiv Bearer injection (headless OAuth: see scripts/alphaxiv_auth.py) ──
    if "alphaxiv" in mcp_servers:
        sys.path.insert(0, str(PROJECT_DIR / "scripts"))
        try:
            from alphaxiv_auth import get_access_token

            token = get_access_token()
        except Exception as e:
            token = None
            print(
                f"AlphaXiv: auth module error ({e}); dropping from mcp_servers",
                flush=True,
            )
        if token:
            mcp_servers["alphaxiv"]["headers"] = {"Authorization": f"Bearer {token}"}
            print(f"AlphaXiv: authenticated ({token[:18]}...)", flush=True)
        else:
            print(
                "AlphaXiv: no token; run `uv run python scripts/alphaxiv_auth.py login` "
                "to enable. Dropping from mcp_servers for this run.",
                flush=True,
            )
            del mcp_servers["alphaxiv"]

    # Default mcpServers per agent: all configured servers, unless frontmatter overrides.
    # Without this sub-agents only see Claude Code built-ins (Read/Write/WebFetch).
    all_server_names = list(mcp_servers.keys())
    agents = {}
    for name, a in raw_agents.items():
        agent_servers = (
            a["mcpServers"] if a["mcpServers"] is not None else all_server_names
        )
        agents[name] = AgentDefinition(
            description=a["description"],
            prompt=a["prompt"],
            model=a["model"],
            maxTurns=a["maxTurns"],
            tools=a["tools"],
            mcpServers=agent_servers,
            disallowedTools=a["disallowedTools"],
        )

    print(f"Session:  {session_name}", flush=True)
    print(f"Workdir:  {workdir}", flush=True)
    print(f"MCP:      {', '.join(mcp_servers.keys())}", flush=True)
    print(
        f"MLE Kit:  {mcp_servers.get('mle_kit', {}).get('url', 'not configured')}",
        flush=True,
    )
    print(f"Langfuse: {cfg.langfuse_base_url_v3}", flush=True)
    print(f"Question: {question[:120]}", flush=True)
    print(flush=True)
    print("Agents:", flush=True)
    for name, a in raw_agents.items():
        ag_def = agents[name]
        n_servers = len(ag_def.mcpServers or [])
        print(
            f"  {name:<20} model={a['model']!r:<10} maxTurns={a['maxTurns']} "
            f"mcpServers={n_servers}",
            flush=True,
        )
    print(flush=True)

    # ── Preflight: probe each MCP server (fails fast under --strict-preflight) ──
    await probe_mcp_servers(mcp_servers, strict=args.strict_preflight)
    print(flush=True)

    # ── HITL: register the in-process ask_human tool (docs/plans/2026-08-12-hitl-cc.md) ──
    # In-process SDK MCP server → the handler runs here with direct workdir access, no HTTP server.
    # Registered AFTER preflight (it has no URL to probe) and only when --hitl, so a normal run is
    # byte-identical. Manager-only: it goes into mcp_servers (the manager's registry) but NOT into
    # any sub-agent's mcpServers allowlist (already computed above), so sub-agents can't call it.
    completion = prepare_cli_iteration(
        workdir,
        resume=bool(sdk_resume_id),
        sdk_session_id=sdk_resume_id or sdk_session_id,
        question=question,
        settings={
            "research_profile": profile,
            "model_name": model,
            **session_settings,
        },
    )
    research_session.save_session_settings(workdir, session_settings)
    if policy.enabled:
        mcp_servers["hitl"], ask_timeout = build_hitl_server(
            workdir, approvals=policy, iteration_id=completion.iteration_id
        )
        print(
            f"HITL:     ask_human enabled (answer timeout {int(ask_timeout)}s)",
            flush=True,
        )

    start = time.time()

    options_kwargs = dict(
        system_prompt=manager_prompt,
        cwd=str(workdir),
        model=model,
        cli_path=resolve_cli_path(),
        thinking=ThinkingConfigAdaptive(type="adaptive"),
        effort="max",
        max_turns=args.max_turns,
        permission_mode="bypassPermissions",
        # Research roles/hooks come from the SDK, not personal/project plugins.
        setting_sources=[],
        disallowed_tools=["ScheduleWakeup"],
        mcp_servers=mcp_servers,
        agents=agents,
        # SDK default 1 MiB is too small: a single Agent("writer") invocation can
        # easily exceed it when the manager passes the accumulated literature
        # context. 1 MiB → 32 MiB is plenty even for full-paper assembly.
        max_buffer_size=32 * 1024 * 1024,
    )
    if profile == "chemistry":
        from claude_agent_sdk import HookMatcher

        if session_settings["compute_mode"] == "Local":
            options_kwargs["disallowed_tools"] += research_profiles.REMOTE_TOOLS
        options_kwargs["hooks"] = {
            "PreToolUse": [
                HookMatcher(
                    hooks=[research_profiles.ChemistryToolGate(session_name, workdir, session_settings["compute_mode"])]
                )
            ]
        }
    from claude_agent_sdk import HookMatcher
    from session_tools import MleSessionToolGate

    options_kwargs.setdefault("hooks", {}).setdefault("PreToolUse", []).append(
        HookMatcher(hooks=[MleSessionToolGate(session_name, workdir)])
    )
    if policy.enabled:
        options_kwargs.setdefault("hooks", {}).setdefault("PreToolUse", []).append(
            HookMatcher(
                hooks=[
                    approval_control.ApprovalGate(
                        workdir, policy, iteration_id=completion.iteration_id,
                        activity=completion.activity,
                    )
                ],
            )
        )
    from claude_agent_sdk import HookMatcher

    for hook_name in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        options_kwargs.setdefault("hooks", {}).setdefault(hook_name, []).append(
            HookMatcher(hooks=[completion.activity.lifecycle_hook])
        )
    options_kwargs.setdefault("hooks", {}).setdefault("Stop", []).append(
        HookMatcher(hooks=[completion.stop_hook])
    )
    # Durable pause/restart: a NEW run pins a chosen UUID session id (so its transcript is
    # findable for --resume); a --resume run replays that session instead. The two are
    # mutually exclusive per the SDK (unless fork_session), so set exactly one.
    if sdk_resume_id:
        options_kwargs["resume"] = sdk_resume_id
    else:
        options_kwargs["session_id"] = sdk_session_id

    import research_context

    research_context.configure_options(options_kwargs, workdir)

    # After a durable resume the conversation is already replayed; nudge to continue rather
    # than re-asking the task. (query()'s session_id here is a per-message stream tag, distinct
    # from options.session_id/resume above.)
    query_text = completion.query(
        RESUME_NUDGE if sdk_resume_id else question,
        mode="resume" if sdk_resume_id else "initial",
        approvals=policy,
    )

    # ── Across-limit orchestration (docs/plans/2026-08-13-rate-limit-resume-cc.md) ──
    # main() owns the client lifecycle, so the wait-and-reconnect-via-resume loop lives here (see
    # _drive_across_limits). The factory rebuilds options in resume mode on each post-limit attempt.
    resume_id = sdk_resume_id or sdk_session_id

    def client_factory(resume: bool):
        kw = dict(options_kwargs)
        kw.pop("thinking", None)
        kw.pop("effort", None)
        kw.update({key: value for key, value in configured_model.items() if key != "env"})
        kw["env"] = {**cli_environment(kw.get("env")), **configured_model["env"]}
        if resume:
            kw.pop("session_id", None)
            kw["resume"] = resume_id
        return ClaudeSDKClient(options=ClaudeAgentOptions(**kw))

    def render(msg, started, directory):
        return _render_manager_message(
            msg, started, directory, include_result_output=completion.observe(msg)
        )

    status, error, document = "error", None, None
    import session_titles
    title_task = None
    try:
        research_iterations.update_iteration(
            workdir, completion.iteration_id, status="running"
        )
        import docker_bash
        async with docker_bash.executor_session(workdir) as executor:
            if not args.resume:
                title_task = session_titles.start_title_task(workdir, question)
            docker_bash.configure_options(options_kwargs, workdir, executor.env)
            with cc_research_lib.trace_run_context(propagate_attributes, session_name):
                await _drive_across_limits(
                    client_factory,
                    workdir,
                    session_name,
                    start,
                    run_control,
                    first_query=query_text,
                    start_resume=bool(sdk_resume_id),
                    no_auto_wait=args.no_auto_wait,
                    buffer_s=float(os.environ.get("RATE_LIMIT_BUFFER_SECONDS", "60")),
                    backoff_s=float(os.environ.get("RATE_LIMIT_BACKOFF_SECONDS", "600")),
                    max_waits=int(os.environ.get("RATE_LIMIT_MAX_WAITS", "24")),
                    render=render,
                    completion=completion,
                )
        if run_control.is_checkpoint_stop(workdir):
            status = "cancelled"
        else:
            document = completion.validated_result()
            status = "complete"
    except asyncio.CancelledError:
        status = "cancelled"
        raise
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        await session_titles.cancel_title_task(title_task)
        try:
            approval_control.close_iteration(workdir, completion.iteration_id)
            research_iterations.finish_iteration(
                workdir,
                completion.iteration_id,
                status=status,
                answer=completion.answer,
                result_path=completion.output_path if status == "complete" else None,
                result_bytes=document,
                plan_documents=completion.plans if status == "complete" else None,
                error=error,
            )
            research_session.save_run_status(workdir, status)
        finally:
            cc_research_lib.flush_tracing(langfuse)
    if status == "complete":
        print(f"\nSaved result: {workdir / completion.output_path}", flush=True)
    if langfuse is not None:
        print(f"\nTraces: {cfg.langfuse_base_url_v3}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
