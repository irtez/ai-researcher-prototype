#!/usr/bin/env python3
"""Shared helpers for the Claude Code (SDK) research path.

Extracted so both the CLI entry point (``scripts/run_research.py``) and the
host-side HTTP runner (``scripts/cc_runner.py``, added in a later SC) speak one
wire contract instead of duplicating logic — same precedent as
``scripts/cc_trace_lib.py``.

This module owns the SDK-message -> ``AgentEvent`` mapping
(SC2 of ``docs/plans/2026-07-03-ui-claude-code-orchestrator.md``): the pure
function that turns a ``claude_agent_sdk`` message stream into the *same*
``AgentEvent`` NDJSON the CodeArkt server (``app:5055``) emits, so
``ui/src/live.ts::reduceAgentEvent`` and ``agentToStage`` consume both backends
without any change.

Wire contract (must not drift):

- ``AgentEvent`` / ``EventType`` are imported from ``codearkt.event_bus`` — the
  single source of truth for the stream schema, shared by both orchestrators.
- Sub-agent names are normalized ``-`` -> ``_`` (``mle-solver`` -> ``mle_solver``)
  because ``agentToStage`` keys the timeline on the underscore form; without it
  the stage never lights up.
- ``agent_start`` carries ``content=None`` (CodeArkt parity — it publishes
  ``AGENT_START`` with no content); ``observation`` carries a short tool label.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from codearkt.event_bus import AgentEvent, EventType
from cc_activity import ResearchActivity

from claude_agent_sdk.types import (
    AssistantMessage,
    MirrorErrorMessage,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

# ── Constants ──

MANAGER_AGENT_NAME = "manager"
"""Root agent name the timeline/`reduceAgentEvent` expects for the manager."""

TASK_TOOL_NAMES = frozenset({"Agent", "Task"})
"""The built-in tool the manager uses to delegate to a sub-agent.

The Claude CLI names this tool ``Agent`` (verified live against claude
2.1.x / ``claude_agent_sdk`` 0.1.72 — the ``ToolUseBlock`` arrives as
``name='Agent'`` with ``subagent_type`` / ``description`` / ``prompt`` inputs);
older/other CLI builds have named it ``Task``. Match **either** so a delegation
is recognized regardless of which name the installed CLI emits — mis-detecting it
sends every sub-agent's output back to the manager and the stage timeline in
``ui/src/live.ts::agentToStage`` never lights up (SC2)."""

TASK_TOOL_NAME = "Agent"
"""Back-compat alias: the current CLI's delegation tool name. Prefer
:data:`TASK_TOOL_NAMES` for membership checks."""

# Priority order for extracting a short human hint from a tool call's input,
# so an ``observation`` event says *what* the tool did, not just its name.
_OBSERVATION_HINT_KEYS = (
    "description",
    "command",
    "query",
    "prompt",
    "file_path",
    "path",
    "pattern",
    "url",
)

# TaskAgents threads the per-run Task ``tool_use_id`` -> normalized sub-agent
# name across ``map_sdk_message_to_events`` calls: set when a ``Task`` tool_use
# is seen, popped on the first completion signal (so a second signal for the
# same task can't double-emit ``agent_end``). The caller owns and reuses one
# dict for the whole run.
TaskAgents = Dict[str, str]


class ResearchEvent(AgentEvent):
    """Additive Claude activity fields; the base/legacy event contract stays readable."""

    activity: dict[str, Any] | None = None
    received_at: str | None = None
    iteration_id: str | None = None
    sequence: int | None = None
    output_mode: str | None = None


def normalize_agent_name(name: str) -> str:
    """Normalize an SDK sub-agent name to the underscore form the UI keys on.

    ``mle-solver`` -> ``mle_solver``. Empty/whitespace collapses to a generic
    ``subagent`` so the mapping never emits an empty ``agent_name`` (which the
    reducer would track as a nameless agent).
    """
    normalized = (name or "").strip().replace("-", "_")
    return normalized or "subagent"


def _event(
    session_id: str,
    agent_name: str,
    event_type: EventType,
    content: str | None = None,
) -> AgentEvent:
    return AgentEvent(
        session_id=session_id,
        agent_name=agent_name,
        event_type=event_type,
        content=content,
    )


def manager_start_event(session_id: str) -> AgentEvent:
    """Opening ``agent_start`` (manager) the runner emits before the SDK loop.

    Not tied to any SDK message — the CodeArkt server likewise emits the root
    ``agent_start`` at run start, before any model output. Kept here so the SC2
    contract (stream begins with ``agent_start`` manager) lives in one place.
    """
    return _event(session_id, MANAGER_AGENT_NAME, EventType.AGENT_START)


def manager_end_event(session_id: str) -> AgentEvent:
    """Closing ``agent_end`` (manager) the runner emits once the whole run is over.

    Symmetric to :func:`manager_start_event`. On the multi-turn UI path the per-turn
    manager ``agent_end`` that :func:`map_sdk_message_to_events` emits on every
    ``ResultMessage`` is suppressed (it would make the UI stage view flap across
    steer/pause/rate-limit turn boundaries); the runner emits exactly one of these
    after the driver truly completes instead.
    """
    return _event(session_id, MANAGER_AGENT_NAME, EventType.AGENT_END)


def _tool_label(block: ToolUseBlock) -> str:
    """Compact, deterministic label for an ``observation`` event."""
    name = block.name
    if isinstance(block.input, dict):
        for key in _OBSERVATION_HINT_KEYS:
            val = block.input.get(key)
            if isinstance(val, str) and val.strip():
                return f"{name}: {val.strip()[:120]}"
    return name


def _subagent_type(block: ToolUseBlock) -> str:
    if isinstance(block.input, dict):
        raw = block.input.get("subagent_type", "")
        if isinstance(raw, str):
            return raw
    return ""


def _is_background_launch(message: UserMessage, block: ToolResultBlock) -> bool:
    """An Agent acknowledgement is not the subagent's terminal result."""
    if block.is_error:
        return False
    metadata = message.tool_use_result or {}
    if metadata.get("status") == "async_launched":
        return True
    # Some CLI/SDK versions forward the acknowledgement only as text.
    content = block.content
    if isinstance(content, list):
        content = "\n".join(
            part.get("text", "") for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return isinstance(content, str) and content.lstrip().startswith(
        "Async agent launched successfully."
    )


def map_sdk_message_to_events(
    message: Any,
    session_id: str,
    task_agents: TaskAgents | ResearchActivity,
    *,
    include_result_output: bool = True,
) -> List[AgentEvent]:
    """Map one ``claude_agent_sdk`` message to zero or more ``AgentEvent``\\s.

    Pure w.r.t. the outside world; the only mutation is bookkeeping in
    ``task_agents`` (the caller's per-run Task map). Threading that map across
    calls is what lets sub-agent output/close events resolve the right name and
    what guarantees a Task closes exactly once.

    Mapping (from the spec's table):

    - ``AssistantMessage`` w/o ``parent_tool_use_id`` (manager): each non-empty
      ``TextBlock`` -> ``output`` (manager); a delegation ``ToolUseBlock`` (name
      in :data:`TASK_TOOL_NAMES` — ``Agent`` on the current CLI, ``Task`` on
      older builds) -> ``agent_start`` (normalized ``subagent_type``) + records
      the task; any other ``ToolUseBlock`` -> ``observation`` (manager).
    - ``AssistantMessage`` *with* ``parent_tool_use_id`` (sub-agent): ``TextBlock``
      -> ``output`` from that sub-agent; ``ToolUseBlock`` -> ``observation`` from
      that sub-agent.
    - ``UserMessage`` carrying a terminal ``ToolResultBlock`` whose
      ``tool_use_id`` is a known Task -> ``agent_end`` of that sub-agent.
      Background launch acknowledgements keep the role open for subsequent
      messages and its actual completion notification.
    - ``TaskNotificationMessage`` (completed/failed/stopped) -> ``agent_end`` of
      the sub-agent, *iff* the task is still open (dedup against the tool_result
      path — never two ``agent_end``\\s for one Task).
    - ``TaskStartedMessage`` / ``TaskProgressMessage`` -> no event (the
      ``agent_start`` was already emitted from the ``Task`` tool_use).
    - other ``SystemMessage`` (incl. non-fatal ``MirrorErrorMessage``) -> no event.
    - ``ResultMessage`` -> final ``output`` (manager, the result text) then
      ``agent_end`` (manager), closing the stream.
    """
    if isinstance(task_agents, ResearchActivity):
        return [ResearchEvent(session_id=session_id, **record) for record in
                task_agents.consume(message, include_result_output=include_result_output)]

    # Compatibility for callers using the original role-only mapping dictionary.
    events: List[AgentEvent] = []

    if isinstance(message, AssistantMessage):
        parent = message.parent_tool_use_id
        if parent:
            agent = task_agents.get(parent, MANAGER_AGENT_NAME)
            for block in message.content or []:
                if isinstance(block, TextBlock) and block.text and block.text.strip():
                    events.append(
                        _event(session_id, agent, EventType.OUTPUT, block.text)
                    )
                elif isinstance(block, ToolUseBlock):
                    events.append(
                        _event(
                            session_id,
                            agent,
                            EventType.TOOL_RESPONSE,
                            _tool_label(block),
                        )
                    )
        else:
            for block in message.content or []:
                if isinstance(block, TextBlock) and block.text and block.text.strip():
                    events.append(
                        _event(
                            session_id, MANAGER_AGENT_NAME, EventType.OUTPUT, block.text
                        )
                    )
                elif isinstance(block, ToolUseBlock):
                    if block.name in TASK_TOOL_NAMES:
                        sub = normalize_agent_name(_subagent_type(block))
                        task_agents[block.id] = sub
                        events.append(_event(session_id, sub, EventType.AGENT_START))
                    else:
                        events.append(
                            _event(
                                session_id,
                                MANAGER_AGENT_NAME,
                                EventType.TOOL_RESPONSE,
                                _tool_label(block),
                            )
                        )
        return events

    if isinstance(message, UserMessage):
        content = message.content
        if isinstance(content, list):
            for block in content:
                if (
                    isinstance(block, ToolResultBlock)
                    and block.tool_use_id in task_agents
                    and not _is_background_launch(message, block)
                ):
                    sub = task_agents.pop(block.tool_use_id)
                    events.append(_event(session_id, sub, EventType.AGENT_END))
        return events

    # TaskStarted/TaskProgress subclass SystemMessage: the sub-agent's
    # agent_start already fired from the Task tool_use, so emit nothing here.
    if isinstance(message, (TaskStartedMessage, TaskProgressMessage)):
        return events

    if isinstance(message, TaskNotificationMessage):
        tool_use_id = message.tool_use_id
        if tool_use_id and tool_use_id in task_agents:
            sub = task_agents.pop(tool_use_id)
            events.append(_event(session_id, sub, EventType.AGENT_END))
        return events

    # MirrorErrorMessage (non-fatal) and any other SystemMessage: no event.
    if isinstance(message, (MirrorErrorMessage, SystemMessage)):
        return events

    if isinstance(message, ResultMessage):
        result = message.result or ""
        if include_result_output and result.strip():
            events.append(
                _event(session_id, MANAGER_AGENT_NAME, EventType.OUTPUT, result)
            )
        events.append(_event(session_id, MANAGER_AGENT_NAME, EventType.AGENT_END))
        return events

    return events


# ── compute_mode agent filter (SC5) ──
#
# The UI's executor selector (``compute_mode`` = ``Auto`` / ``Local`` / ``GPU``)
# must mean the same thing on the CC path as on CodeArkt: a hard ceiling on
# which mle-solver variant the run may use, not a hint. On the CC path the
# ``agents`` dict handed to ``ClaudeAgentOptions`` is rebuilt per run, so we
# filter it per run (race-free — no shared state is mutated).

MLE_SOLVER_LOCAL_AGENT = "mle-solver"
"""``.claude/agents/`` slug of the local (CPU) mle-solver — the variant a
``compute_mode=Local`` run keeps and a ``GPU`` run drops."""

MLE_SOLVER_REMOTE_AGENT = "mle-solver-remote"
"""``.claude/agents/`` slug of the remote (GPU) mle-solver — the variant a
``compute_mode=GPU`` run keeps and a ``Local`` run drops."""

COMPUTE_MODE_LOCAL = "Local"
COMPUTE_MODE_GPU = "GPU"
COMPUTE_MODE_AUTO = "Auto"


class ComputeModeUnavailableError(RuntimeError):
    """A ``compute_mode`` needs an mle-solver variant not rendered on disk.

    ``ensure_synced()``'s ``MLE_SOLVER_IS_REMOTE`` env filter can render only one
    solver variant into ``.claude/agents/`` (e.g. a light session renders only
    the local solver). If the mode the user picked *requires* the variant that
    was filtered out, the runner must fail loud: raise this so the run surfaces
    an explicit stream error (SC5), instead of silently downgrading to whichever
    solver happens to be present.
    """


def filter_agents_by_compute_mode(
    agents: Dict[str, Any], compute_mode: Optional[str]
) -> Dict[str, Any]:
    """Filter the per-run ``agents`` dict by ``compute_mode`` (SC5).

    - ``Local`` -> drop ``mle-solver-remote`` (keep the local solver);
    - ``GPU``   -> drop ``mle-solver`` (keep the remote solver);
    - ``Auto`` / unset / anything else -> keep both variants (no filtering).

    ``agents`` is keyed by the ``.claude/agents/`` slug (dash form,
    ``mle-solver`` / ``mle-solver-remote``). Returns a *new* dict — the input is
    never mutated, so re-filtering the same loaded agents per run is safe.

    Raises :class:`ComputeModeUnavailableError` when the variant the mode
    requires is absent (``ensure_synced()`` didn't render it). ``Auto`` requires
    neither, so it never raises — it respects whatever ``ensure_synced`` chose to
    render (the env filter is an intentional way to run with a single solver).
    """
    mode = (compute_mode or "").strip()

    if mode == COMPUTE_MODE_LOCAL:
        required, drop = MLE_SOLVER_LOCAL_AGENT, MLE_SOLVER_REMOTE_AGENT
    elif mode == COMPUTE_MODE_GPU:
        required, drop = MLE_SOLVER_REMOTE_AGENT, MLE_SOLVER_LOCAL_AGENT
    else:
        # Auto / unset: keep both variants (whatever ensure_synced rendered).
        return dict(agents)

    if required not in agents:
        raise ComputeModeUnavailableError(
            f"compute_mode={mode!r} requires the {required!r} agent, but it was "
            f"not rendered into .claude/agents/ (MLE_SOLVER_IS_REMOTE env filter). "
            f"Refusing to silently run with a different solver."
        )

    return {name: definition for name, definition in agents.items() if name != drop}


# ── Session scaffold (SC3) ──
#
# The Claude-Code runner writes the *same* session artifacts the CodeArkt path
# does, so ui-api's read-only overview and its owner isolation treat both
# orchestrators identically (SC3 of
# ``docs/plans/2026-07-03-ui-claude-code-orchestrator.md``).
#
# Written INLINE here (not imported from ``codearkt.session_id``) for the same
# reason ``scripts/run_research.py`` inlines its figures symlink and ui-api
# inlines its owner-marker reader: this host-side code runs against the codearkt
# PyPI wheel, which does not ship ``codearkt.session_id``. The contracts are
# mirrored 1:1 — write-once markers, a RELATIVE ``figures/paperbanana`` symlink —
# so a CC session is byte-for-byte indistinguishable from a CodeArkt session to
# ui-api. The one addition is the ``.holosophus_ui/orchestrator`` marker, which
# only the CC path writes (its absence == ``codearkt``).

OWNER_MARKER_RELPATH = Path(".holosophus_ui") / "owner"
"""Wire contract with ``codearkt.session_id.write_owner_marker`` / ui-api's
``OWNER_MARKER_RELATIVE_PATH`` — the browser-client that owns the session."""

ORCHESTRATOR_MARKER_RELPATH = Path(".holosophus_ui") / "orchestrator"
"""New CC-only marker; ui-api reads it (default ``codearkt`` when absent)."""

CLAUDE_CODE_ORCHESTRATOR = "claude_code"
"""Value written to the orchestrator marker on the Claude-Code path."""

STARTED_PROMPT_FILENAME = "started_prompt.md"
"""Same file ``langfuse_openai_patch._persist_started_prompt`` writes on the
CodeArkt path (ui-api's ``read_started_prompt`` consumes it)."""

RESULT_FILENAME = "result.md"
"""Final run artifact — the manager's result text, mirrored into the workdir."""


def write_owner_marker(session_dir: Path, client_id: Optional[str]) -> None:
    """Tag a session directory with the browser-client that created it.

    Mirrors ``codearkt.session_id.write_owner_marker``: writes
    ``<session>/.holosophus_ui/owner`` only when ``client_id`` is non-empty,
    write-once (an existing marker is never overwritten so a second client can't
    silently take over), best-effort (never raises — safe on the run-start path).
    """
    if not client_id or not client_id.strip():
        return
    try:
        marker = Path(session_dir) / OWNER_MARKER_RELPATH
        if marker.exists():
            return
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(client_id.strip(), encoding="utf-8")
    except OSError:
        return


def write_orchestrator_marker(
    session_dir: Path, orchestrator: str = CLAUDE_CODE_ORCHESTRATOR
) -> None:
    """Write the write-once ``.holosophus_ui/orchestrator`` marker.

    Same write-once / best-effort semantics as the owner marker. Only the CC
    path writes this file; ui-api treats its absence as ``codearkt``.
    """
    if not orchestrator or not orchestrator.strip():
        return
    try:
        marker = Path(session_dir) / ORCHESTRATOR_MARKER_RELPATH
        if marker.exists():
            return
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(orchestrator.strip(), encoding="utf-8")
    except OSError:
        return


def ensure_figures_symlink(session_dir: Path) -> None:
    """Bridge paperbanana's shared OUTPUT_DIR into the session cwd.

    Mirrors ``codearkt.session_id.ensure_session_scaffold`` and
    ``run_research.py``: a RELATIVE ``<session>/figures/paperbanana`` symlink to
    the workspace-level ``figures/paperbanana`` so the same tree resolves both on
    the host and inside every container that mounts ``./workdir`` at ``/workdir``.
    Idempotent and best-effort (never raises).
    """
    try:
        session_dir = Path(session_dir)
        workspace_root = session_dir.parent
        pb_global = workspace_root / "figures" / "paperbanana"
        pb_global.mkdir(parents=True, exist_ok=True)
        session_figs = session_dir / "figures"
        session_figs.mkdir(parents=True, exist_ok=True)
        session_pb = session_figs / "paperbanana"
        if session_pb.exists() or session_pb.is_symlink():
            return
        rel_target = os.path.relpath(pb_global, start=session_pb.parent)
        session_pb.symlink_to(rel_target, target_is_directory=True)
    except OSError:
        return


def write_started_prompt(session_dir: Path, prompt_text: Optional[str]) -> None:
    """Persist the task text to ``started_prompt.md`` (write-once).

    Mirrors ``langfuse_openai_patch._persist_started_prompt`` (the CodeArkt
    path): the file is what ui-api's ``read_started_prompt`` / brief tab render,
    so the CC path must produce it too. Skips empty prompts and never overwrites.
    """
    if not prompt_text or not prompt_text.strip():
        return
    try:
        path = Path(session_dir) / STARTED_PROMPT_FILENAME
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{prompt_text.strip()}\n", encoding="utf-8")
    except OSError:
        return


def scaffold_cc_session(
    session_dir: Path,
    *,
    client_id: Optional[str],
    prompt_text: Optional[str],
    orchestrator: str = CLAUDE_CODE_ORCHESTRATOR,
) -> Path:
    """Create a CC session directory with full artifact/owner parity (SC3).

    Runs *before* the SDK loop: makes the session dir, writes the owner and
    orchestrator markers, bridges the figures symlink, and persists
    ``started_prompt.md``. Returns the session dir. The manager writes its
    document at the iteration's server-assigned path; rendering never creates it.
    """
    session_dir = Path(session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)
    write_owner_marker(session_dir, client_id)
    write_orchestrator_marker(session_dir, orchestrator)
    ensure_figures_symlink(session_dir)
    write_started_prompt(session_dir, prompt_text)
    return session_dir


# ── Shared Claude Code tracing: Langfuse v3 and optional Phoenix ──
# Both CLI and UI use langsmith_fork and the same OpenTelemetry provider.
# Per-run context associates traces with the session and browser client.

LANGFUSE_PUBLIC_KEY_V3_ENV = "LANGFUSE_PUBLIC_KEY_V3"
LANGFUSE_SECRET_KEY_V3_ENV = "LANGFUSE_SECRET_KEY_V3"
LANGFUSE_BASE_URL_V3_ENV = "LANGFUSE_BASE_URL_V3"
DEFAULT_LANGFUSE_BASE_URL_V3 = "http://localhost:3001"
_TRACING_BRIDGE_ENABLED = False


def noop_propagate_attributes(**_kwargs: Any) -> Any:
    """No-op stand-in for ``langfuse.propagate_attributes``.

    Returned when neither destination is configured or bridge setup fails,
    so the run proceeds without the call
    site special-casing a missing tracer — ``with trace_run_context(...)`` just
    enters a ``nullcontext``.
    """
    return nullcontext()


def init_langfuse_tracing() -> Tuple[Optional[Any], Callable[..., Any]]:
    """Initialize the shared CC bridge and the configured telemetry destinations.

    The historical return contract remains (Langfuse client, propagation context).
    Phoenix is optional and can keep the bridge active without a Langfuse client.
    Call before importing ClaudeSDKClient: the bridge replaces the SDK class.
    """
    global _TRACING_BRIDGE_ENABLED
    logger = logging.getLogger(__name__)
    public_key = os.environ.get(LANGFUSE_PUBLIC_KEY_V3_ENV, "").strip()
    secret_key = os.environ.get(LANGFUSE_SECRET_KEY_V3_ENV, "").strip()
    base_url = (
        os.environ.get(LANGFUSE_BASE_URL_V3_ENV, "").strip()
        or DEFAULT_LANGFUSE_BASE_URL_V3
    )
    langfuse = None
    if public_key and secret_key:
        try:
            os.environ["LANGFUSE_PUBLIC_KEY"] = public_key
            os.environ["LANGFUSE_SECRET_KEY"] = secret_key
            os.environ["LANGFUSE_BASE_URL"] = base_url
            from langfuse import get_client

            candidate = get_client()
            if candidate.auth_check():
                langfuse = candidate
            else:
                logger.warning(
                    "Langfuse authentication failed; checking Phoenix separately"
                )
        except Exception as error:
            logger.warning("Langfuse setup failed (%s)", type(error).__name__)

    phoenix_enabled = False
    try:
        from phoenix_tracing import configure_phoenix_export

        phoenix_enabled = configure_phoenix_export()
    except Exception as error:
        logger.warning("Phoenix setup failed (%s)", type(error).__name__)

    if langfuse is None and not phoenix_enabled:
        return None, noop_propagate_attributes

    try:
        os.environ["LANGSMITH_OTEL_ENABLED"] = "true"
        os.environ["LANGSMITH_OTEL_ONLY"] = "true"
        os.environ["LANGSMITH_TRACING"] = "true"
        from langfuse import propagate_attributes
        import langsmith_fork

        sys.modules["langsmith.integrations.claude_agent_sdk"] = langsmith_fork
        langsmith_fork.configure_claude_agent_sdk()
        _TRACING_BRIDGE_ENABLED = True
        return langfuse, propagate_attributes
    except Exception as error:
        logger.warning(
            "Claude Code tracing bridge setup failed (%s)", type(error).__name__
        )
        return None, noop_propagate_attributes


def flush_tracing(langfuse_client: Optional[Any] = None) -> bool:
    """Drain the bridge queue before both sinks; delivery failures stay nonfatal.

    LangSmith creates OTel spans asynchronously. Flushing only the destination
    processors can miss the last records still waiting in that upstream queue.
    Call outside the run context, and off the event loop for the HTTP runner.
    """
    if not _TRACING_BRIDGE_ENABLED and langfuse_client is None:
        return True
    logger = logging.getLogger(__name__)
    ok = True
    if _TRACING_BRIDGE_ENABLED:
        try:
            from langsmith.run_trees import get_cached_client

            get_cached_client().flush(timeout=10)
        except Exception as error:
            logger.warning("Tracing queue flush failed (%s)", type(error).__name__)
            ok = False
    if langfuse_client is not None:
        try:
            langfuse_client.flush()
        except Exception as error:
            logger.warning("Langfuse flush failed (%s)", type(error).__name__)
            ok = False
    try:
        from phoenix_tracing import flush_phoenix_export

        ok = flush_phoenix_export() and ok
    except Exception as error:
        logger.warning("Phoenix flush failed (%s)", type(error).__name__)
        ok = False
    return ok


@contextmanager
def trace_run_context(
    propagate_attributes: Callable[..., Any],
    session_id: str,
    user_id: Optional[str] = None,
) -> Any:
    """Enter the per-run trace context shared by Langfuse and Phoenix.

    Always sets ``session_id`` (== the run's session_id == the workdir name ==
    the Langfuse session, per ``docs/data-topology.md``). Sets ``user_id`` **only
    when the browser client is non-empty**, so:

    - with a non-empty ``X-Holo-Client`` (incl. an auth-overlay's
      ``$remote_user`` rewrite) the trace's ``user`` field == that value;
    - without the header the ``user`` stays empty.

    This reproduces the login->user parity the CodeArkt path landed in
    ``ad3f449`` (``set_owner`` -> ``langfuse_openai_patch``) on the CC path via
    the v3 bridge's native ``propagate_attributes``.
    """
    from phoenix_tracing import phoenix_trace_context

    user = user_id.strip() if user_id and user_id.strip() else None
    attributes = {"session_id": session_id}
    if user:
        attributes["user_id"] = user
    with phoenix_trace_context(session_id=session_id, user_id=user):
        with propagate_attributes(**attributes):
            yield
