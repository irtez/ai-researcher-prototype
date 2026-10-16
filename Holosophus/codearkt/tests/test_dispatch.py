import asyncio
import json
from pathlib import Path
from typing import Any, List, Tuple

import pytest
from httpx import ASGITransport, AsyncClient

from codearkt.codeact import CodeActAgent
from codearkt.dispatch import (
    SessionDispatcher,
    host_metrics,
    mem_used_pct,
    mle_kit_resources,
)
from codearkt.event_bus import AgentEventBus, EventType
from codearkt.llm import LLM
from codearkt.server import get_main_app
from codearkt.settings import settings
from codearkt import dispatch as dispatch_module


async def test_dispatcher_admits_up_to_limit_without_queueing() -> None:
    dispatcher = SessionDispatcher(limit=2)
    queued_events: List[Tuple[str, int, int]] = []

    async def on_queued(session_id: str, position: int, depth: int) -> None:
        queued_events.append((session_id, position, depth))

    await dispatcher.acquire("a", on_queued=on_queued)
    await dispatcher.acquire("b", on_queued=on_queued)

    assert dispatcher.snapshot() == {"active": 2, "limit": 2, "queued": 0}
    assert queued_events == []


async def test_dispatcher_queues_beyond_limit_with_position() -> None:
    dispatcher = SessionDispatcher(limit=2)
    queued_events: List[Tuple[str, int, int]] = []
    running_events: List[str] = []

    async def on_queued(session_id: str, position: int, depth: int) -> None:
        queued_events.append((session_id, position, depth))

    async def on_running(session_id: str) -> None:
        running_events.append(session_id)

    await dispatcher.acquire("a", on_queued=on_queued, on_running=on_running)
    await dispatcher.acquire("b", on_queued=on_queued, on_running=on_running)

    c_task = asyncio.create_task(
        dispatcher.acquire("c", on_queued=on_queued, on_running=on_running)
    )
    # Let the event loop run "c" up to (and blocking on) the semaphore.
    for _ in range(5):
        await asyncio.sleep(0)

    assert dispatcher.snapshot() == {"active": 2, "limit": 2, "queued": 1}
    assert queued_events == [("c", 1, 1)]
    # "a" and "b" never queued, but on_running still fires for every grant.
    assert running_events == ["a", "b"]

    # Freeing a slot should wake "c" and hand it the slot.
    await dispatcher.release("a")
    await c_task

    assert dispatcher.snapshot() == {"active": 2, "limit": 2, "queued": 0}
    assert running_events == ["a", "b", "c"]

    await dispatcher.release("b")
    await dispatcher.release("c")
    assert dispatcher.snapshot() == {"active": 0, "limit": 2, "queued": 0}


async def test_dispatcher_release_republishes_positions_to_remaining_queue() -> None:
    dispatcher = SessionDispatcher(limit=1)
    position_events: List[Tuple[str, int, int]] = []

    async def on_position(session_id: str, position: int, depth: int) -> None:
        position_events.append((session_id, position, depth))

    await dispatcher.acquire("a")
    b_task = asyncio.create_task(dispatcher.acquire("b"))
    c_task = asyncio.create_task(dispatcher.acquire("c"))
    for _ in range(5):
        await asyncio.sleep(0)

    assert dispatcher.snapshot() == {"active": 1, "limit": 1, "queued": 2}

    await dispatcher.release("a", on_position=on_position)
    await b_task

    # Re-published against the queue as it stood at release time (both "b"
    # and "c" were still nominally queued then) -- best-effort UX polish;
    # correctness/ordering is guaranteed by the semaphore itself, not this.
    assert position_events == [("b", 1, 2), ("c", 2, 2)]

    await dispatcher.release("b")
    await c_task
    assert dispatcher.snapshot() == {"active": 1, "limit": 1, "queued": 0}
    await dispatcher.release("c")
    assert dispatcher.snapshot() == {"active": 0, "limit": 1, "queued": 0}


async def test_dispatcher_cancel_while_queued_drops_bookkeeping() -> None:
    dispatcher = SessionDispatcher(limit=1)
    await dispatcher.acquire("a")

    b_task = asyncio.create_task(dispatcher.acquire("b"))
    for _ in range(5):
        await asyncio.sleep(0)
    assert dispatcher.snapshot() == {"active": 1, "limit": 1, "queued": 1}

    b_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await b_task

    # Cancelling a queued waiter must not leak a phantom queue entry.
    assert dispatcher.snapshot() == {"active": 1, "limit": 1, "queued": 0}
    await dispatcher.release("a")
    assert dispatcher.snapshot() == {"active": 0, "limit": 1, "queued": 0}


def test_host_metrics_shape_and_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    metrics = host_metrics()
    assert set(metrics.keys()) == {"load1", "load5", "load15", "cpu_count", "mem_used_pct"}
    assert isinstance(metrics["load1"], float)
    assert isinstance(metrics["load5"], float)
    assert isinstance(metrics["load15"], float)
    assert isinstance(metrics["cpu_count"], int)
    assert metrics["mem_used_pct"] is None or isinstance(metrics["mem_used_pct"], float)


def test_mem_used_pct_none_when_meminfo_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(dispatch_module, "MEMINFO_PATH", str(tmp_path / "no-such-meminfo"))
    assert mem_used_pct() is None


def test_mem_used_pct_reads_fixture_meminfo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       1000000 kB\nMemAvailable:    250000 kB\n")
    monkeypatch.setattr(dispatch_module, "MEMINFO_PATH", str(meminfo))
    assert mem_used_pct() == 75.0


def test_mem_used_pct_malformed_file_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("not a real meminfo file\n")
    monkeypatch.setattr(dispatch_module, "MEMINFO_PATH", str(meminfo))
    assert mem_used_pct() is None


async def test_capacity_endpoint_reflects_dispatcher_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_ainvoke(self: CodeActAgent, *args: Any, **kwargs: Any) -> str:
        return "ok"

    monkeypatch.setattr(CodeActAgent, "ainvoke", fake_ainvoke)

    agent = CodeActAgent(name="root", description="root", llm=LLM("gpt-4o"))
    app = get_main_app(
        agent,
        event_bus=AgentEventBus(),
        mcp_config=None,
        server_host=settings.DEFAULT_SERVER_HOST,
        server_port=0,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/capacity")
        assert resp.status_code == 200
        body = resp.json()

        assert body["sessions"] == {
            "active": 0,
            "limit": settings.MAX_ACTIVE_SESSIONS,
            "queued": 0,
        }
        assert set(body["host"].keys()) == {
            "load1",
            "load5",
            "load15",
            "cpu_count",
            "mem_used_pct",
        }
        # SC5/SC6: the two resource-class queues are surfaced from mle_kit.
        for resource_class in ("gpu", "local_exec"):
            assert set(body[resource_class].keys()) == {"busy", "waiting", "slots"}


def test_mle_kit_resources_missing_file_returns_zeros(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    assert mle_kit_resources() == {
        "gpu": {"busy": 0, "waiting": 0, "slots": 0},
        "local_exec": {"busy": 0, "waiting": 0, "slots": 0},
    }


def test_mle_kit_resources_reads_status_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    status_dir = tmp_path / ".holosophus_capacity"
    status_dir.mkdir()
    (status_dir / "mle_kit.json").write_text(
        '{"gpu": {"busy": 1, "waiting": 2, "slots": 1},'
        ' "local_exec": {"busy": 3, "waiting": 0, "slots": 4}}',
        encoding="utf-8",
    )
    resources = mle_kit_resources()
    assert resources["gpu"] == {"busy": 1, "waiting": 2, "slots": 1}
    assert resources["local_exec"] == {"busy": 3, "waiting": 0, "slots": 4}


def test_mle_kit_resources_malformed_file_returns_zeros(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    status_dir = tmp_path / ".holosophus_capacity"
    status_dir.mkdir()
    (status_dir / "mle_kit.json").write_text("{ not json", encoding="utf-8")
    assert mle_kit_resources()["gpu"]["busy"] == 0


async def test_gpu_admission_second_gpu_run_queues_on_gpu_resource(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """SC5 ([B]/[C]): a 2nd compute_mode=GPU root run queues with a visible
    `resource="gpu"` position while the 1st holds the (single) GPU slot for
    its whole run -- and `/capacity`'s `gpu` reflects the app-level GPU
    dispatcher, not mle_kit's box-level lease.
    """
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "GPU_SLOTS", 1)

    hold_first = asyncio.Event()
    started_first = asyncio.Event()

    async def fake_ainvoke(self: CodeActAgent, *args: Any, session_id: str, **kwargs: Any) -> str:
        if session_id == "gpu-first":
            started_first.set()
            await hold_first.wait()
        return "ok"

    monkeypatch.setattr(CodeActAgent, "ainvoke", fake_ainvoke)

    agent = CodeActAgent(name="root", description="root", llm=LLM("gpt-4o"))
    event_bus = AgentEventBus()
    app = get_main_app(
        agent,
        event_bus=event_bus,
        mcp_config=None,
        server_host=settings.DEFAULT_SERVER_HOST,
        server_port=0,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:

        def _post(session_id: str) -> "asyncio.Task[Any]":
            return asyncio.create_task(
                client.post(
                    "/agents/root",
                    json={
                        "messages": [{"role": "user", "content": "hi"}],
                        "session_id": session_id,
                        "stream": False,
                        "compute_mode": "GPU",
                    },
                )
            )

        first_task = _post("gpu-first")
        await asyncio.wait_for(started_first.wait(), timeout=5)

        resp = await client.get("/capacity")
        assert resp.json()["gpu"] == {"busy": 1, "waiting": 0, "slots": 1}

        second_task = _post("gpu-second")

        # The 2nd GPU run must be gated on the *gpu* resource specifically
        # (not the session dispatcher, which still has free slots) -- assert
        # against the actual event published on the bus.
        queue = event_bus.queues.setdefault("gpu-second", asyncio.Queue())
        event = await asyncio.wait_for(queue.get(), timeout=5)
        assert event.event_type == EventType.SESSION_QUEUED
        payload = json.loads(event.content or "{}")
        assert payload == {"resource": "gpu", "position": 1, "depth": 1}

        resp = await client.get("/capacity")
        assert resp.json()["gpu"] == {"busy": 1, "waiting": 1, "slots": 1}

        hold_first.set()
        first_resp = await asyncio.wait_for(first_task, timeout=5)
        assert first_resp.status_code == 200

        second_resp = await asyncio.wait_for(second_task, timeout=5)
        assert second_resp.status_code == 200

        resp = await client.get("/capacity")
        assert resp.json()["gpu"] == {"busy": 0, "waiting": 0, "slots": 1}


async def test_capacity_prefers_mle_kit_gpu_snapshot_once_multi_card_discovered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A shared multi-card host (e.g. Selectel) lifts mle_kit's own `gpu.slots`
    above the static single-box default once live discovery runs -- at that
    point `/capacity` must reflect mle_kit's per-session registry snapshot
    (the real ground truth), not the app-level `gpu_dispatcher`'s static
    {"busy": 0, "waiting": 0, "slots": settings.GPU_SLOTS}, even with no
    GPU-mode run in flight at the app layer.
    """
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "GPU_SLOTS", 1)

    status_dir = tmp_path / ".holosophus_capacity"
    status_dir.mkdir()
    (status_dir / "mle_kit.json").write_text(
        json.dumps(
            {
                "gpu": {"busy": 3, "waiting": 1, "slots": 8},
                "local_exec": {"busy": 0, "waiting": 0, "slots": 1},
            }
        ),
        encoding="utf-8",
    )

    async def fake_ainvoke(self: CodeActAgent, *args: Any, **kwargs: Any) -> str:
        return "ok"

    monkeypatch.setattr(CodeActAgent, "ainvoke", fake_ainvoke)

    agent = CodeActAgent(name="root", description="root", llm=LLM("gpt-4o"))
    app = get_main_app(
        agent,
        event_bus=AgentEventBus(),
        mcp_config=None,
        server_host=settings.DEFAULT_SERVER_HOST,
        server_port=0,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/capacity")
        assert resp.status_code == 200
        assert resp.json()["gpu"] == {"busy": 3, "waiting": 1, "slots": 8}


async def test_capacity_falls_back_to_gpu_dispatcher_when_mle_kit_slots_not_above_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When mle_kit's `gpu.slots` is <= 1 -- either because the status file is
    absent, or because it reports the single-box default -- `/capacity` must
    keep sourcing `gpu` from the app-level `gpu_dispatcher`'s live snapshot
    while a GPU-mode run is in flight, not from mle_kit's file.
    """
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "GPU_SLOTS", 1)

    # No mle_kit.json written at all -- absent-file case.
    hold_first = asyncio.Event()
    started_first = asyncio.Event()

    async def fake_ainvoke(self: CodeActAgent, *args: Any, session_id: str, **kwargs: Any) -> str:
        if session_id == "gpu-first":
            started_first.set()
            await hold_first.wait()
        return "ok"

    monkeypatch.setattr(CodeActAgent, "ainvoke", fake_ainvoke)

    agent = CodeActAgent(name="root", description="root", llm=LLM("gpt-4o"))
    app = get_main_app(
        agent,
        event_bus=AgentEventBus(),
        mcp_config=None,
        server_host=settings.DEFAULT_SERVER_HOST,
        server_port=0,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first_task = asyncio.create_task(
            client.post(
                "/agents/root",
                json={
                    "messages": [{"role": "user", "content": "hi"}],
                    "session_id": "gpu-first",
                    "stream": False,
                    "compute_mode": "GPU",
                },
            )
        )
        await asyncio.wait_for(started_first.wait(), timeout=5)

        resp = await client.get("/capacity")
        assert resp.json()["gpu"] == {"busy": 1, "waiting": 0, "slots": 1}

        # Now write an mle_kit.json that reports slots == 1 (single-box
        # provider, not yet/never discovery-lifted) -- must still fall back.
        status_dir = tmp_path / ".holosophus_capacity"
        status_dir.mkdir()
        (status_dir / "mle_kit.json").write_text(
            json.dumps({"gpu": {"busy": 0, "waiting": 0, "slots": 1}, "local_exec": {}}),
            encoding="utf-8",
        )

        resp = await client.get("/capacity")
        assert resp.json()["gpu"] == {"busy": 1, "waiting": 0, "slots": 1}

        hold_first.set()
        first_resp = await asyncio.wait_for(first_task, timeout=5)
        assert first_resp.status_code == 200


async def test_gpu_admission_second_gpu_run_does_not_queue_once_multi_card_discovered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Boundary fix: once mle_kit reports live multi-card discovery (`gpu.slots
    > 1`, e.g. Selectel), the app-level `gpu_dispatcher` -- a static
    single-box semaphore that is never reconciled with mle_kit's dynamic slot
    count -- must NOT gate a 2nd concurrent GPU-mode root run behind the 1st.
    Before the fix this reproduced the bug from the review: a 2nd GPU session
    provably blocked on the app's single admission slot while `/capacity`
    reported 7 free `gpu` slots and zero queueing the whole time.
    """
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "GPU_SLOTS", 1)

    status_dir = tmp_path / ".holosophus_capacity"
    status_dir.mkdir()
    (status_dir / "mle_kit.json").write_text(
        json.dumps(
            {
                "gpu": {"busy": 1, "waiting": 0, "slots": 8},
                "local_exec": {"busy": 0, "waiting": 0, "slots": 1},
            }
        ),
        encoding="utf-8",
    )

    hold_first = asyncio.Event()
    started_first = asyncio.Event()

    async def fake_ainvoke(self: CodeActAgent, *args: Any, session_id: str, **kwargs: Any) -> str:
        if session_id == "gpu-first":
            started_first.set()
            await hold_first.wait()
        return "ok"

    monkeypatch.setattr(CodeActAgent, "ainvoke", fake_ainvoke)

    agent = CodeActAgent(name="root", description="root", llm=LLM("gpt-4o"))
    event_bus = AgentEventBus()
    app = get_main_app(
        agent,
        event_bus=event_bus,
        mcp_config=None,
        server_host=settings.DEFAULT_SERVER_HOST,
        server_port=0,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:

        def _post(session_id: str) -> "asyncio.Task[Any]":
            return asyncio.create_task(
                client.post(
                    "/agents/root",
                    json={
                        "messages": [{"role": "user", "content": "hi"}],
                        "session_id": session_id,
                        "stream": False,
                        "compute_mode": "GPU",
                    },
                )
            )

        first_task = _post("gpu-first")
        await asyncio.wait_for(started_first.wait(), timeout=5)

        resp = await client.get("/capacity")
        assert resp.json()["gpu"] == {"busy": 1, "waiting": 0, "slots": 8}

        queue = event_bus.queues.setdefault("gpu-second", asyncio.Queue())
        second_task = _post("gpu-second")

        # The 2nd GPU run must complete WITHOUT waiting on `hold_first` -- if
        # the app-level gpu_dispatcher (still capped at 1) wrongly gated it,
        # this would hang until `hold_first.set()` below and time out here.
        second_resp = await asyncio.wait_for(second_task, timeout=5)
        assert second_resp.status_code == 200

        # It must have been reported as running immediately, not queued (a
        # SESSION_QUEUED/gpu event here would mean the app-level dispatcher
        # -- still capped at 1 -- was consulted after all).
        event = await asyncio.wait_for(queue.get(), timeout=5)
        assert event.event_type == EventType.SESSION_RUNNING
        payload = json.loads(event.content or "{}")
        assert payload == {"resource": "gpu"}

        # /capacity still reflects mle_kit's (unchanged) snapshot throughout.
        resp = await client.get("/capacity")
        assert resp.json()["gpu"] == {"busy": 1, "waiting": 0, "slots": 8}

        hold_first.set()
        first_resp = await asyncio.wait_for(first_task, timeout=5)
        assert first_resp.status_code == 200


async def test_gpu_admission_non_gpu_run_does_not_touch_gpu_slot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A compute_mode=Local (non-GPU) root run must never consume a GPU slot."""
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "GPU_SLOTS", 1)

    hold_local = asyncio.Event()
    started_local = asyncio.Event()

    async def fake_ainvoke(self: CodeActAgent, *args: Any, session_id: str, **kwargs: Any) -> str:
        if session_id == "local-run":
            started_local.set()
            await hold_local.wait()
        return "ok"

    monkeypatch.setattr(CodeActAgent, "ainvoke", fake_ainvoke)

    agent = CodeActAgent(name="root", description="root", llm=LLM("gpt-4o"))
    app = get_main_app(
        agent,
        event_bus=AgentEventBus(),
        mcp_config=None,
        server_host=settings.DEFAULT_SERVER_HOST,
        server_port=0,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        local_task = asyncio.create_task(
            client.post(
                "/agents/root",
                json={
                    "messages": [{"role": "user", "content": "hi"}],
                    "session_id": "local-run",
                    "stream": False,
                    "compute_mode": "Local",
                },
            )
        )
        await asyncio.wait_for(started_local.wait(), timeout=5)

        resp = await client.get("/capacity")
        assert resp.json()["gpu"] == {"busy": 0, "waiting": 0, "slots": 1}

        hold_local.set()
        local_resp = await asyncio.wait_for(local_task, timeout=5)
        assert local_resp.status_code == 200

        resp = await client.get("/capacity")
        assert resp.json()["gpu"] == {"busy": 0, "waiting": 0, "slots": 1}
