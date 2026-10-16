"""Title generation is isolated, optional and never invokes an LLM in tests."""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from holosophos import research_session
import session_titles


def test_ids_and_display_metadata_are_independent(tmp_path):
    ids = {research_session.new_session_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(s.startswith("research_") and s.isascii() for s in ids)
    work = tmp_path / next(iter(ids))
    work.mkdir()
    research_session.initialize_display_metadata(work, "Сравни\nэтанол и кислоту")
    assert research_session.read_marker(work, "title") == "Сравни этанол и кислоту"
    created = research_session.read_marker(work, "created_at")
    research_session.initialize_display_metadata(work, "Замени результат")
    assert research_session.read_marker(work, "title") == "Сравни этанол и кислоту"
    assert research_session.read_marker(work, "created_at") == created


def test_background_title_is_one_shot_and_has_no_research_context(
    tmp_path, monkeypatch
):
    import claude_agent_sdk
    import run_research

    calls = []
    release = None

    async def fake_query(*, prompt, options):
        calls.append((prompt, options))
        await release.wait()
        yield claude_agent_sdk.ResultMessage(
            subtype="success",
            duration_ms=0,
            duration_api_ms=0,
            is_error=False,
            num_turns=1,
            session_id="title-only",
            result="Сравнение молекулярных масс",
        )

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    monkeypatch.setenv("NODE_TLS_REJECT_UNAUTHORIZED", "0")
    monkeypatch.setattr(run_research, "resolve_cli_path", lambda: "/test/claude")

    async def scenario():
        nonlocal release
        release = asyncio.Event()
        research_session.initialize_display_metadata(tmp_path, "Сравни этанол")
        task = session_titles.start_title_task(tmp_path, "Сравни этанол")
        assert task is not None and not task.done()
        assert session_titles.start_title_task(tmp_path, "Сравни этанол") is None
        await asyncio.sleep(0)
        release.set()
        await task
        assert session_titles.start_title_task(tmp_path, "Доработай") is None

    asyncio.run(scenario())
    assert (
        research_session.read_marker(tmp_path, "title") == "Сравнение молекулярных масс"
    )
    prompt, options = calls[0]
    assert len(calls) == 1 and "Сравни этанол" in prompt
    assert options.model == "opus" and options.tools == []
    assert not options.mcp_servers and not options.agents
    assert options.setting_sources == [] and options.max_turns == 1
    assert options.env["ANTHROPIC_API_KEY"] == options.env["ANTHROPIC_AUTH_TOKEN"] == ""
    assert options.env["NODE_TLS_REJECT_UNAUTHORIZED"] == "1"
    assert options.resume is None and options.session_id is None
    assert "no-session-persistence" in options.extra_args
    assert "strict-mcp-config" in options.extra_args
    assert options.cwd != str(tmp_path)


@pytest.mark.parametrize("response", ["", "a\nb", "x" * 121])
def test_invalid_title_keeps_fallback_and_is_not_retried(
    tmp_path, monkeypatch, response
):
    async def bad_title(prompt, **kwargs):
        return response

    monkeypatch.setattr(session_titles, "request_title", bad_title)
    research_session.initialize_display_metadata(tmp_path, "Исходная задача")

    async def scenario():
        await session_titles.start_title_task(tmp_path, "Исходная задача")
        assert session_titles.start_title_task(tmp_path, "Исходная задача") is None

    asyncio.run(scenario())
    assert research_session.read_marker(tmp_path, "title") == "Исходная задача"


def test_title_failure_and_manual_edit_do_not_break_session(tmp_path, monkeypatch):
    async def fail(prompt, **kwargs):
        raise RuntimeError("unavailable")

    monkeypatch.setattr(session_titles, "request_title", fail)
    research_session.initialize_display_metadata(tmp_path, "Task")

    async def scenario():
        await session_titles.start_title_task(tmp_path, "Task")

    asyncio.run(scenario())
    assert research_session.read_marker(tmp_path, "title") == "Task"


def test_manual_title_wins_over_pending_generation(tmp_path, monkeypatch):
    async def title(prompt, **kwargs):
        (tmp_path / ".holosophus_ui/title").write_text("Моё название")
        return "Generated title"

    monkeypatch.setattr(session_titles, "request_title", title)
    research_session.initialize_display_metadata(tmp_path, "Task")
    asyncio.run(session_titles.generate_title_once(tmp_path, "Task"))
    assert research_session.read_marker(tmp_path, "title") == "Моё название"


def test_title_timeout_cancels_only_helper_and_keeps_fallback(tmp_path, monkeypatch):
    cancelled = []

    async def slow(prompt, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(session_titles, "request_title", slow)
    monkeypatch.setattr(session_titles, "TITLE_TIMEOUT_SECONDS", 0.01)
    research_session.initialize_display_metadata(tmp_path, "Task")
    asyncio.run(session_titles.generate_title_once(tmp_path, "Task"))
    assert cancelled == [True]
    assert research_session.read_marker(tmp_path, "title") == "Task"
    assert not research_session.title_is_pending(tmp_path)
    assert research_session.read_marker(tmp_path, "title_attempted") == "done"
