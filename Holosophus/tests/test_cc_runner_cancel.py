"""SC4 (ui-claude-code-orchestrator): CC runner cancellation + registry.

Drives ``scripts/cc_runner.py``'s cancel path directly (no real SDK/CLI): the
session registry, ``cancel_session`` idempotency, the ``agent_interrupted``
terminal it injects, and the force-cancel fallback that would kill the CLI
subprocess when graceful ``interrupt()`` is ignored.

The *live* half of SC4 (a real claude CLI subprocess dying <=15 s in a browser
run) is the Validation Plan's manual e2e check — this file is the automated
idempotency unit the spec's ``## Tests`` row asks for ("unit на идемпотентность
реестра"). Async bodies run via ``asyncio.run`` so the file needs no
pytest-asyncio configuration at the repo-root test home.
"""

import asyncio
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from cc_runner import (  # noqa: E402
    _STREAM_END,
    RunnerSession,
    SessionRegistry,
    _stream_session,
    cancel_session,
    interrupted_event,
)
from codearkt.event_bus import EventType  # noqa: E402


class _FakeClient:
    """Stand-in for ``ClaudeSDKClient``: records graceful-interrupt attempts."""

    def __init__(self) -> None:
        self.interrupts = 0

    async def interrupt(self) -> None:
        self.interrupts += 1


def _make_session(session_id: str = "sess-1") -> RunnerSession:
    return RunnerSession(session_id=session_id, events=asyncio.Queue())


def _drain(queue: "asyncio.Queue") -> list:
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


# ── Registry basics ──


def test_registry_register_get_remove_are_idempotent() -> None:
    reg = SessionRegistry()
    s = _make_session("a")
    reg.register(s)
    assert "a" in reg
    assert reg.get("a") is s
    assert reg.active_ids() == ["a"]
    assert len(reg) == 1
    # Re-registering the same object is idempotent; a new active iteration
    # cannot replace it (covered by the feedback concurrency regression).
    reg.register(s)
    assert len(reg) == 1
    reg.remove("a")
    assert "a" not in reg
    assert reg.get("a") is None
    # Removing an already-removed / never-present id must not raise.
    reg.remove("a")
    reg.remove("never")
    assert len(reg) == 0


# ── Cancel of unknown / finished sessions -> idempotent success ──


def test_cancel_unknown_session_is_success_and_leaves_registry_intact() -> None:
    async def body() -> None:
        reg = SessionRegistry()
        other = _make_session("live")
        reg.register(other)
        result = await cancel_session(reg, "missing")
        assert result == {"status": "cancelled", "session_id": "missing", "active": False}
        # The unrelated live session is untouched, and no terminal was queued.
        assert "live" in reg
        assert other.events.empty()
        assert other.interrupted is False

    asyncio.run(body())


def test_cancel_finished_session_is_success_without_emitting() -> None:
    async def body() -> None:
        reg = SessionRegistry()
        s = _make_session()
        s.finished = True
        reg.register(s)
        result = await cancel_session(reg, s.session_id)
        assert result["active"] is False
        # A finished run gets no second terminal / interrupt.
        assert s.events.empty()
        assert s.interrupted is False

    asyncio.run(body())


def test_cancel_completed_session_in_teardown_tail_is_noop() -> None:
    # grounding fix: a run whose SDK body finished (manager agent_end already
    # queued) but is still in the registry during its slow Langfuse-flush tail is
    # latched ``completed``. A cancel landing in that window must be an idempotent
    # no-op -- NOT inject a spurious agent_interrupted after the genuine agent_end
    # (which would retroactively tag a successful run cancelled in the browser).
    async def body() -> None:
        reg = SessionRegistry()
        s = _make_session()
        s.client = _FakeClient()
        s.task = asyncio.create_task(asyncio.sleep(30))
        s.completed = True  # run body done, still registered (flush in flight)
        reg.register(s)

        result = await cancel_session(reg, s.session_id, interrupt_timeout=0.05)

        assert result["active"] is False
        # No terminal injected, no graceful interrupt attempted, run not marked
        # interrupted -> the successful run keeps its genuine outcome.
        assert s.events.empty()
        assert s.client.interrupts == 0
        assert s.interrupted is False
        # The still-running teardown task is left alone (it ends on its own).
        assert not s.task.cancelled()
        s.task.cancel()

    asyncio.run(body())


# ── Cancel of a live session ──


def test_cancel_live_session_ignoring_interrupt_force_cancels_and_emits() -> None:
    async def body() -> None:
        reg = SessionRegistry()
        s = _make_session()
        s.client = _FakeClient()
        # A run that ignores interrupt (never returns on its own).
        s.task = asyncio.create_task(asyncio.sleep(30))
        reg.register(s)

        result = await cancel_session(reg, s.session_id, interrupt_timeout=0.05)

        assert result["active"] is True
        assert result["status"] == "cancelled"
        # Graceful interrupt was attempted first.
        assert s.client.interrupts == 1
        # Ignored -> force-cancelled so the CLI subprocess would die.
        assert s.task.done()
        assert s.task.cancelled()
        assert s.interrupted is True
        assert s.finished is True
        # Terminal agent_interrupted (manager) + stream-end sentinel queued.
        queued = _drain(s.events)
        assert queued[0].event_type == EventType.AGENT_INTERRUPTED
        assert queued[0].agent_name == "manager"
        assert queued[0].session_id == s.session_id
        assert queued[-1] is _STREAM_END

    asyncio.run(body())


def test_cancel_live_session_honoring_interrupt_is_not_force_cancelled() -> None:
    async def body() -> None:
        reg = SessionRegistry()
        s = _make_session()
        s.client = _FakeClient()
        # A run that winds down on its own promptly after interrupt().
        s.task = asyncio.create_task(asyncio.sleep(0.001))
        reg.register(s)

        result = await cancel_session(reg, s.session_id, interrupt_timeout=1.0)

        assert result["active"] is True
        assert s.client.interrupts == 1
        assert s.task.done()
        # Completed on its own within the grace budget -> not force-cancelled.
        assert not s.task.cancelled()

    asyncio.run(body())


def test_double_cancel_of_same_session_is_idempotent() -> None:
    async def body() -> None:
        reg = SessionRegistry()
        s = _make_session()
        s.client = _FakeClient()
        s.task = asyncio.create_task(asyncio.sleep(30))
        reg.register(s)

        first = await cancel_session(reg, s.session_id, interrupt_timeout=0.05)
        assert first["active"] is True
        # Second cancel while still registered short-circuits (latched finished).
        second = await cancel_session(reg, s.session_id, interrupt_timeout=0.05)
        assert second["active"] is False
        # No second interrupt attempt on the already-torn-down run.
        assert s.client.interrupts == 1

    asyncio.run(body())


def test_cancel_without_client_or_task_still_emits_terminal() -> None:
    # Cancel racing a run that registered but hasn't entered the SDK context yet
    # (client/task still unset) must not raise and must still close the stream.
    async def body() -> None:
        reg = SessionRegistry()
        s = _make_session()
        reg.register(s)
        result = await cancel_session(reg, s.session_id, interrupt_timeout=0.05)
        assert result["active"] is True
        queued = _drain(s.events)
        assert queued[0].event_type == EventType.AGENT_INTERRUPTED
        assert queued[-1] is _STREAM_END

    asyncio.run(body())


def test_cancelled_stream_serializes_agent_interrupted_and_unregisters() -> None:
    # End-to-end of the SC4 wire path: cancel queues the terminal, the stream
    # reader serializes exactly that one AGENT_INTERRUPTED NDJSON line, stops on
    # the sentinel, and drops the session so a later cancel is a clean success.
    async def body() -> None:
        reg = SessionRegistry()
        s = _make_session()
        s.client = _FakeClient()
        s.task = asyncio.create_task(asyncio.sleep(30))
        reg.register(s)

        await cancel_session(reg, s.session_id, interrupt_timeout=0.05)

        lines = [line async for line in _stream_session(reg, s)]
        assert len(lines) == 1
        assert '"event_type":"agent_interrupted"' in lines[0].replace(" ", "")
        assert s.session_id not in reg
        assert s.finished is True

        # The now-unregistered session cancels as an idempotent no-op success.
        result = await cancel_session(reg, s.session_id)
        assert result["active"] is False

    asyncio.run(body())


def test_interrupted_event_shape_matches_wire_contract() -> None:
    ev = interrupted_event("sess-x")
    assert ev.session_id == "sess-x"
    assert ev.agent_name == "manager"
    assert ev.event_type == EventType.AGENT_INTERRUPTED
    assert ev.content is None


# ── HTTP boundary: POST /agents/cancel on an unknown session -> 200, no crash ──


def test_cancel_endpoint_unknown_session_returns_success() -> None:
    # SC4: cancelling a nonexistent session at the HTTP layer returns success
    # (no exception escapes to a 500), so the UI's Stop click is always safe.
    from fastapi.testclient import TestClient

    import cc_runner

    with TestClient(cc_runner.app) as client:
        resp = client.post("/agents/cancel", json={"session_id": "does-not-exist"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "cancelled"
        assert body["active"] is False

        # And the liveness probe answers.
        assert client.get("/health").status_code == 200
