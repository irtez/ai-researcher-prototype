"""The document is a model-written artifact, separate from the closing message."""

import asyncio
from contextlib import asynccontextmanager
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "holosophos"))
from holosophos import research_iterations as history
from result_contract import ResultContract, RESULT_PROMPT


@pytest.fixture(autouse=True)
def no_title_llm(monkeypatch):
    import session_titles
    import docker_bash
    monkeypatch.setattr(session_titles, "start_title_task", lambda *args: None)
    @asynccontextmanager
    async def executor(workdir):
        yield SimpleNamespace(env={"CLAUDE_CODE_SHELL_PREFIX": "/fixture/docker-bash"})
    monkeypatch.setattr(docker_bash, "executor_session", executor)


def result(text="Done", *, error=False):
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=error,
        num_turns=1,
        session_id="sdk",
        result=text,
    )


def contract(work):
    item = history.start_iteration(
        work, kind="initial", user_message="Task", settings={}
    )
    return ResultContract(work, item["id"])


def test_document_bytes_are_preserved_and_final_message_is_separate(tmp_path):
    run = contract(tmp_path)
    document = "# Table\n\n| mass |\n|---|\n| 46.07 |\n\nUnchanged conclusion.\n"
    (tmp_path / run.output_path).write_text(document)
    run.begin_turn()
    run.observe(result("Rounding updated"))
    run.end_turn()
    data = run.validated_result()
    history.finish_iteration(
        tmp_path,
        run.iteration_id,
        status="complete",
        answer=run.answer,
        result_path=run.output_path,
        result_bytes=data,
    )
    assert (
        history.read_iteration(tmp_path, run.iteration_id)["result_content"] == document
    )
    assert (
        history.read_iteration(tmp_path, run.iteration_id)["answer"]
        == "Rounding updated"
    )
    assert not (tmp_path / "result.md").exists()


@pytest.mark.parametrize("bad", [None, b" \n", b"\xff"])
def test_one_persistent_reminder_then_explicit_missing_result(tmp_path, bad):
    run = contract(tmp_path)
    (tmp_path / "result.md").write_text("Old result cannot satisfy this iteration")
    if bad is not None:
        (tmp_path / run.output_path).write_bytes(bad)
    hook = {"hook_event_name": "Stop", "stop_hook_active": False}
    first = asyncio.run(run.stop_hook(hook))
    assert first["decision"] == "block" and run.output_path in first["reason"]
    resumed = ResultContract(tmp_path, run.iteration_id)
    assert asyncio.run(resumed.stop_hook(hook)) == {}
    resumed.begin_turn()
    resumed.observe(result())
    resumed.end_turn()
    with pytest.raises(ValueError, match="Result document"):
        resumed.validated_result()


def test_reminder_does_not_run_for_operator_pause_or_nested_stop(tmp_path):
    run = contract(tmp_path)
    run.defer_completion = True
    assert asyncio.run(run.stop_hook({"hook_event_name": "Stop"})) == {}
    run.defer_completion = False
    assert asyncio.run(run.stop_hook({"hook_event_name": "SubagentStop"})) == {}
    assert (
        asyncio.run(
            run.stop_hook({"hook_event_name": "Stop", "stop_hook_active": True})
        )
        == {}
    )
    assert (
        asyncio.run(run.stop_hook({"hook_event_name": "Stop"}))["decision"] == "block"
    )


def test_last_turn_must_finish_even_after_previous_success(tmp_path):
    run = contract(tmp_path)
    (tmp_path / run.output_path).write_text("Draft")
    run.begin_turn()
    run.observe(result())
    run.end_turn()
    run.begin_turn()  # A new query after pause/steer cannot inherit success.
    run.end_turn()
    with pytest.raises(ValueError, match="final SDK response"):
        run.validated_result()


def test_final_dedup_is_local_to_current_manager_turn(tmp_path):
    run = contract(tmp_path)
    run.begin_turn()
    msg = AssistantMessage(content=[TextBlock(text="Done")], model="fixture")
    assert run.observe(msg)
    assert not run.observe(result())
    run.begin_turn()
    assert run.observe(result())  # Same text in a later turn remains visible.


def test_feedback_context_uses_last_successful_snapshot(tmp_path):
    run = contract(tmp_path)
    (tmp_path / run.output_path).write_text("Previous")
    history.finish_iteration(
        tmp_path, run.iteration_id, status="complete", result_path=run.output_path
    )
    failed = history.start_iteration(
        tmp_path, kind="feedback", user_message="Failed", settings={}
    )
    history.finish_iteration(tmp_path, failed["id"], status="error")
    item = history.start_iteration(
        tmp_path, kind="feedback", user_message="Round", settings={}
    )
    next_run = ResultContract(tmp_path, item["id"])
    query = next_run.query("Round", mode="feedback")
    assert "PREVIOUS_RESULT_PATH: .holosophus_ui/iterations/0001/result.md" in query
    assert "PREVIOUS_RESULT_SOURCE_PATH: results/0001/result.md" in query
    assert "OUTPUT_PATH: results/0003/result.md" in query
    assert "initial task" not in query
    assert "0003" not in RESULT_PROMPT


def test_snapshot_uses_validated_bytes_even_if_work_file_changes(tmp_path):
    run = contract(tmp_path)
    path = tmp_path / run.output_path
    path.write_text("Validated")
    run.begin_turn()
    run.observe(result())
    run.end_turn()
    data = run.validated_result()
    path.write_text("Changed later")
    history.finish_iteration(
        tmp_path,
        run.iteration_id,
        status="complete",
        result_path=run.output_path,
        result_bytes=data,
    )
    assert (
        history.read_iteration(tmp_path, run.iteration_id)["result_content"]
        == "Validated"
    )


def test_result_target_symlink_is_rejected(tmp_path):
    run = contract(tmp_path)
    outside = tmp_path / "other.md"
    outside.write_text("Previous file")
    (tmp_path / run.output_path).symlink_to(outside)
    run.begin_turn()
    run.observe(result())
    run.end_turn()
    with pytest.raises(ValueError, match="Result document"):
        run.validated_result()


@pytest.mark.parametrize("save_document", [True, False])
def test_cli_main_uses_same_contract_and_publishes_only_document(
    tmp_path, monkeypatch, save_document
):
    import re
    import claude_agent_sdk
    import run_research as rr
    import cc_research_lib
    import sync_claude_agents

    prompt = rr.load_manager_prompt(include_hitl=False)
    monkeypatch.setattr(rr, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(rr, "load_manager_prompt", lambda **_: prompt)
    monkeypatch.setattr(rr, "load_all_agents", dict)
    monkeypatch.setattr(rr, "load_mcp_servers", dict)
    monkeypatch.setattr(sync_claude_agents, "ensure_synced", lambda: None)
    monkeypatch.setattr(
        cc_research_lib,
        "init_langfuse_tracing",
        lambda: (None, cc_research_lib.noop_propagate_attributes),
    )
    monkeypatch.setattr(cc_research_lib, "flush_tracing", lambda _: None)
    work = tmp_path / "cli"
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(
        sys, "argv", ["run_research.py", "Task", "--workdir", str(work)]
    )
    seen = []

    class Client:
        def __init__(self, *, options):
            self.options = options
            seen.append(options)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def query(self, prompt, session_id):
            self.output = re.search(r"^OUTPUT_PATH: (.+)$", prompt, re.M).group(1)

        async def receive_response(self):
            hook = self.options.hooks["Stop"][0].hooks[0]
            assert (await hook({"hook_event_name": "Stop"}))["decision"] == "block"
            if save_document:
                (work / self.output).write_text("Complete CLI document")
            yield result("CLI done")

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", Client)
    if save_document:
        asyncio.run(rr.main())
    else:
        with pytest.raises(ValueError, match="Result document"):
            asyncio.run(rr.main())
    item = history.read_iteration(work, "0001")
    assert item["status"] == ("complete" if save_document else "error")
    assert item["result_content"] == (
        "Complete CLI document" if save_document else None
    )
    assert item["answer"] == "CLI done"
    assert RESULT_PROMPT in seen[0].system_prompt
    assert seen[0].env["CLAUDE_CODE_SHELL_PREFIX"] == "/fixture/docker-bash"
    assert seen[0].setting_sources == []
    assert "ScheduleWakeup" in seen[0].disallowed_tools
    assert f"SESSION_WORKSPACE: {work.resolve()}" in seen[0].system_prompt
    for name, role in seen[0].agents.items():
        assert f"SESSION_WORKSPACE: {work.resolve()}" in role.prompt
        if name in {"mle-solver", "mle-solver-remote"}:
            assert "EXPERIMENT_SUMMARY_PATH" in role.prompt
            assert "mle/log.jsonl" not in role.prompt
    assert not (work / "result.md").exists()


def test_technical_resume_keeps_id_path_reminder_and_clears_stale_limit(tmp_path):
    from result_contract import prepare_cli_iteration
    import cc_run_control as ctl
    import run_research as rr

    run = prepare_cli_iteration(
        tmp_path, resume=False, sdk_session_id="sdk", question="Initial", settings={}
    )
    assert (
        asyncio.run(run.stop_hook({"hook_event_name": "Stop"}))["decision"] == "block"
    )
    history.finish_iteration(tmp_path, run.iteration_id, status="error")
    ctl.write_rate_limit(tmp_path, 1, "seven_day")
    resumed = prepare_cli_iteration(
        tmp_path, resume=True, sdk_session_id="sdk", question="", settings={}
    )
    assert resumed.iteration_id == run.iteration_id
    assert resumed.output_path == run.output_path

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def query(self, *args, **kwargs):
            assert ctl.read_rate_limit(tmp_path) is None

        async def receive_response(self):
            assert await resumed.stop_hook({"hook_event_name": "Stop"}) == {}
            (tmp_path / resumed.output_path).write_text("Resumed document")
            yield result()

    asyncio.run(
        rr._drive_across_limits(
            lambda _: Client(),
            tmp_path,
            "sdk",
            0,
            ctl,
            first_query=resumed.query("Continue", mode="resume"),
            start_resume=True,
            completion=resumed,
            render=lambda msg, *_: resumed.observe(msg),
        )
    )
    assert resumed.validated_result() == b"Resumed document"
    assert len(history.list_iterations(tmp_path)["iterations"]) == 1


def test_thinking_block_does_not_duplicate_or_truncate_final_text(tmp_path, capsys):
    from claude_agent_sdk.types import ThinkingBlock
    import run_research as rr

    run = contract(tmp_path)
    text = "A" * 180 + " final suffix"
    msg = AssistantMessage(
        content=[
            ThinkingBlock(thinking="internal", signature="test"),
            TextBlock(text=text),
        ],
        model="fixture",
    )
    rr._render_manager_message(msg, 0, tmp_path, include_result_output=run.observe(msg))
    rr._render_manager_message(
        result(text), 0, tmp_path, include_result_output=run.observe(result(text))
    )
    output = capsys.readouterr().out
    assert output.count(text) == 1
    assert "internal" not in output


def test_resume_policy_survives_another_reconnect(tmp_path):
    from result_contract import prepare_cli_iteration

    run = prepare_cli_iteration(
        tmp_path,
        resume=False,
        sdk_session_id="sdk",
        question="Task",
        settings={"approvals": {"sources": True}},
    )
    history.finish_iteration(tmp_path, run.iteration_id, status="error")
    with pytest.raises(ValueError, match="fixed"):
        prepare_cli_iteration(
            tmp_path,
            resume=True,
            sdk_session_id="sdk",
            question="",
            settings={"approvals": {"plan": True}},
        )
    resumed = prepare_cli_iteration(
        tmp_path,
        resume=True,
        sdk_session_id="sdk",
        question="",
        settings={"approvals": {"sources": True}},
    )
    initial = resumed.query("Continue", mode="resume", approvals={"sources": True})
    reconnect = resumed.query("Continue again", mode="resume")
    assert "sources: required" in initial and "sources: required" in reconnect
    assert "plan: required" not in reconnect


def test_cli_resume_without_saved_conversation_does_not_start_sdk(
    tmp_path, monkeypatch
):
    import claude_agent_sdk
    import run_research as rr
    import cc_research_lib

    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path.parent))

    monkeypatch.setattr(
        sys, "argv", ["run_research.py", "--workdir", str(tmp_path), "--resume"]
    )
    monkeypatch.setattr(
        cc_research_lib,
        "init_langfuse_tracing",
        lambda: (None, cc_research_lib.noop_propagate_attributes),
    )

    def unexpected_client(**kwargs):
        raise AssertionError("Must not start a fresh conversation")

    monkeypatch.setattr(claude_agent_sdk, "ClaudeSDKClient", unexpected_client)
    with pytest.raises(ValueError, match="Saved SDK conversation is missing"):
        asyncio.run(rr.main())


def test_driver_last_empty_turn_after_steer_cannot_publish_old_success(tmp_path):
    import cc_run_control as ctl
    import run_research as rr

    run = contract(tmp_path)
    (tmp_path / run.output_path).write_text("First draft")

    class Client:
        turns = 0

        async def query(self, *args, **kwargs):
            self.turns += 1

        async def receive_response(self):
            if self.turns == 1:
                yield result("First done")
                ctl.write_steer(tmp_path, "Fix", mode="next")
                ctl.send_command(tmp_path, "steer")
                await asyncio.sleep(0.03)

        async def interrupt(self):
            pass

    client = Client()
    asyncio.run(
        rr._drive_with_pause_control(
            client,
            "Task",
            "sdk",
            tmp_path,
            0,
            render=lambda msg, *_: run.observe(msg),
            completion=run,
            poll_interval=0.001,
        )
    )
    assert client.turns == 2
    with pytest.raises(ValueError, match="final SDK response"):
        run.validated_result()
