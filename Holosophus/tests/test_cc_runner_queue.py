"""SC6 (ui-claude-code-orchestrator): CC runner run-slot queue.

Drives ``scripts/cc_runner.py``'s SC6 gate directly (no real SDK/CLI): the
runner's own ``CC_MAX_ACTIVE_SESSIONS`` admission pool, the ``session_queued``
banner an (N+1)th run gets, the ``session_running`` it gets once a slot frees,
and the exact ``{"resource":"session","position","depth"}`` wire payload the UI
reducer (``ui/src/live.ts``) already parses for the CodeArkt path.

The *live* half of SC6 (a second CC run from a second browser tab showing the
queue banner, with ``GET /capacity`` unchanged) is the Validation Plan's manual
e2e check — this file is the automated queue unit the spec's ``## Tests`` row
asks for ("unit (очередь)"). Async bodies run via ``asyncio.run`` so the file
needs no pytest-asyncio configuration at the repo-root test home.
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Callable

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "codearkt"))
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from codearkt.dispatch import SessionDispatcher  # noqa: E402
from codearkt.event_bus import EventType  # noqa: E402

import cc_runner  # noqa: E402
from cc_runner import (  # noqa: E402
    RunnerSession,
    SessionRegistry,
    _run_slot,
    session_position_event,
    session_queued_event,
    session_running_event,
)


def _make_session(session_id: str) -> RunnerSession:
    return RunnerSession(session_id=session_id, events=asyncio.Queue())


def _drain(queue: "asyncio.Queue") -> list:
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


async def _wait_until(pred: Callable[[], bool], timeout: float = 2.0) -> None:
    """Cooperatively yield until ``pred`` holds (or fail the test on timeout)."""
    deadline = asyncio.get_event_loop().time() + timeout
    while not pred():
        assert asyncio.get_event_loop().time() < deadline, "condition not reached in time"
        await asyncio.sleep(0.01)


# ── Wire payload shape (what the UI reducer parses) ──


def test_session_queued_event_shape_matches_wire_contract() -> None:
    ev = session_queued_event("sess-1", position=2, depth=3)
    assert ev.session_id == "sess-1"
    assert ev.agent_name == "manager"
    assert ev.event_type == EventType.SESSION_QUEUED
    assert json.loads(ev.content) == {"resource": "session", "position": 2, "depth": 3}


def test_session_running_event_shape_matches_wire_contract() -> None:
    ev = session_running_event("sess-1")
    assert ev.event_type == EventType.SESSION_RUNNING
    assert ev.agent_name == "manager"
    # session_running carries only the resource class (CodeArkt parity); the
    # reducer ignores position/depth on running and clears them.
    assert json.loads(ev.content) == {"resource": "session"}


def test_session_position_event_shape_matches_wire_contract() -> None:
    ev = session_position_event("sess-1", position=1, depth=2)
    assert ev.event_type == EventType.SESSION_POSITION
    assert json.loads(ev.content) == {"resource": "session", "position": 1, "depth": 2}


# ── The gate itself (SC6 core) ──


def test_first_run_at_limit_runs_immediately_without_queuing() -> None:
    # A run entering a free pool gets only session_running -- never a spurious
    # session_queued banner.
    async def body() -> None:
        dispatcher = SessionDispatcher(1)
        registry = SessionRegistry()
        session = _make_session("solo")
        registry.register(session)

        async with _run_slot(dispatcher, session, registry):
            events = _drain(session.events)
            assert len(events) == 1
            assert events[0].event_type == EventType.SESSION_RUNNING
            assert dispatcher.snapshot() == {"active": 1, "limit": 1, "queued": 0}

    asyncio.run(body())


def test_second_run_at_limit_1_queues_then_runs_on_release() -> None:
    # The heart of SC6: at CC_MAX_ACTIVE_SESSIONS=1 the 2nd run gets
    # session_queued {resource,position,depth} and blocks; once the 1st releases
    # its slot the 2nd gets session_running and proceeds.
    async def body() -> None:
        dispatcher = SessionDispatcher(1)
        registry = SessionRegistry()
        first = _make_session("first")
        second = _make_session("second")
        registry.register(first)
        registry.register(second)

        release_first = asyncio.Event()
        second_entered = asyncio.Event()

        async def hold_first() -> None:
            async with _run_slot(dispatcher, first, registry):
                await release_first.wait()

        async def run_second() -> None:
            async with _run_slot(dispatcher, second, registry):
                second_entered.set()

        first_task = asyncio.create_task(hold_first())
        # First holds the only slot (wait until its session_running actually
        # landed, not just until the counter ticked -- acquire fires the
        # callback after releasing its internal lock).
        await _wait_until(lambda: not first.events.empty())
        assert [e.event_type for e in _drain(first.events)] == [EventType.SESSION_RUNNING]

        second_task = asyncio.create_task(run_second())
        # Second cannot get a slot -> it is queued and got the banner.
        await _wait_until(lambda: not second.events.empty())
        assert dispatcher.snapshot()["queued"] == 1
        assert not second_entered.is_set()
        queued = _drain(second.events)
        assert len(queued) == 1
        assert queued[0].event_type == EventType.SESSION_QUEUED
        assert queued[0].session_id == "second"
        assert json.loads(queued[0].content) == {
            "resource": "session",
            "position": 1,
            "depth": 1,
        }

        # Free the first slot -> second acquires and runs.
        release_first.set()
        await asyncio.wait_for(first_task, timeout=2.0)
        await asyncio.wait_for(second_task, timeout=2.0)
        assert second_entered.is_set()
        # Second's stream then carried session_running (slot granted).
        assert EventType.SESSION_RUNNING in {e.event_type for e in _drain(second.events)}
        # Pool drained cleanly -- no phantom active/queued left behind.
        assert dispatcher.snapshot() == {"active": 0, "limit": 1, "queued": 0}

    asyncio.run(body())


def test_release_republishes_position_to_still_queued_runs() -> None:
    # With two runs queued behind a held slot, releasing it wakes the head of
    # the queue and re-publishes session_position (routed to the tail run's OWN
    # private queue, not the releaser's) so its banner counts down.
    async def body() -> None:
        dispatcher = SessionDispatcher(1)
        registry = SessionRegistry()
        head = _make_session("head")
        mid = _make_session("mid")
        tail = _make_session("tail")
        for s in (head, mid, tail):
            registry.register(s)

        release_head = asyncio.Event()
        mid_entered = asyncio.Event()

        async def hold_head() -> None:
            async with _run_slot(dispatcher, head, registry):
                await release_head.wait()

        async def run_mid() -> None:
            async with _run_slot(dispatcher, mid, registry):
                mid_entered.set()

        release_tail = asyncio.Event()

        async def run_tail() -> None:
            async with _run_slot(dispatcher, tail, registry):
                await release_tail.wait()

        head_task = asyncio.create_task(hold_head())
        await _wait_until(lambda: not head.events.empty())
        _drain(head.events)  # head's session_running

        mid_task = asyncio.create_task(run_mid())
        await _wait_until(lambda: not mid.events.empty())
        tail_task = asyncio.create_task(run_tail())
        await _wait_until(lambda: not tail.events.empty())
        assert dispatcher.snapshot()["queued"] == 2

        # tail's initial queued banner: position 2 of depth 2.
        tail_events = _drain(tail.events)
        assert len(tail_events) == 1
        assert json.loads(tail_events[0].content) == {
            "resource": "session",
            "position": 2,
            "depth": 2,
        }

        # Release head -> mid runs; tail gets a refreshed session_position on
        # its own queue.
        release_head.set()
        await asyncio.wait_for(head_task, timeout=2.0)
        await _wait_until(mid_entered.is_set)
        await _wait_until(lambda: not tail.events.empty())

        refreshed = _drain(tail.events)
        assert any(e.event_type == EventType.SESSION_POSITION for e in refreshed)
        assert all(e.session_id == "tail" for e in refreshed)

        # Let mid and tail finish cleanly.
        await asyncio.wait_for(mid_task, timeout=2.0)
        release_tail.set()
        await asyncio.wait_for(tail_task, timeout=2.0)
        assert dispatcher.snapshot() == {"active": 0, "limit": 1, "queued": 0}

    asyncio.run(body())


# ── SC6 invariant: CC runs never touch app:5055's GET /capacity ──


def test_runner_dispatcher_is_disjoint_from_codearkt_capacity() -> None:
    # The runner has its OWN admission pool, sized by CC_MAX_ACTIVE_SESSIONS,
    # and speaks the queue only over its own NDJSON stream. It exposes no
    # /capacity route, so a CC run can never appear in (or perturb) the
    # CodeArkt server's GET /capacity readout -- that stays a process-local
    # CodeArkt-only snapshot (SC6 invariant).
    assert isinstance(cc_runner.DISPATCHER, SessionDispatcher)
    assert cc_runner.DISPATCHER.limit == cc_runner.CC_MAX_ACTIVE_SESSIONS

    route_paths = {getattr(r, "path", None) for r in cc_runner.app.router.routes}
    assert "/capacity" not in route_paths
    # The endpoints the runner does expose are only the manager/cancel/health
    # trio -- nothing that reads or writes CodeArkt capacity.
    assert "/agents/manager" in route_paths
    assert "/agents/cancel" in route_paths
    assert "/health" in route_paths
