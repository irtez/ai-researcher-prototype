"""An HTTP timeout must not leave the smoke's detached runner task alive."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import smoke_test_cc_local as smoke


def test_http_timeout_stops_its_runner_after_registry_removal(monkeypatch, tmp_path):
    class Registry:
        def __init__(self):
            self.sessions = {}

        def register(self, session):
            self.sessions[session.session_id] = session

        def get(self, session_id):
            return self.sessions.get(session_id)

        def remove(self, session_id):
            self.sessions.pop(session_id, None)

    started = None
    session = None

    async def run_manager(session, *args, **kwargs):
        started.set()
        await asyncio.Event().wait()

    async def terminate_run(session, interrupt_timeout):
        session.task.cancel()
        await asyncio.gather(session.task, return_exceptions=True)

    async def cancel_session(registry, session_id, **kwargs):
        target = registry.get(session_id)
        if target and not target.finished and not target.completed:
            await terminate_run(target, kwargs.get("interrupt_timeout"))

    runner = SimpleNamespace(
        REGISTRY=Registry(),
        _run_manager=run_manager,
        _terminate_run=terminate_run,
        cancel_session=cancel_session,
        _build_sdk_options=lambda *args, **kwargs: None,
        _workspace_root=lambda: tmp_path / "workdir",
        cc_research_lib=SimpleNamespace(map_sdk_message_to_events=lambda *args: []),
        CC_NO_AUTO_WAIT=False,
        create_app=lambda: object(),
    )

    class TimeoutClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, path, *, json, **kwargs):
            nonlocal session
            session = SimpleNamespace(
                session_id=json["session_id"],
                task=None,
                client=None,
                finished=False,
                completed=False,
                interrupted=False,
                events=asyncio.Queue(),
            )
            runner.REGISTRY.register(session)
            session.task = asyncio.create_task(runner._run_manager(session))
            await started.wait()
            # StreamingResponse closes its iterator on HTTP cancellation; the
            # iterator removes the registry entry, but does not own this task.
            session.finished = True
            runner.REGISTRY.remove(session.session_id)
            raise TimeoutError("HTTP request timed out")

    monkeypatch.setitem(sys.modules, "cc_runner", runner)
    monkeypatch.setitem(
        sys.modules,
        "httpx",
        SimpleNamespace(
            ASGITransport=lambda **kwargs: object(),
            AsyncClient=TimeoutClient,
        ),
    )
    monkeypatch.setattr(smoke, "PROJECT_DIR", tmp_path)

    async def scenario():
        nonlocal started
        started = asyncio.Event()
        unrelated = asyncio.create_task(asyncio.Event().wait())
        try:
            with pytest.raises(TimeoutError):
                await smoke.run_smoke("timeout-smoke", "test-client")
            assert session.task.cancelled(), "HTTP timeout left the smoke runner alive"
            assert not unrelated.done(), "Smoke cleanup cancelled an unrelated task"
        finally:
            tasks = [unrelated] + ([session.task] if session else [])
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(scenario())
