"""SC7 (ui-claude-code-orchestrator): Langfuse v3 trace parity.

The *live* half of SC7 — a real CC run producing a v3 trace at :3001 whose
``session_id`` matches the run and whose ``user`` matches ``X-Holo-Client`` — is
the Validation Plan's manual e2e check (no unit can stand in for a live trace).
This file locks the *deterministic* wire logic that decides what lands in the
trace, in ``scripts/cc_research_lib.py``:

- ``trace_run_context`` always sets ``session_id`` and sets ``user_id`` **only
  when the browser client is non-empty** — the exact ``ad3f449`` parity SC7
  asks for (header present -> ``user`` == value; absent -> ``user`` empty);
- ``init_langfuse_tracing`` with the v3 keys absent falls back to an untraced
  no-op instead of crashing the runner.

Plain functions + ``nullcontext`` fakes, so the suite needs no live Langfuse and
no pytest-asyncio config at the repo-root test home.
"""

import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Dict, List
from types import SimpleNamespace

import pytest

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "codearkt"))
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

import cc_research_lib  # noqa: E402


@pytest.fixture(autouse=True)
def tracing_env(monkeypatch):
    monkeypatch.setattr(cc_research_lib, "_TRACING_BRIDGE_ENABLED", False)
    monkeypatch.setenv("ENABLE_PHOENIX", "false")
    for key in ("LANGSMITH_TRACING", "LANGSMITH_OTEL_ENABLED", "LANGSMITH_OTEL_ONLY"):
        monkeypatch.setenv(key, "false")


class _RecordingPropagate:
    """Stand-in for ``langfuse.propagate_attributes`` that records its kwargs.

    Returns a ``nullcontext`` so it is usable in a ``with`` block, exactly like
    the real callable, while the test asserts on what the runner passed.
    """

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return nullcontext()


# ── trace_run_context: the SC7 session_id + user parity ──


def test_trace_context_always_sets_session_id() -> None:
    rec = _RecordingPropagate()
    with cc_research_lib.trace_run_context(rec, "sess-abc", user_id="alice"):
        pass
    assert rec.calls == [{"session_id": "sess-abc", "user_id": "alice"}]


def test_trace_context_sets_user_when_client_non_empty() -> None:
    rec = _RecordingPropagate()
    with cc_research_lib.trace_run_context(rec, "sess-1", user_id="bob@example.com"):
        pass
    (call,) = rec.calls
    assert call["session_id"] == "sess-1"
    assert call["user_id"] == "bob@example.com"


def test_trace_context_strips_user_whitespace() -> None:
    rec = _RecordingPropagate()
    with cc_research_lib.trace_run_context(rec, "sess-2", user_id="  carol  "):
        pass
    assert rec.calls == [{"session_id": "sess-2", "user_id": "carol"}]


def test_trace_context_omits_user_when_none() -> None:
    """No ``X-Holo-Client`` header -> user_id must not be sent (trace user empty)."""
    rec = _RecordingPropagate()
    with cc_research_lib.trace_run_context(rec, "sess-3", user_id=None):
        pass
    assert rec.calls == [{"session_id": "sess-3"}]
    assert "user_id" not in rec.calls[0]


def test_trace_context_omits_user_when_empty_or_whitespace() -> None:
    for empty in ("", "   ", "\t\n"):
        rec = _RecordingPropagate()
        with cc_research_lib.trace_run_context(rec, "sess-x", user_id=empty):
            pass
        assert rec.calls == [{"session_id": "sess-x"}], f"empty client {empty!r} leaked a user_id"


def test_trace_context_default_user_omits() -> None:
    """Default (no user_id arg) behaves like an absent header."""
    rec = _RecordingPropagate()
    with cc_research_lib.trace_run_context(rec, "sess-4"):
        pass
    assert rec.calls == [{"session_id": "sess-4"}]


# ── noop fallback + no-keys init: the runner must run untraced, not crash ──


def test_noop_propagate_is_usable_context_manager() -> None:
    with cc_research_lib.noop_propagate_attributes(session_id="s", user_id="u"):
        pass  # entering/exiting must not raise, ignores all kwargs


def test_init_without_v3_keys_falls_back_to_noop(monkeypatch: Any) -> None:
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY_V3", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY_V3", raising=False)

    client, propagate = cc_research_lib.init_langfuse_tracing()

    assert client is None
    # The exact no-op is returned (identity), so no live Langfuse is contacted.
    assert propagate is cc_research_lib.noop_propagate_attributes
    # And it is still a working trace context (untraced run proceeds cleanly).
    with cc_research_lib.trace_run_context(propagate, "sess-untraced", user_id="dave"):
        pass


def test_init_with_only_public_key_falls_back_to_noop(monkeypatch: Any) -> None:
    """A half-configured stack (public key but no secret) must not go live."""
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY_V3", "pk-lf-xxxx")
    monkeypatch.delenv("LANGFUSE_SECRET_KEY_V3", raising=False)

    client, propagate = cc_research_lib.init_langfuse_tracing()

    assert client is None
    assert propagate is cc_research_lib.noop_propagate_attributes


@pytest.mark.parametrize("langfuse_configured", [False, True])
def test_phoenix_keeps_bridge_enabled_without_working_langfuse(
    monkeypatch, langfuse_configured
):
    """An absent/unavailable Langfuse must not disable the Phoenix trace path."""
    monkeypatch.setenv("ENABLE_PHOENIX", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY_V3", "pk-offline" if langfuse_configured else "")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY_V3", "sk-offline" if langfuse_configured else "")
    propagation = _RecordingPropagate()
    configured = []
    # Only external sink setup and SDK class patching are replaced; exercise
    # the real initialization decision and returned per-run context.
    monkeypatch.setitem(sys.modules, "phoenix_tracing", SimpleNamespace(
        configure_phoenix_export=lambda: True,
        phoenix_trace_context=lambda **kwargs: nullcontext(),
    ))
    monkeypatch.setitem(sys.modules, "langfuse", SimpleNamespace(
        get_client=lambda: SimpleNamespace(auth_check=lambda: False),
        propagate_attributes=propagation,
    ))
    monkeypatch.setitem(sys.modules, "langsmith_fork", SimpleNamespace(
        configure_claude_agent_sdk=lambda: configured.append(True),
    ))
    client, propagate = cc_research_lib.init_langfuse_tracing()
    assert client is None
    assert configured == [True]
    with cc_research_lib.trace_run_context(propagate, "phoenix-only", user_id="alice"):
        pass
    assert propagation.calls == [{"session_id": "phoenix-only", "user_id": "alice"}]


def test_phoenix_setup_failure_does_not_disable_langfuse(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY_V3", "pk-offline")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY_V3", "sk-offline")
    client = SimpleNamespace(auth_check=lambda: True)
    propagate = _RecordingPropagate()
    configured = []

    def failing_phoenix():
        raise RuntimeError("collector configuration rejected")

    monkeypatch.setitem(sys.modules, "phoenix_tracing", SimpleNamespace(
        configure_phoenix_export=failing_phoenix,
    ))
    monkeypatch.setitem(sys.modules, "langfuse", SimpleNamespace(
        get_client=lambda: client, propagate_attributes=propagate,
    ))
    monkeypatch.setitem(sys.modules, "langsmith_fork", SimpleNamespace(
        configure_claude_agent_sdk=lambda: configured.append(True),
    ))
    actual, context = cc_research_lib.init_langfuse_tracing()
    assert actual is client
    assert context is propagate
    assert configured == [True]


@pytest.mark.parametrize("langfuse_fails", [False, True])
def test_flush_drains_bridge_before_sinks_and_keeps_phoenix_on_failure(
    monkeypatch, langfuse_fails
):
    pending = ["last span"]
    exported = []
    order = []

    def drain(*, timeout):
        assert timeout > 0
        order.append("bridge")
        exported.extend(pending)
        pending.clear()

    def flush_langfuse():
        assert not pending
        order.append("langfuse")
        if langfuse_fails:
            raise ConnectionError("unavailable")

    def flush_phoenix():
        assert exported == ["last span"]
        order.append("phoenix")
        return True

    monkeypatch.setattr(cc_research_lib, "_TRACING_BRIDGE_ENABLED", True, raising=False)
    monkeypatch.setitem(sys.modules, "langsmith.run_trees", SimpleNamespace(
        get_cached_client=lambda: SimpleNamespace(flush=drain),
    ))
    monkeypatch.setitem(sys.modules, "phoenix_tracing", SimpleNamespace(
        flush_phoenix_export=flush_phoenix,
    ))
    result = cc_research_lib.flush_tracing(SimpleNamespace(flush=flush_langfuse))
    assert order == ["bridge", "langfuse", "phoenix"]
    assert result is (not langfuse_fails)


def test_flush_without_tracing_does_not_create_langsmith_client(monkeypatch):
    monkeypatch.setattr(cc_research_lib, "_TRACING_BRIDGE_ENABLED", False, raising=False)

    def unexpected_client():
        pytest.fail("Disabled tracing must not create a LangSmith client")

    monkeypatch.setitem(sys.modules, "langsmith.run_trees", SimpleNamespace(
        get_cached_client=unexpected_client,
    ))
    assert cc_research_lib.flush_tracing() is True
