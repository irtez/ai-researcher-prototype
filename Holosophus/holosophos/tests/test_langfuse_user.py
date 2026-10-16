"""The authenticated login must surface as the Langfuse trace `user`.

Exercises the real `_update_trace_snapshot` (not a reimplementation) with a fake
Langfuse client, asserting it reads the per-session owner registry and stamps it
as `user_id` -- and leaves `user_id` None for unauthenticated runs (backward
compatible with the pre-auth behaviour).
"""

from typing import Any

from codearkt.run_config import clear_owner, set_owner
from holosophos.langfuse_openai_patch import _update_trace_snapshot


class _FakeTrace:
    def __init__(self, sink: dict[str, Any]) -> None:
        self._sink = sink

    def update(self, **kwargs: Any) -> None:
        self._sink["update_kwargs"] = kwargs


class _FakeLangfuseClient:
    def __init__(self) -> None:
        self.sink: dict[str, Any] = {}

    def trace(self, **kwargs: Any) -> _FakeTrace:
        self.sink["create_kwargs"] = kwargs
        return _FakeTrace(self.sink)


def test_update_trace_snapshot_stamps_owner_as_user_id() -> None:
    client = _FakeLangfuseClient()
    session_id = "sess-owner-test"
    set_owner(session_id, "alice")
    try:
        _update_trace_snapshot(
            client,
            "trace-1",
            agent_name="manager",
            langfuse_session_id="lf-1",
            workspace_session_id=session_id,
        )
    finally:
        clear_owner(session_id)
    assert client.sink["update_kwargs"]["user_id"] == "alice"


def test_update_trace_snapshot_user_id_none_when_unauthenticated() -> None:
    client = _FakeLangfuseClient()
    session_id = "sess-anon-test"
    clear_owner(session_id)  # ensure no owner registered
    _update_trace_snapshot(
        client,
        "trace-2",
        agent_name="manager",
        langfuse_session_id="lf-2",
        workspace_session_id=session_id,
    )
    assert client.sink["update_kwargs"]["user_id"] is None
