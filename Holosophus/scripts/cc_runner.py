#!/usr/bin/env python3
"""Host-side HTTP runner for the Claude Code (SDK) orchestrator path.

Speaks the *same* wire contract the CodeArkt server (``app:5055``) exposes to the
UI, so the browser's stream reducer, queue banner, and Stop button work against
both backends unchanged (``docs/plans/2026-07-03-ui-claude-code-orchestrator.md``):

- ``POST /agents/manager`` — NDJSON stream of ``AgentEvent`` (SC2),
- ``POST /agents/cancel`` — ``{session_id}`` -> interrupt the live run (SC4),
- ``GET  /health``        — liveness probe.

nginx routes to this runner on the ``X-Holo-Orchestrator: claude_code`` header
(``docker/nginx-ui.conf``); a ``claude_code`` request that reaches ``app:5055``
instead is rejected there with 400, and vice-versa here.

This increment lands **SC4 — cancellation**. Its heart is a per-session
registry that ``POST /agents/cancel`` uses to stop a live run:

1. queue an ``agent_interrupted`` event so the UI flips to *cancelled* at once
   (``ui/src/live.ts::reduceAgentEvent`` treats it as terminal), then
2. ask the SDK to stop gracefully (``ClaudeSDKClient.interrupt()``), then
3. within a bounded budget (``CC_CANCEL_TIMEOUT_SEC`` < 15 s, SC4) force-cancel
   the run task — cancelling it unwinds the ``async with ClaudeSDKClient(...)``
   context, whose ``disconnect()`` terminates the spawned claude CLI subprocess,
   so the process dies within the SC bound even if graceful interrupt is ignored.

Cancelling a finished or unknown session is a **success, never an exception**
(``cancel_session`` returns ``active=False``), so a late Stop click cannot crash
the runner.

This increment also lands **SC6 — the runner queue**. The runner has its *own*
admission pool (``CC_MAX_ACTIVE_SESSIONS``, default 1), disjoint from the
CodeArkt server's ``MAX_ACTIVE_SESSIONS`` (a CC run never touches ``app:5055``'s
``GET /capacity``). When every slot is busy the next run publishes
``session_queued`` (the UI's existing queue banner) and blocks until a slot
frees, then ``session_running`` — the *same* ``{"resource":"session",...}``
payload ``codearkt.event_bus.publish_status`` emits, so the browser reducer
works against both backends unchanged. The gate reuses CodeArkt's own
``SessionDispatcher`` (``codearkt.dispatch``) so the position/depth semantics are
byte-for-byte identical, not a re-implementation that could drift.

Concerns owned by later SCs layer onto the same manager endpoint without
touching the cancel path above: Langfuse v3 ``propagate_attributes`` (SC7). They
are called out with ``# SCn seam`` markers below.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Literal, Optional

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

PROJECT_DIR = Path(__file__).resolve().parent.parent

# Wire-contract parity: the pinned ``codearkt`` PyPI wheel lags the local source
# -- its ``EventType`` predates ``agent_interrupted`` / ``session_*`` that the
# running app emits via its editable local ``codearkt`` (the Dockerfile swaps
# the wheel for the local package). Prepend the local package so this host-side
# runner speaks the SAME event vocabulary the CodeArkt server does; without it
# the ``agent_interrupted`` terminal this SC4 increment injects would be
# unresolvable. Ordered before ``scripts`` so ``cc_research_lib`` (imported
# next) binds the same local ``codearkt`` too.
sys.path.insert(0, str(PROJECT_DIR / "codearkt"))
sys.path.insert(0, str(PROJECT_DIR / "scripts"))
sys.path.insert(0, str(PROJECT_DIR / "holosophos"))

from codearkt import auth  # noqa: E402
from codearkt.admission import (  # noqa: E402
    AdmissionLedger,
    global_limit,
    per_owner_limit,
)
from codearkt.dispatch import SessionDispatcher  # noqa: E402
from codearkt.event_bus import AgentEvent, EventType  # noqa: E402

import cc_research_lib  # noqa: E402
import research_profiles  # noqa: E402
import approval_control  # noqa: E402
import docker_bash  # noqa: E402
from holosophos import research_iterations, research_session  # noqa: E402

logger = logging.getLogger("cc_runner")
logger.setLevel(logging.INFO)


# ── Env config (all have code defaults — none is a REQUIRED_NON_EMPTY key) ──

CC_RUNNER_BIND = os.environ.get("CC_RUNNER_BIND", "0.0.0.0")
"""Bind interface. ``0.0.0.0`` (not ``127.0.0.1``) because on Linux
host-gateway the ``ui`` container reaches ``host.docker.internal`` on the docker
bridge IP, a non-loopback host interface (design p.1)."""

CC_RUNNER_PORT = int(os.environ.get("CC_RUNNER_PORT", "5061"))
"""Listen port of *this* runner. The nginx upstream port is a literal ``5061``
in the conf (env is not substituted there); changing it means editing env + both
conf files."""

CC_CANCEL_TIMEOUT_SEC = float(os.environ.get("CC_CANCEL_TIMEOUT_SEC", "10"))
"""Grace budget for a cancel: how long the run task gets to wind down on its own
after ``interrupt()`` before it is force-cancelled. Kept under the SC4 15 s bound
so the whole cancel (including subprocess teardown) completes in time."""

CC_MAX_ACTIVE_SESSIONS = int(os.environ.get("CC_MAX_ACTIVE_SESSIONS", "1"))
"""SC6: how many CC runs may execute at once in *this* runner process. The
(N+1)th run queues behind the pool and gets ``session_queued`` (the UI banner),
then ``session_running`` once a slot frees. Default 1 -- concurrent Langfuse
trace attribution in one process is unverified (spec Open Questions), so
correctness over parallelism until a live two-run check clears it. Disjoint from
the CodeArkt server's ``MAX_ACTIVE_SESSIONS``: a CC run never occupies an
``app:5055`` slot, so ``GET /capacity`` there is unaffected by CC runs."""

CLAUDE_CODE_ORCHESTRATOR = "claude_code"
"""The only orchestrator this runner serves; anything else is a routing desync."""

CC_CTL_POLL_INTERVAL = float(os.environ.get("CC_CTL_POLL_INTERVAL", "1.0"))
"""How often the shared driver polls ``.holosophus_ctl`` for pause/resume/stop/steer on a
UI-started run. Matches the CLI default (1 s)."""

CC_NO_AUTO_WAIT = os.environ.get("CC_NO_AUTO_WAIT", "").lower() in ("1", "true", "yes")
"""When set, a rate-limit hit ends the run cleanly (durably resumable) instead of auto-waiting
for the reset in-process — the runner analogue of the CLI ``--no-auto-wait`` flag."""

CC_DEFAULT_MODEL = research_session.CC_DEFAULT_MODEL
"""Claude model the CC path runs on when the request carries no ``model_name``
override -- the same model ``scripts/run_research.py`` uses for the CLI path. A
non-empty ``model_name`` on the request is honoured (passed straight to the SDK),
so the per-run model selector has a real effect instead of being silently
dropped; the frontend keys the CC session_id on this same value so the id never
mislabels the run with the CodeArkt OpenRouter slug."""

# Same response headers the CodeArkt server sets on its NDJSON stream
# (``AGENT_RESPONSE_HEADERS`` in ``codearkt/codearkt/server.py``): disable proxy
# buffering so events reach the browser as they are produced.
AGENT_RESPONSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

# Sentinel put on a session's event queue to mark end-of-stream. The stream
# reader stops on it; it is never serialized to the wire.
_STREAM_END = object()


# ── Session registry (the SC4 substrate) ──


class IterationEventQueue(asyncio.Queue):
    """Persist at the producer, before any browser consumes an event."""

    def __init__(self, workdir: Path, iteration_id: str):
        super().__init__()
        self.workdir = workdir
        self.iteration_id = iteration_id
        self.history_error: Exception | None = None
        self.stream_closed = False

    def put_nowait(self, item: Any) -> None:
        if isinstance(item, AgentEvent) and self.history_error is None:
            try:
                record = research_iterations.append_event(
                    self.workdir, self.iteration_id, item.model_dump(mode="json")
                )
                item = cc_research_lib.ResearchEvent(**record)
            except (OSError, ValueError) as exc:
                self.history_error = exc
                logger.exception(
                    "Cannot persist events for iteration %s", self.iteration_id
                )
        # Even on a storage failure, deliver errors and the closing sentinel.
        super().put_nowait(item)
        if item is _STREAM_END:
            self.stream_closed = True


@dataclass
class RunnerSession:
    """One in-flight CC run, keyed by ``session_id`` in :class:`SessionRegistry`.

    ``events`` is the per-session outgoing queue that decouples the run task
    (producer of mapped ``AgentEvent``\\s) from the streaming response (consumer)
    — the same decoupling ``codearkt.event_bus`` provides, and what lets a
    *different* request (``POST /agents/cancel``) inject ``agent_interrupted``.
    ``client`` / ``task`` are populated by the manager endpoint once the run
    starts, so the cancel handler can interrupt the SDK and force-cancel the run.
    """

    session_id: str
    events: "asyncio.Queue[Any]"
    client: Any = None
    task: Optional["asyncio.Task[Any]"] = None
    interrupted: bool = False
    finished: bool = False
    # Set the instant the run body emits its own terminal (manager ``agent_end``),
    # BEFORE the slow, networked Langfuse flush in the run's ``finally``. A cancel
    # landing in that teardown tail then short-circuits to the idempotent no-op
    # branch instead of injecting a spurious ``agent_interrupted`` after a genuine
    # ``agent_end`` and tagging a successful run *cancelled* (grounding fix).
    completed: bool = False
    iteration_id: str | None = None


def _finish_iteration_history(
    session: RunnerSession,
    workdir: Path,
    status: str,
    *,
    answer: str | None = None,
    error: str | None = None,
    result_path: str | None = None,
    result_bytes: bytes | None = None,
    plan_documents: list[dict] | None = None,
) -> str:
    if session.iteration_id is None:
        return status
    history_error = getattr(session.events, "history_error", None)
    if history_error is not None:
        status, error = "error", "Could not save the complete iteration history"
    try:
        approval_control.close_iteration(workdir, session.iteration_id)
        research_iterations.finish_iteration(
            workdir,
            session.iteration_id,
            status=status,
            answer=answer,
            result_path=result_path if status == "complete" else None,
            result_bytes=result_bytes if status == "complete" else None,
            plan_documents=plan_documents if status == "complete" else None,
            error=error,
        )
    except (OSError, ValueError):
        logger.exception("Cannot finish iteration history %s", session.iteration_id)
        status, error = "error", "Could not save iteration history"
        history_error = True
    if history_error:
        session.events.put_nowait(
            AgentEvent(
                session_id=session.session_id,
                agent_name="manager",
                event_type=EventType.OUTPUT,
                content=f"Runner error: {error}",
            )
        )
    return status


class SessionRegistry:
    """Process-local map of ``session_id -> RunnerSession`` for live CC runs.

    The manager registers an iteration before starting its task; task completion
    removes that exact object. A disconnected reader does not release an active
    iteration. All access is from the single asyncio event loop, so no lock.
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, RunnerSession] = {}

    def register(self, session: RunnerSession) -> None:
        if self.get(session.session_id) is session:
            return
        if self.is_active(session.session_id):
            raise ValueError("This session already has an active iteration")
        self._sessions[session.session_id] = session

    def is_active(self, session_id: str) -> bool:
        session = self.get(session_id)
        if session is None:
            return False
        if session.task is not None:
            return not session.task.done()
        return not (session.finished or session.completed)

    def get(self, session_id: str) -> Optional[RunnerSession]:
        return self._sessions.get(session_id)

    def remove(self, session_id: str, expected: Optional[RunnerSession] = None) -> None:
        # Idempotent: removing an already-removed session is a no-op, so a
        # double completion/cancel can't raise KeyError.
        if expected is None or self.get(session_id) is expected:
            self._sessions.pop(session_id, None)

    def active_ids(self) -> List[str]:
        return list(self._sessions.keys())

    def __contains__(self, session_id: str) -> bool:
        return session_id in self._sessions

    def __len__(self) -> int:
        return len(self._sessions)


REGISTRY = SessionRegistry()


# ── Run-slot dispatcher (the SC6 substrate) ──
#
# The runner's own admission pool, reusing CodeArkt's `SessionDispatcher` so the
# queue position/depth semantics and the JSON payload are identical to the
# CodeArkt path (no re-implementation to drift).
#
# Auth off (the default / dev): process-local pool sized by CC_MAX_ACTIVE_SESSIONS,
# disjoint from `app:5055` -- unchanged pre-auth behaviour (SC6 invariant).
DISPATCHER = SessionDispatcher(CC_MAX_ACTIVE_SESSIONS)

# Team-login (docs/plans/2026-07-05-ui-login-and-per-account-queue.md): when
# app-level auth is on, the run pool becomes the SAME cross-process ledger the
# CodeArkt server uses, so the "6 total / 3 per account" caps are counted across
# BOTH engines (the user asked for one unified count, no CodeArkt-vs-CC split).
# Resolved lazily at request time, NOT at import: on the host this runner's .env
# is loaded by `main()` (via _ensure_langfuse_ready) only after import, so
# `auth.auth_enabled()` and the env-driven limits are correct only by then.
_LEDGER_DISPATCHER: Optional[SessionDispatcher] = None


def get_dispatcher() -> SessionDispatcher:
    """The admission pool for a run: the shared cross-process ledger when
    app-level auth is on, else the plain process-local `DISPATCHER`."""
    global _LEDGER_DISPATCHER
    if not auth.auth_enabled():
        return DISPATCHER
    if _LEDGER_DISPATCHER is None:
        _LEDGER_DISPATCHER = SessionDispatcher(
            global_limit(),
            ledger=AdmissionLedger(),
            per_owner_limit=per_owner_limit(),
            engine="claude_code",
        )
    return _LEDGER_DISPATCHER


# ── Langfuse v3 tracing (the SC7 substrate) ──
#
# The Claude-Code path traces to Langfuse v3 (@ :3001) through the local
# ``langsmith_fork`` bridge, exactly like ``scripts/run_research.py``. The bridge
# patches ``ClaudeSDKClient`` process-wide, so it MUST be configured before
# ``_run_manager`` imports the client (a local ``from claude_agent_sdk import
# ClaudeSDKClient`` binds whatever the attribute is at that moment; a later patch
# of the module attribute won't rebind that local). We configure once and cache
# the ``propagate_attributes`` callable each run is wrapped in.
_PROPAGATE_ATTRIBUTES: Any = cc_research_lib.noop_propagate_attributes
_LANGFUSE_CLIENT: Any = None
_LANGFUSE_INITIALIZED = False


def _ensure_langfuse_ready() -> None:
    """Configure the v3 tracer once, before the first SDK client import (SC7).

    Idempotent and no-op after the first call. Called from ``main()`` at startup
    (the normal path) and, defensively, at the top of ``_run_manager`` so any
    entry path self-initializes before ``ClaudeSDKClient`` is imported. Importing
    ``run_research`` first loads ``.env`` (so the v3 keys are visible) and drops
    placeholder Anthropic/OpenAI keys (keychain fallback), the same prerequisites
    the SDK run relies on. Without v3 keys Phoenix can keep tracing enabled;
    when neither destination is configured the run proceeds untraced."""
    global _PROPAGATE_ATTRIBUTES, _LANGFUSE_CLIENT, _LANGFUSE_INITIALIZED
    if _LANGFUSE_INITIALIZED:
        return
    _LANGFUSE_INITIALIZED = True
    with contextlib.suppress(Exception):
        import run_research  # noqa: F401  (side effect: load .env + drop placeholder keys)
    _LANGFUSE_CLIENT, _PROPAGATE_ATTRIBUTES = cc_research_lib.init_langfuse_tracing()


async def _flush_langfuse() -> None:
    """Flush both optional sinks off the event loop, including Phoenix-only runs."""
    await asyncio.get_running_loop().run_in_executor(
        None, cc_research_lib.flush_tracing, _LANGFUSE_CLIENT
    )


def _status_event(session_id: str, event_type: EventType, **payload: Any) -> AgentEvent:
    """Build a dispatcher status event mirroring ``event_bus.publish_status``.

    The queue payload (``resource``/``position``/``depth``) is JSON-encoded into
    the existing ``content`` field -- no new ``AgentEvent`` fields -- so the
    browser reducer (``ui/src/live.ts``) parses a CC run's queue banner exactly
    as a CodeArkt run's. ``agent_name`` is the manager (root), matching what the
    CodeArkt server passes to ``publish_status``.
    """
    return AgentEvent(
        session_id=session_id,
        agent_name=cc_research_lib.MANAGER_AGENT_NAME,
        event_type=event_type,
        content=json.dumps(payload),
    )


def session_queued_event(session_id: str, position: int, depth: int) -> AgentEvent:
    """``session_queued`` -- the run had to wait; carries its 1-based queue slot."""
    return _status_event(
        session_id,
        EventType.SESSION_QUEUED,
        resource="session",
        position=position,
        depth=depth,
    )


def session_running_event(session_id: str) -> AgentEvent:
    """``session_running`` -- a run-slot is now held (fires whether or not it waited)."""
    return _status_event(session_id, EventType.SESSION_RUNNING, resource="session")


def session_position_event(session_id: str, position: int, depth: int) -> AgentEvent:
    """``session_position`` -- refreshed queue slot for a still-waiting run after a release."""
    return _status_event(
        session_id,
        EventType.SESSION_POSITION,
        resource="session",
        position=position,
        depth=depth,
    )


RATE_LIMIT_WAIT_KIND = "rate_limit_wait"
"""Discriminator on a manager ``observation`` whose ``content`` is a JSON object
``{"kind": "rate_limit_wait", "resets_in_s": N}``. Emitted ~every 60 s while a UI run is
auto-waiting out a subscription usage limit: it keeps the NDJSON stream flowing (so nginx's
3600 s idle cap can't sever it) AND lets the frontend derive a ``waiting_rate_limit`` status.
Encoded into the existing ``content`` field — no new wire ``EventType`` — so both engines'
reducers stay compatible."""


def _is_manager_agent_end(event: AgentEvent) -> bool:
    """True for the per-turn manager ``agent_end`` the mapper emits on each ``ResultMessage``.

    The event-queue render suppresses these (the multi-turn driver produces one per turn, which
    would flap the UI stage view); the runner emits exactly one :func:`manager_end_event` after the
    whole run ends. Lifted to a named predicate so the B6 suppression rule is unit-testable.
    """
    return (
        event.event_type == EventType.AGENT_END
        and event.agent_name == cc_research_lib.MANAGER_AGENT_NAME
    )


def rate_limit_wait_event(session_id: str, remaining_s: float) -> AgentEvent:
    """Heartbeat emitted during a rate-limit hold (see :data:`RATE_LIMIT_WAIT_KIND`)."""
    return AgentEvent(
        session_id=session_id,
        agent_name=cc_research_lib.MANAGER_AGENT_NAME,
        event_type=EventType.TOOL_RESPONSE,
        content=json.dumps(
            {"kind": RATE_LIMIT_WAIT_KIND, "resets_in_s": round(remaining_s)}
        ),
    )


def interrupted_event(session_id: str) -> AgentEvent:
    """The terminal ``agent_interrupted`` event a cancel injects into the stream.

    Carries the manager (root) agent name, matching what
    ``codearkt.event_bus`` emits on interruption, so ``reduceAgentEvent`` flips
    the run to *cancelled* the same way for both backends.
    """
    return AgentEvent(
        session_id=session_id,
        agent_name=cc_research_lib.MANAGER_AGENT_NAME,
        event_type=EventType.AGENT_INTERRUPTED,
        content=None,
    )


async def _terminate_run(session: RunnerSession, interrupt_timeout: float) -> None:
    """Tear down a live run's SDK client + task within ``interrupt_timeout``.

    Graceful first (``client.interrupt()`` — asks the current turn to stop),
    then a bounded fallback: give the run task the budget to end on its own; if
    it is still running, ``task.cancel()`` unwinds the ``ClaudeSDKClient``
    context, whose ``disconnect()`` terminates the claude CLI subprocess.
    Best-effort throughout — a teardown error must not turn cancel into a 500.
    """
    client = session.client
    if client is not None:
        with contextlib.suppress(Exception):
            await client.interrupt()

    task = session.task
    if task is None or task.done():
        return

    # Wait out the grace budget without cancelling the task ourselves yet
    # (shield keeps wait_for's timeout from cancelling it): a run that honours
    # interrupt() finishes here and skips the force-cancel below.
    with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError, Exception):
        await asyncio.wait_for(asyncio.shield(task), timeout=interrupt_timeout)

    if not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


async def cancel_session(
    registry: SessionRegistry,
    session_id: str,
    *,
    interrupt_timeout: float = CC_CANCEL_TIMEOUT_SEC,
) -> Dict[str, Any]:
    """Cancel a live CC run (SC4). Idempotent and non-raising.

    - Unknown / already-finished / already-completed session -> ``{"active":
      False}`` success (a late Stop click can't crash the runner, and — the
      grounding fix — a cancel arriving during a *finished* run's teardown tail
      does not retroactively tag it cancelled).
    - Live session -> mark interrupted, queue the terminal ``agent_interrupted``
      + stream-end sentinel (so the UI flips to *cancelled* immediately), then
      tear down the SDK client + run task within ``interrupt_timeout`` so the CLI
      subprocess dies within the SC4 15 s bound.
    """
    session = registry.get(session_id)
    if session is None or session.finished or session.completed:
        return {"status": "cancelled", "session_id": session_id, "active": False}

    session.interrupted = True
    # Latch terminal *now* so a concurrent/second cancel short-circuits to the
    # idempotent success branch above instead of re-interrupting a run that is
    # already being torn down.
    session.finished = True

    # Surface the terminal event first, so the stream closes as *cancelled*
    # regardless of how the (possibly slow) subprocess teardown below unfolds.
    await session.events.put(interrupted_event(session_id))
    await session.events.put(_STREAM_END)

    await _terminate_run(session, interrupt_timeout)

    return {"status": "cancelled", "session_id": session_id, "active": True}


# ── Request models (subset of the CodeArkt AgentRequest contract) ──


class ManagerRequest(BaseModel):  # type: ignore
    messages: List[Dict[str, Any]]
    session_id: Optional[str] = None
    stream: bool = True
    compute_mode: Optional[str] = None
    model_name: Optional[str] = None
    effort: Optional[str] = None
    research_profile: Optional[Literal["general", "chemistry"]] = None
    input_files: List[str] = Field(default_factory=list)
    feedback_upload_id: Optional[str] = None
    orchestrator: Optional[str] = CLAUDE_CODE_ORCHESTRATOR
    # Legacy opt-in: absent explicit approvals, true selects both stages without
    # a timer. Explicit policy takes precedence; both stages default to off.
    hitl: bool = False
    approvals: Optional[approval_control.ApprovalPolicy] = None
    resume: bool = False


class CancelRequest(BaseModel):  # type: ignore
    session_id: str


class ControlRequest(BaseModel):  # type: ignore
    """A non-terminal operator control for a live run (docs/plans/2026-08-13-ui-operator-control-cc.md).

    ``verb`` ∈ ``cc_run_control.VALID_COMMANDS`` (pause/resume/stop/inject/steer). ``text``/``mode``
    apply only to ``steer`` (``mode`` ∈ now/next, default now). File-channel writes, so the run need
    not be on THIS process — but if the session is unknown here the write still lands in its workdir.
    """

    session_id: str
    verb: str
    text: Optional[str] = None
    mode: Optional[str] = None


class AnswerRequest(BaseModel):  # type: ignore
    """Answer an ordinary question or resolve one concrete approval request."""

    session_id: str
    text: str = ""
    qid: Optional[str] = None
    action: Literal["approve", "revise", "answer"] = "answer"


def _reject_foreign_orchestrator(orchestrator: Optional[str]) -> None:
    """Guard against a routing desync (mirror of the CodeArkt-side 400).

    ``None``/absent stays on the default path; anything other than
    ``claude_code`` reaching this runner means the ``X-Holo-Orchestrator`` route
    desynced, so fail loud instead of running the wrong pipeline.
    """
    if orchestrator is not None and orchestrator != CLAUDE_CODE_ORCHESTRATOR:
        raise HTTPException(
            status_code=400,
            detail=(
                "This runner only serves orchestrator='claude_code', "
                f"got '{orchestrator}'"
            ),
        )


def _require_control_auth(authorization: Optional[str]) -> None:
    """Bearer parity with ``POST /agents/manager`` for control WRITES: when app-login is
    configured, a control/answer write needs a valid signed token; dev (auth off) stays
    unguarded — the same posture the run-start endpoint has."""
    if auth.auth_enabled() and not auth.verify_token(authorization):
        raise HTTPException(status_code=401, detail="login required")


def _workspace_root() -> Path:
    """Resolve the shared workdir root the same way ui-api / the CodeArkt server
    do, so a CC session lands where ui-api reads it."""
    raw = os.environ.get("WORKSPACE_DIR")
    root = Path(raw).expanduser() if raw else (PROJECT_DIR / "workdir")
    return root.resolve()


def _last_user_prompt(messages: List[Dict[str, Any]]) -> str:
    """Extract the task text from the UI's ``messages`` list (last user turn)."""
    for msg in reversed(messages or []):
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str):
                return content
    return ""


def _resolve_model(model_name: Optional[str]) -> str:
    """Pick the SDK model for this run (correctness fix: no silent drop).

    A non-empty ``model_name`` from the request is honoured verbatim; otherwise
    the CC path's default (:data:`CC_DEFAULT_MODEL`) is used. Threading the field
    through means the per-run model selector actually takes effect on the CC
    path — an unrecognized model surfaces as an explicit SDK error in the stream
    (SC5's ``явная ошибка, не тихая подмена`` principle), never a silent
    substitution back to the default."""
    if model_name and model_name.strip():
        return model_name.strip()
    return CC_DEFAULT_MODEL


def _demo_fixed_launch_enabled() -> bool:
    # Read at use time: main loads .env after this module is imported.
    return os.environ.get("CC_DEMO_FIXED_LAUNCH", "").strip().lower() in {"1", "true", "yes"}


def _build_sdk_options(
    question: str,
    session_id: str,
    workdir: Path,
    compute_mode: Optional[str],
    model_name: Optional[str] = None,
    *,
    sdk_session_id: Optional[str] = None,
    resume: bool = False,
    hitl: bool = False,
    approvals: Optional[approval_control.ApprovalPolicy] = None,
    completion=None,
    executor_env: dict | None = None,
) -> Any:
    """Assemble ``ClaudeAgentOptions`` for one run, reusing the shared loaders.

    Mirrors ``scripts/run_research.py``'s option assembly (agents from
    regenerated ``.claude/agents/*.md``, MCP servers from ``config/claude-code.yaml``, manager
    prompt from ``system.yaml``, ``permission_mode='bypassPermissions'``,
    ``cwd=workdir``, 32 MiB buffer). Heavy SDK imports stay lazy so importing
    this module for tests never pulls in the SDK.

    ``model_name`` (the request's per-run model override) is resolved by
    :func:`_resolve_model`: honoured when non-empty, else :data:`CC_DEFAULT_MODEL`.
    The field is no longer parsed-then-discarded — it selects the SDK model.

    SC5: the per-run ``compute_mode`` filter (drop the ``mle-solver`` /
    ``mle-solver-remote`` variant the executor selector excludes) is applied to
    the loaded agents below. When ``ensure_synced()`` did not render the variant
    the mode requires, ``filter_agents_by_compute_mode`` raises
    ``ComputeModeUnavailableError`` — it propagates to ``_run_manager``'s handler,
    which surfaces it as an explicit stream error rather than a silent downgrade.
    """
    import run_research  # noqa: E402  (lazy: also pops placeholder API keys)
    from claude_agent_sdk import ClaudeAgentOptions
    from claude_agent_sdk.types import AgentDefinition, ThinkingConfigAdaptive
    from sync_claude_agents import ensure_synced
    from result_contract import RESULT_PROMPT

    policy = approval_control.normalize_policy(approvals, hitl=hitl)
    profile = research_session.read_research_profile(workdir)
    compute_mode = research_profiles.compute_mode(profile, compute_mode)
    if profile == "chemistry":
        raw_agents = research_profiles.load_chemistry_agents(compute_mode)
        manager_prompt = research_profiles.load_chemistry_manager_prompt(
            include_hitl=policy.enabled
        )
    else:
        # Chemistry overrides stay in memory; the general rendering is unchanged.
        ensure_synced()
        raw_agents = run_research.load_all_agents()
        manager_prompt = run_research.load_manager_prompt(include_hitl=policy.enabled)
    manager_prompt += "\n\n" + RESULT_PROMPT
    manager_prompt += "\n\n" + research_profiles.execution_prompt(compute_mode)
    if policy.enabled:
        manager_prompt += "\n\n" + approval_control.APPROVAL_RULES
    # SC5: hard ceiling on the mle-solver variant for this compute_mode. Raises
    # on a mode/env mismatch (the required variant wasn't rendered) so the run
    # fails loud in the stream instead of quietly using the wrong solver.
    raw_agents = cc_research_lib.filter_agents_by_compute_mode(raw_agents, compute_mode)
    mcp_servers = run_research.load_mcp_servers()
    if profile == "chemistry":
        mcp_servers = research_profiles.chemistry_mcp_servers(mcp_servers)
    # hitl: append the ask_human rule to the manager prompt (additive; a non-hitl
    # run is byte-identical). The server itself is registered manager-only below.
    manager_prompt = manager_prompt.replace("{{SESSION_NAME}}", session_id)

    # AlphaXiv Bearer injection (same as run_research): headless OAuth token, or
    # drop the server for this run if unavailable.
    if "alphaxiv" in mcp_servers:
        try:
            from alphaxiv_auth import get_access_token

            token = get_access_token()
        except Exception:
            token = None
        if token:
            mcp_servers["alphaxiv"]["headers"] = {"Authorization": f"Bearer {token}"}
        else:
            del mcp_servers["alphaxiv"]

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

    # HITL: register the in-process ask_human server AFTER the per-agent allowlists
    # (computed from ``all_server_names`` above) so it stays manager-only — sub-agents
    # never see it. Mirrors run_research.main()'s ordering.
    if policy.enabled:
        mcp_servers["hitl"], _ = run_research.build_hitl_server(
            workdir,
            approvals=policy,
            iteration_id=completion.iteration_id if completion else None,
        )

    opts_kwargs: Dict[str, Any] = dict(
        system_prompt=manager_prompt,
        cwd=str(workdir),
        model=_resolve_model(model_name),
        # Same reason as the CLI path: the SDK's bundled claude wins over PATH, and
        # it is the CLI that resolves model aliases (see run_research.resolve_cli_path).
        cli_path=run_research.resolve_cli_path(),
        thinking=ThinkingConfigAdaptive(type="adaptive"),
        effort="max",
        max_turns=300,
        permission_mode="bypassPermissions",
        # Research roles/hooks come from the SDK, not personal/project plugins.
        setting_sources=[],
        disallowed_tools=["ScheduleWakeup"],
        mcp_servers=mcp_servers,
        agents=agents,
        max_buffer_size=32 * 1024 * 1024,
    )
    if profile == "chemistry":
        from claude_agent_sdk import HookMatcher

        if compute_mode == "Local":
            opts_kwargs["disallowed_tools"] += research_profiles.REMOTE_TOOLS
        opts_kwargs["hooks"] = {
            "PreToolUse": [
                HookMatcher(
                    hooks=[research_profiles.ChemistryToolGate(session_id, workdir, compute_mode)]
                )
            ]
        }
    from claude_agent_sdk import HookMatcher
    from session_tools import MleSessionToolGate

    opts_kwargs.setdefault("hooks", {}).setdefault("PreToolUse", []).append(
        HookMatcher(hooks=[MleSessionToolGate(session_id, workdir)])
    )
    if policy.enabled:
        opts_kwargs.setdefault("hooks", {}).setdefault("PreToolUse", []).append(
            HookMatcher(
                hooks=[
                    approval_control.ApprovalGate(
                        workdir,
                        policy,
                        iteration_id=completion.iteration_id if completion else None,
                        activity=completion.activity if completion else None,
                    )
                ],
            )
        )
    if completion is not None:
        from claude_agent_sdk import HookMatcher

        for hook_name in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
            opts_kwargs.setdefault("hooks", {}).setdefault(hook_name, []).append(
                HookMatcher(hooks=[completion.activity.lifecycle_hook])
            )
        opts_kwargs.setdefault("hooks", {}).setdefault("Stop", []).append(
            HookMatcher(hooks=[completion.stop_hook])
        )
    # Durable resume parity with the CLI (docs/plans/2026-08-13-rate-limit-resume-cc.md):
    # a NEW run pins ``options.session_id`` (so the transcript is findable) and a
    # reconnect after a rate-limit hold replays it via ``options.resume``. Mutually
    # exclusive per the SDK, so set exactly one. Absent ``sdk_session_id`` -> unchanged
    # (SDK picks its own id; the pre-UI-control behavior).
    if sdk_session_id is not None:
        if resume:
            opts_kwargs["resume"] = sdk_session_id
        else:
            opts_kwargs["session_id"] = sdk_session_id
    import research_context

    research_context.configure_options(opts_kwargs, workdir)
    docker_bash.configure_options(opts_kwargs, workdir, executor_env)
    from cc_config import cli_environment

    from holosophos import model_catalog
    configured = model_catalog.claude_options(model_catalog.session_selection(workdir, model_name))
    opts_kwargs.pop("thinking", None)
    opts_kwargs.pop("effort", None)
    opts_kwargs.update({key: value for key, value in configured.items() if key != "env"})
    opts_kwargs["env"] = {**cli_environment(opts_kwargs.get("env")), **configured["env"]}
    return ClaudeAgentOptions(**opts_kwargs)


@contextlib.asynccontextmanager
async def _run_slot(
    dispatcher: SessionDispatcher,
    session: RunnerSession,
    registry: SessionRegistry,
    owner: Optional[str] = None,
) -> AsyncGenerator[None, None]:
    """Hold a CC run-slot for the duration of the wrapped run body (SC6).

    On entry: ``dispatcher.acquire`` blocks if the ``CC_MAX_ACTIVE_SESSIONS``
    pool is full, publishing ``session_queued`` (the UI banner) to *this*
    session's event queue while it waits, then ``session_running`` the moment a
    slot is held (fires whether or not it queued -- CodeArkt parity). On exit:
    release the slot, waking the next queued run and re-publishing
    ``session_position`` to every run still waiting. Reuses CodeArkt's dispatcher
    so the payload and position/depth bookkeeping are identical to ``app:5055``.

    ``session_queued``/``session_running`` always concern *this* session, so they
    go on ``session.events`` directly. ``session_position`` fires during *this*
    release for the *other* still-queued sessions, so -- unlike CodeArkt's shared
    session-keyed event bus, where one publish path routes by id -- it must be
    routed to each target session's own private queue via the ``registry``.

    A run cancelled while still queued is force-cancelled at ``acquire``'s
    ``await``; the dispatcher drops its queue bookkeeping and re-raises, so the
    slot was never held and this context manager's ``finally`` skips the release.
    """
    session_id = session.session_id
    events = session.events

    async def _on_queued(sid: str, position: int, depth: int) -> None:
        await events.put(session_queued_event(sid, position, depth))

    async def _on_running(sid: str) -> None:
        await events.put(session_running_event(sid))

    async def _on_position(sid: str, position: int, depth: int) -> None:
        # Refreshed position for a *different* still-queued session -> route to
        # ITS private queue (a lookup miss means it already left the queue).
        target = registry.get(sid)
        if target is not None:
            await target.events.put(session_position_event(sid, position, depth))

    # `owner` + `on_position` are only consulted in ledger mode (team login):
    # the per-account cap keys on `owner`, and a queued run re-publishes its own
    # position from the poll loop. Plain mode ignores both (unchanged).
    await dispatcher.acquire(
        session_id,
        owner=owner,
        on_queued=_on_queued,
        on_running=_on_running,
        on_position=_on_position,
    )
    try:
        yield
    finally:
        await dispatcher.release(session_id, on_position=_on_position)


async def _run_manager(
    session: RunnerSession,
    question: str,
    workdir: Path,
    compute_mode: Optional[str] = None,
    dispatcher: SessionDispatcher = DISPATCHER,
    registry: SessionRegistry = REGISTRY,
    user_id: Optional[str] = None,
    model_name: Optional[str] = None,
    hitl: bool = False,
    approvals: Optional[approval_control.ApprovalPolicy] = None,
    resume: bool = False,
) -> None:
    """Run one SDK session, publishing mapped ``AgentEvent``\\s to the queue.

    Registered as ``session.task`` so the cancel path can force-cancel it. On
    normal completion the SC2 mapping already emits the manager's closing
    ``agent_end``; this coroutine marks ``session.completed`` right then (before
    the slow trace flush) and puts the stream-end sentinel. When the session was
    interrupted, the cancel handler owns the terminal + sentinel, so this path
    emits neither (no duplicate close).

    ``compute_mode`` (SC5) selects the mle-solver variant for this run; a
    mode/env mismatch raised by ``_build_sdk_options`` is caught below and
    surfaced as an explicit stream error (not a silent downgrade). ``model_name``
    (the per-run model override) is threaded into ``_build_sdk_options`` so the
    request field actually selects the SDK model instead of being dropped.

    SC6: the whole run body is wrapped in :func:`_run_slot`, so the SDK loop
    (and the opening ``agent_start``) only begin once a ``CC_MAX_ACTIVE_SESSIONS``
    slot is held -- a run that had to wait streams ``session_queued`` first.

    SC7: the SDK run is wrapped in ``cc_research_lib.trace_run_context`` so the v3
    trace's ``session_id`` == this run's session_id and its ``user`` == the
    browser client (``user_id``, from ``X-Holo-Client``) when non-empty, empty
    otherwise -- the login->user parity of ``ad3f449`` on the CC path. The bridge
    is configured (once) *before* ``ClaudeSDKClient`` is imported below.
    """
    session_id = session.session_id
    events = session.events
    run_status = "error"
    result_error = False
    result_seen = False
    last_answer = None
    error_detail = None
    completion = None
    result_bytes = None
    title_task = None
    # SC7: configure the v3 tracer before importing ClaudeSDKClient so the
    # langsmith bridge has patched the class this run instantiates.
    try:
        if session.interrupted:
            return  # A cancel can arrive before this task gets its first tick.
        if session.iteration_id:
            from result_contract import ResultContract

            completion = ResultContract(
                workdir, session.iteration_id, interrupted=lambda: session.interrupted
            )
        _ensure_langfuse_ready()
        async with _run_slot(dispatcher, session, registry, owner=user_id):
            research_session.save_run_status(workdir, "running")
            await events.put(cc_research_lib.manager_start_event(session_id))

            # Lazy: the shared driver + control channel (heavy SDK imports stay out of
            # module import so tests can import cc_runner without the SDK).
            import run_research
            import cc_run_control
            import session_titles
            from claude_agent_sdk import ClaudeSDKClient

            task_agents = completion.activity if completion else cc_research_lib.ResearchActivity()
            if completion:
                def publish_activity(records):
                    if not session.interrupted:
                        for record in records:
                            session.events.put_nowait(cc_research_lib.ResearchEvent(session_id=session_id, **record))
                completion.activity_sink = publish_activity
            start = time.time()

            # Durable SDK session id — distinct from the browser ``session_id`` (run identity /
            # workdir key). Pin + persist it so a rate-limit reconnect can ``--resume`` the real
            # transcript, and a later CLI ``--resume`` can pick the run up after a stop.
            # Valid dashed UUID (the claude CLI's --session-id contract; ``.hex`` has no
            # hyphens and the CLI rejects it). Same helper the CLI path uses.
            if resume:
                saved = cc_run_control.read_session_info(workdir)
                if saved is None:
                    raise ValueError(
                        "Saved conversation is missing; cannot resume this session"
                    )
                sdk_session_id = saved["session_id"]
            else:
                sdk_session_id = cc_run_control.new_session_id()
                cc_run_control.write_session_info(workdir, sdk_session_id, question)

            if session.iteration_id:
                research_iterations.update_iteration(
                    workdir, session.iteration_id, sdk_session_id=sdk_session_id
                )

            def _emit(msg: Any, _start: float, _workdir: Path) -> None:
                """Event-queue render (the shared driver's message sink for a UI run)."""
                nonlocal result_error, result_seen, last_answer, error_detail
                if session.interrupted:
                    return  # cancel owns the terminal + sentinel; stop feeding the queue
                for event in cc_research_lib.map_sdk_message_to_events(
                    msg,
                    session_id,
                    task_agents,
                    include_result_output=completion.observe(msg)
                    if completion
                    else True,
                ):
                    # B6: drop the per-turn manager agent_end (the multi-turn driver emits one on
                    # every ResultMessage, which would flap the UI stage view across steer/pause/
                    # rate-limit boundaries). Exactly one deferred close is emitted after the run
                    # truly ends, below. The result OUTPUT is kept.
                    if _is_manager_agent_end(event):
                        continue
                    session.events.put_nowait(event)
                if type(msg).__name__ == "ResultMessage":
                    result_seen = True
                    last_answer = getattr(msg, "result", None)
                    result_error = bool(getattr(msg, "is_error", False))
                    error_detail = None
                    if result_error:
                        detail = (
                            "; ".join(getattr(msg, "errors", None) or [])
                            or getattr(msg, "result", None)
                            or "Research iteration failed"
                        )
                        error_detail = detail
                        session.events.put_nowait(
                            AgentEvent(
                                session_id=session_id,
                                agent_name="manager",
                                event_type=EventType.OUTPUT,
                                content=f"Runner error: {detail}",
                            )
                        )

            def _on_wait_tick(remaining_s: float) -> None:
                """Rate-limit heartbeat: keep the stream alive + drive the UI status."""
                if not session.interrupted:
                    session.events.put_nowait(
                        rate_limit_wait_event(session_id, remaining_s)
                    )

            def client_factory(resume: bool) -> Any:
                options = _build_sdk_options(
                    question,
                    session_id,
                    workdir,
                    compute_mode,
                    model_name,
                    sdk_session_id=sdk_session_id,
                    resume=resume,
                    hitl=hitl,
                    approvals=approvals,
                    completion=completion,
                    executor_env=executor.env,
                )
                client = ClaudeSDKClient(options=options)
                # B5: keep cancel's graceful ``client.interrupt()`` path alive across every
                # (re)connect — the driver creates the client, so publish it to the session.
                session.client = client
                return client

            async with docker_bash.executor_session(workdir) as executor:
                if not resume:
                    title_task = session_titles.start_title_task(
                        workdir, completion.item["user_message"] if completion else question
                    )
                with cc_research_lib.trace_run_context(
                    _PROPAGATE_ATTRIBUTES, session_id, user_id
                ):
                    await run_research._drive_across_limits(
                        client_factory,
                        workdir,
                        session_id,
                        start,
                        cc_run_control,
                        first_query=completion.query(
                            question, mode="feedback" if resume else "initial"
                        )
                        if completion
                        else question,
                        start_resume=resume,
                        render=_emit,
                        poll_interval=CC_CTL_POLL_INTERVAL,
                        on_wait_tick=_on_wait_tick,
                        no_auto_wait=CC_NO_AUTO_WAIT,
                        buffer_s=float(os.environ.get("RATE_LIMIT_BUFFER_SECONDS", "60")),
                        backoff_s=float(
                            os.environ.get("RATE_LIMIT_BACKOFF_SECONDS", "600")
                        ),
                        max_waits=int(os.environ.get("RATE_LIMIT_MAX_WAITS", "24")),
                        completion=completion,
                    )
        # Run body finished normally (natural completion or operator stop). Emit the ONE
        # deferred manager close (B6), then latch completion NOW -- before the awaiting teardown
        # below (``finally`` runs a slow, networked Langfuse flush) -- so a cancel arriving in
        # that tail is an idempotent no-op, not a spurious ``agent_interrupted`` that would tag
        # this finished run cancelled. A cancelled run's terminal is owned by cancel_session.
        if not session.interrupted:
            stopped = cc_run_control.is_checkpoint_stop(workdir)
            if not stopped:
                if cc_run_control.read_rate_limit(workdir) is not None:
                    raise RuntimeError("Research did not finish: usage limit reached")
                if not result_seen:
                    raise RuntimeError("Research ended without a final SDK response")
                if completion:
                    result_bytes = completion.validated_result()
            await events.put(
                interrupted_event(session_id)
                if stopped
                else cc_research_lib.manager_end_event(session_id)
            )
            session.completed = True
            run_status = (
                "cancelled" if stopped else "error" if result_error else "complete"
            )
    except asyncio.CancelledError:
        # Force-cancel from the cancel path: let it unwind the SDK context
        # (killing the CLI subprocess) and re-raise so the task ends cancelled.
        run_status = "cancelled"
        raise
    except Exception as exc:  # fatal SDK error inside the stream (spec mapping)
        error_detail = str(exc)
        logger.exception("cc_runner session %s failed", session_id)
        if not session.interrupted:
            await events.put(
                AgentEvent(
                    session_id=session_id,
                    agent_name=cc_research_lib.MANAGER_AGENT_NAME,
                    event_type=EventType.OUTPUT,
                    content=f"Runner error: {exc}",
                )
            )
            await events.put(
                AgentEvent(
                    session_id=session_id,
                    agent_name=cc_research_lib.MANAGER_AGENT_NAME,
                    event_type=EventType.AGENT_END,
                    content=None,
                )
            )
            # Error terminal emitted -> latch completion too, so a cancel in the
            # teardown tail can't re-tag this (already terminal) run.
            session.completed = True
    finally:
        import session_titles
        await session_titles.cancel_title_task(title_task)
        try:
            approval_control.cancel_pending(workdir)
            run_status = _finish_iteration_history(
                session,
                workdir,
                "cancelled" if session.interrupted else run_status,
                answer=completion.answer if completion else last_answer,
                error=error_detail,
                result_path=completion.output_path if completion else None,
                result_bytes=result_bytes,
                plan_documents=completion.plans if completion else None,
            )
            research_session.save_run_status(workdir, run_status)
        finally:
            # Closing the response must not depend on a successful disk write.
            if not session.interrupted:
                await events.put(_STREAM_END)
        # Close the stream FIRST -- before the slow, networked Langfuse flush --
        # so the reader drains the sentinel and drops the session from the
        # registry promptly, shrinking the window in which a cancel could race
        # a finished run. An interrupted run's terminal + sentinel were queued by
        # cancel_session, so skip both here.
        # SC7: flush the v3 trace so it lands promptly after the run (best-effort,
        # off the event loop). No-op when tracing is not configured.
        await _flush_langfuse()


async def _stream_session(
    registry: SessionRegistry, session: RunnerSession
) -> AsyncGenerator[str, None]:
    """Serialize one iteration without letting reader teardown end its runner."""
    try:
        while True:
            item = await session.events.get()
            if item is _STREAM_END:
                break
            yield item.model_dump_json() + "\n"
    finally:
        # A disconnected browser does not end its runner. An older stream also
        # cannot remove a subsequent iteration registered under the same ID.
        if session.task is None or session.task.done():
            session.finished = True
            registry.remove(session.session_id, expected=session)


def create_app() -> FastAPI:
    app = FastAPI(title="Holosophus Claude-Code Runner", version="1.0.0")

    @app.get("/health")
    async def health() -> JSONResponse:  # noqa: D401
        return JSONResponse({"status": "ok"})

    @app.post("/agents/manager")
    async def manager(
        request: ManagerRequest,
        x_holo_client: Optional[str] = Header(default=None),
        authorization: Optional[str] = Header(default=None),
    ) -> Any:
        _reject_foreign_orchestrator(request.orchestrator)

        # App-level team auth (parity with the CodeArkt server): when a login
        # list is configured, starting a run needs a valid signed bearer token,
        # and the verified username -- not the forgeable X-Holo-Client header --
        # is the run's owner (artifact marker, Langfuse `user`, per-account
        # quota). Auth off (dev) keeps the old X-Holo-Client-as-owner behaviour.
        authenticated_user = (
            auth.verify_token(authorization) if auth.auth_enabled() else None
        )
        if auth.auth_enabled() and not authenticated_user:
            raise HTTPException(status_code=401, detail="login required to start a run")
        owner = authenticated_user or x_holo_client

        import cc_run_control

        session_id = request.session_id or research_session.new_session_id()
        question = _last_user_prompt(request.messages)
        user_message = question
        feedback_files = []
        try:
            workdir = research_session.session_directory(_workspace_root(), session_id)
            research_session.check_session_owner(workdir, owner)
            if REGISTRY.is_active(session_id):
                raise HTTPException(
                    status_code=409,
                    detail="This session already has an active iteration",
                )
            saved_conversation = cc_run_control.read_session_info(workdir)
            if request.resume and saved_conversation is None:
                raise HTTPException(
                    status_code=409,
                    detail="Saved conversation is missing; cannot resume this session",
                )
            if not request.resume and saved_conversation is not None:
                raise HTTPException(
                    status_code=409,
                    detail="This session already exists; send feedback with resume=true",
                )
            if request.resume and not question.strip():
                raise ValueError("Feedback requires non-empty text")
            saved_settings = (
                research_session.read_session_settings(workdir)
                if request.resume
                else {}
            )
            profile = research_session.resolve_research_profile(
                workdir, request.research_profile
            )
            from holosophos import model_catalog

            # Temporary demo policy, 2026-10-10. Applies before validating UI
            # selections or restoring old launch settings; approvals stay below.
            # Disable CC_DEMO_FIXED_LAUNCH to restore normal per-session routing.
            if _demo_fixed_launch_enabled():
                selected_compute_mode = "Local"
                selection = model_catalog.resolve("claude_code", "opus", "high")
            else:
                selected_compute_mode = research_profiles.compute_mode(
                    profile,
                    saved_settings.get("compute_mode", "Auto")
                    if request.resume
                    else request.compute_mode,
                )
                selection = (model_catalog.session_selection(workdir) if request.resume else
                             model_catalog.resolve("claude_code", request.model_name, request.effort))
                if request.resume and (
                    (request.model_name and request.model_name != selection["id"])
                    or (request.effort and request.effort != selection.get("effort"))
                ):
                    raise ValueError("Model and effort are fixed for this session")
            model_catalog.claude_options(selection)  # Validate credentials before admission.
            selected_model = selection["id"]
            # Remember the initial form separately from the actual SDK settings.
            # Feedback must never replace this snapshot with its own payload.
            requested_launch = saved_settings.get("requested_launch")
            if not request.resume:
                try:
                    requested_model = model_catalog.resolve(
                        "claude_code", request.model_name, request.effort
                    )
                    requested_launch = {
                        "model_name": requested_model["id"],
                        "model_label": requested_model["label"],
                        "effort": requested_model.get("effort"),
                        "compute_mode": research_profiles.compute_mode(profile, request.compute_mode),
                    }
                except ValueError:
                    # An ignored UI selection must not block the fixed demo run.
                    requested_launch = None
            policy_input = request.approvals
            if (
                request.resume
                and policy_input is None
                and "hitl" not in request.model_fields_set
            ):
                policy_input = saved_settings.get("approvals")
            legacy_hitl = (
                request.hitl
                if "hitl" in request.model_fields_set
                else bool(saved_settings.get("hitl", False))
            )
            policy = approval_control.normalize_policy(policy_input, hitl=legacy_hitl)
            attachments = research_session.input_paths(workdir, request.input_files)
            if request.feedback_upload_id:
                from holosophos.feedback_inputs import read_batch

                if not request.resume:
                    raise ValueError("Feedback uploads require resume=true")
                feedback_files = read_batch(
                    workdir, request.feedback_upload_id, attachments,
                    research_iterations.list_iterations(workdir),
                )
            elif request.resume and attachments:
                raise ValueError("New feedback attachments require a saved upload batch")
        except PermissionError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if profile == "chemistry":
            try:
                await research_profiles.check_chemistry_services()
            except ValueError as error:
                raise HTTPException(status_code=503, detail=str(error)) from error
        question = research_session.with_input_files(question, attachments)
        if feedback_files:
            question += (
                "\nThese are new attachments for this feedback iteration. Earlier input files remain available; "
                "use the versions listed here for the requested update and do not overwrite earlier inputs. "
                "Original filenames and stored paths:\n"
                + "\n".join(f"- {file['name']}: {file['path']}" for file in feedback_files)
            )
        # Chemistry service probing above yields. Recheck before any mutation.
        if REGISTRY.is_active(session_id):
            raise HTTPException(
                status_code=409, detail="This session already has an active iteration"
            )
        try:
            research_session.check_session_owner(workdir, owner, claim=True)
            research_session.save_research_profile(workdir, profile)
            research_session.save_session_model(workdir, selected_model)
            model_catalog.save_selection(workdir, selection)
            research_session.save_session_settings(
                workdir,
                {
                    "compute_mode": selected_compute_mode,
                    "model_label": selection["label"],
                    "effort": selection.get("effort"),
                    "approvals": policy.model_dump(),
                    "requested_launch": requested_launch,
                },
            )
        except PermissionError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

        # SC3: full artifact/owner parity before the SDK loop.
        cc_research_lib.scaffold_cc_session(
            workdir, client_id=owner, prompt_text=question
        )
        if not request.resume:
            research_session.initialize_display_metadata(workdir, user_message)

        approval_control.cancel_pending(workdir)
        # A prior stop/answer must not apply to the new feedback iteration.
        cc_run_control.poll_command(workdir)
        cc_run_control.read_steer(workdir)
        cc_run_control.read_inject(workdir)
        cc_run_control.clear_answer(workdir)
        cc_run_control.clear_checkpoint_stop(workdir)
        cc_run_control.clear_rate_limit(workdir)
        try:
            iteration = research_iterations.start_iteration(
                workdir,
                kind="feedback" if request.resume else "initial",
                user_message=user_message,
                settings={
                    "research_profile": profile,
                    "model_name": selected_model,
                    "model_label": selection["label"],
                    "effort": selection.get("effort"),
                    "compute_mode": selected_compute_mode,
                    "approvals": policy.model_dump(),
                    "input_files": attachments,
                    **({"feedback_upload_id": request.feedback_upload_id} if request.feedback_upload_id else {}),
                },
                # The registry was checked twice without yielding since the last
                # check. A remaining durable active record belongs to a lost run.
                recover_abandoned=True,
            )
        except (OSError, ValueError) as error:
            raise HTTPException(
                status_code=503, detail="Cannot initialize iteration history"
            ) from error
        try:
            research_session.save_run_status(workdir, "queued")
        except OSError as error:
            research_iterations.finish_iteration(
                workdir,
                iteration["id"],
                status="error",
                error="Cannot initialize run status",
            )
            raise HTTPException(
                status_code=503, detail="Cannot initialize run status"
            ) from error
        session = RunnerSession(
            session_id=session_id,
            events=IterationEventQueue(workdir, iteration["id"]),
            iteration_id=iteration["id"],
        )
        REGISTRY.register(session)
        # SC5: carry the run's compute_mode so the mle-solver variant filter
        # (and its explicit stream error on a mode/env mismatch) applies per run.
        # SC6: gate on the shared process-local DISPATCHER so the (N+1)th
        # concurrent run queues (session_queued banner) behind the pool.
        # SC7: carry X-Holo-Client as the v3 trace user (non-empty -> user set,
        # absent -> user empty; parity with ad3f449).
        # model_name: the per-run model override -> selects the SDK model instead
        # of being silently dropped (defaults to CC_DEFAULT_MODEL when empty).
        session.task = asyncio.create_task(
            _run_manager(
                session,
                question,
                workdir,
                selected_compute_mode,
                get_dispatcher(),
                REGISTRY,
                owner,
                selected_model,
                request.hitl,
                policy,
                request.resume,
            )
        )

        def finished(task):
            try:
                approval_control.close_iteration(workdir, session.iteration_id)
                # Covers cancellation before _run_manager entered its try/finally.
                # Already-finalized records are immutable and this is a no-op.
                terminal_status = (
                    "cancelled" if session.interrupted or task.cancelled() else "error"
                )
                if not session.events.stream_closed:
                    session.events.put_nowait(
                        interrupted_event(session_id)
                        if terminal_status == "cancelled"
                        else cc_research_lib.manager_end_event(session_id)
                    )
                changed = research_iterations.finish_iteration(
                    workdir,
                    session.iteration_id,
                    status=terminal_status,
                    error="Runner ended before recording completion",
                )
                if changed:
                    research_session.save_run_status(workdir, terminal_status)
            except (OSError, ValueError):
                logger.exception(
                    "Cannot finalize abandoned iteration %s", session.iteration_id
                )
            finally:
                if not session.events.stream_closed:
                    session.events.put_nowait(_STREAM_END)
                REGISTRY.remove(session_id, expected=session)

        session.task.add_done_callback(finished)

        return StreamingResponse(
            _stream_session(REGISTRY, session),
            media_type="application/x-ndjson",
            headers=AGENT_RESPONSE_HEADERS,
        )

    @app.post("/agents/cancel")
    async def cancel(
        request: CancelRequest,
        x_holo_orchestrator: Optional[str] = Header(default=None),
        authorization: Optional[str] = Header(default=None),
        x_holo_client: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        # A cancel that reaches the wrong backend is still idempotently
        # successful there; no orchestrator 400 on cancel (the run may already
        # be gone). SC4: cancelling unknown/finished is success, never a raise.
        workdir = _control_workdir(request.session_id, authorization, x_holo_client)
        if REGISTRY.is_active(request.session_id):
            approval_control.cancel_pending(workdir)
        return await cancel_session(REGISTRY, request.session_id)

    # ── Operator control (docs/plans/2026-08-13-ui-operator-control-cc.md) ──
    # Non-terminal steering of a live run through the file channel. All three are
    # thin: they resolve workdir = WORKSPACE_DIR/<session_id> (same key ui-api and
    # the manager endpoint use) and delegate to a cc_run_control helper. The run
    # need not be registered on THIS process for the write to land.

    def _control_workdir(
        session_id: str,
        authorization: Optional[str] = None,
        client_id: Optional[str] = None,
    ) -> Path:
        _require_control_auth(authorization)
        owner = auth.verify_token(authorization) if auth.auth_enabled() else client_id
        try:
            workdir = research_session.session_directory(_workspace_root(), session_id)
            research_session.check_session_owner(workdir, owner)
            return workdir
        except PermissionError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/agents/control")
    async def control(
        request: ControlRequest,
        authorization: Optional[str] = Header(default=None),
        x_holo_client: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        import cc_run_control  # lazy: keep module import SDK/codearkt-free elsewhere

        _require_control_auth(authorization)
        verb = (request.verb or "").strip()
        if verb not in cc_run_control.VALID_COMMANDS:
            raise HTTPException(
                status_code=400,
                detail=f"unknown control verb '{verb}'; expected one of "
                f"{sorted(cc_run_control.VALID_COMMANDS)}",
            )
        workdir = _control_workdir(request.session_id, authorization, x_holo_client)
        if verb == cc_run_control.CMD_STOP:
            approval_control.cancel_pending(workdir)
        if verb == cc_run_control.CMD_STEER:
            text = (request.text or "").strip()
            if not text:
                raise HTTPException(
                    status_code=400, detail="steer requires non-empty text"
                )
            mode = request.mode or cc_run_control.STEER_NOW
            if mode not in cc_run_control.STEER_MODES:
                mode = cc_run_control.STEER_NOW  # tolerant: invalid mode -> now
            cc_run_control.send_steer(workdir, text, mode)
            return {
                "status": "ok",
                "verb": verb,
                "mode": mode,
                "session_id": request.session_id,
            }
        if verb == cc_run_control.CMD_INJECT:
            text = (request.text or "").strip()
            if not text:
                raise HTTPException(
                    status_code=400, detail="inject requires non-empty text"
                )
            cc_run_control.write_inject(workdir, text)
            cc_run_control.send_command(workdir, verb)
            return {"status": "ok", "verb": verb, "session_id": request.session_id}
        cc_run_control.send_command(workdir, verb)
        return {"status": "ok", "verb": verb, "session_id": request.session_id}

    @app.post("/agents/answer")
    async def answer(
        request: AnswerRequest,
        authorization: Optional[str] = Header(default=None),
        x_holo_client: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        workdir = _control_workdir(request.session_id, authorization, x_holo_client)
        try:
            result = approval_control.submit_answer(
                workdir, request.qid, request.action, request.text
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {**result, "session_id": request.session_id}

    @app.get("/agents/question/{session_id}")
    async def question(
        session_id: str,
        authorization: Optional[str] = Header(default=None),
        x_holo_client: Optional[str] = Header(default=None),
    ) -> Any:
        q = approval_control.read_pending_question(
            _control_workdir(session_id, authorization, x_holo_client)
        )
        if q is None:
            return Response(status_code=204)
        return q

    @app.get("/agents/session/{session_id}")
    async def session_settings(
        session_id: str,
        authorization: Optional[str] = Header(default=None),
        x_holo_client: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        import cc_run_control

        workdir = _control_workdir(session_id, authorization, x_holo_client)
        active = REGISTRY.is_active(session_id)
        has_conversation = cc_run_control.read_session_info(workdir) is not None
        if not active and not has_conversation:
            raise HTTPException(
                status_code=404,
                detail="Saved conversation is missing; cannot resume this session",
            )
        try:
            saved = research_session.read_session_settings(workdir)
            policy = approval_control.normalize_policy(
                saved.get("approvals"), hitl=bool(saved.get("hitl", False))
            )
            profile = research_session.read_research_profile(workdir)
            return {
                "session_id": session_id,
                "research_profile": profile,
                "model_name": research_session.read_session_model(workdir)
                or CC_DEFAULT_MODEL,
                "model_label": saved.get("model_label"),
                "effort": saved.get("effort"),
                "compute_mode": research_profiles.compute_mode(
                    profile, saved.get("compute_mode", "Auto")
                ),
                "approvals": policy.model_dump(),
                "active": active,
                "can_resume": not active and has_conversation,
                "status": research_session.read_marker(workdir, "run_status")
                or "complete",
                "last_decision": approval_control.read_last_decision(workdir),
                "requested_launch": saved.get("requested_launch") if _demo_fixed_launch_enabled() else None,
            }
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    return app


app = create_app()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    # SC7: configure v3 tracing (and patch ClaudeSDKClient) once at startup,
    # before any request imports the client. Idempotent -- _run_manager also
    # guards it defensively for non-main entry paths.
    _ensure_langfuse_ready()
    if _demo_fixed_launch_enabled():
        logger.info("Temporary demo launch policy: opus / high / Local; approvals follow the request")
    logger.info(
        "cc_runner listening on %s:%s (langfuse v3 tracing: %s)",
        CC_RUNNER_BIND,
        CC_RUNNER_PORT,
        "on" if _LANGFUSE_CLIENT is not None else "off",
    )
    uvicorn.run(app, host=CC_RUNNER_BIND, port=CC_RUNNER_PORT, access_log=False)


if __name__ == "__main__":
    main()
