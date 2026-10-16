"""Session approval contracts, using real files and a fake SDK registration boundary."""

import asyncio
import importlib
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import cc_run_control as ctl


@pytest.fixture
def core():
    # An assertion makes the initial red explicitly name the missing behavior.
    assert importlib.util.find_spec("approval_control"), (
        "session approval runtime is missing"
    )
    return importlib.import_module("approval_control")


@pytest.fixture
def server(core, monkeypatch, tmp_path):
    def decorator(name, description, schema):
        def wrap(handler):
            return types.SimpleNamespace(
                name=name, description=description, schema=schema, handler=handler
            )

        return wrap

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        types.SimpleNamespace(
            tool=decorator,
            create_sdk_mcp_server=lambda *, name, tools: {t.name: t for t in tools},
        ),
    )

    def build(policy=None):
        return core.build_hitl_server(tmp_path, policy)[0]

    return build


def payload(result):
    return result["content"][0]["text"]


async def pending(core, workdir):
    for _ in range(100):
        q = core.read_pending_question(workdir)
        if q:
            return q
        await asyncio.sleep(0.005)
    raise AssertionError("request did not become pending")


async def approve(core, server, workdir, stage, filename):
    task = asyncio.create_task(
        server["request_approval"].handler({"stage": stage, "document_path": filename})
    )
    q = await pending(core, workdir)
    result = core.submit_answer(workdir, q["qid"], action="approve")
    assert result["status"] == "approved"
    assert payload(await task) == "Согласовано."
    return q


def test_policy_strict_timer_and_legacy_override(core):
    assert core.normalize_policy(hitl=True).model_dump() == {
        "sources": True,
        "plan": True,
        "auto_approve_after_seconds": None,
    }
    assert not core.normalize_policy({}, hitl=True).enabled
    assert core.normalize_policy({"plan": True}).enabled
    for bad in (0, -1, True, 1.5, "3"):
        with pytest.raises(ValueError):
            core.normalize_policy({"auto_approve_after_seconds": bad})


@pytest.mark.parametrize("tool", ["Agent", "Task"])
@pytest.mark.parametrize(
    "role",
    [
        "librarian",
        "proposer",
        "mle-solver",
        "mle_solver",
        "mle_solver_local",
        "writer",
        "reviewer",
        "unknown",
    ],
)
def test_gate_denies_missing_sources_for_all_roles(core, tmp_path, tool, role):
    gate = core.ApprovalGate(tmp_path, {"sources": True, "plan": True})
    decision = asyncio.run(
        gate({"tool_name": tool, "tool_input": {"subagent_type": role}}, "id", None)
    )
    out = decision["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    assert "sources" in out["permissionDecisionReason"]
    assert "request_approval" in out["permissionDecisionReason"]


def test_only_plan_does_not_block_librarian_but_blocks_solver_without_writer(
    core, tmp_path
):
    gate = core.ApprovalGate(tmp_path, {"plan": True})
    assert (
        asyncio.run(
            gate(
                {"tool_name": "Agent", "tool_input": {"subagent_type": "librarian"}},
                "id",
                None,
            )
        )
        == {}
    )
    assert (
        asyncio.run(
            gate(
                {
                    "tool_name": "Task",
                    "tool_input": {"subagent_type": "mle-solver-local"},
                },
                "id",
                None,
            )
        )["hookSpecificOutput"]["permissionDecision"]
        == "deny"
    )
    assert asyncio.run(gate({"tool_name": "Read", "tool_input": {}}, "id", None)) == {}


@pytest.mark.parametrize("role,allowed", [("librarian", True), ("proposer", True), ("writer", False), ("mle-solver", False)])
def test_message_uses_registered_recipient_role_before_plan(core, tmp_path, role, allowed):
    from cc_activity import ResearchActivity
    from test_cc_activity import background_agent, ended
    activity = ResearchActivity()
    background_agent(activity, role=role)
    activity.consume(ended())
    gate = core.ApprovalGate(tmp_path, {"plan": True}, activity=activity)
    for key in ("to", "recipient"):
        response = asyncio.run(gate({"tool_name": "SendMessage",
            "tool_input": {key: "task-a", "subagent_type": "librarian", "message": "Continue"}}))
        assert (response == {}) is allowed
        if not allowed:
            assert "plan" in response["hookSpecificOutput"]["permissionDecisionReason"]


def test_unknown_or_unapproved_message_cannot_bypass_sources(core, tmp_path):
    from cc_activity import ResearchActivity
    from test_cc_activity import background_agent
    activity = ResearchActivity()
    background_agent(activity, role="librarian")
    gate = core.ApprovalGate(tmp_path, {"sources": True, "plan": True}, activity=activity)
    response = asyncio.run(gate({"tool_name": "SendMessage", "tool_input": {"to": "task-a"}}))
    assert "sources" in response["hookSpecificOutput"]["permissionDecisionReason"]
    gate = core.ApprovalGate(tmp_path, {"plan": True}, activity=activity)
    response = asyncio.run(gate({"tool_name": "SendMessage", "tool_input": {"to": "unknown", "subagent_type": "librarian"}}))
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_disabled_tools_and_prompt(core, server, tmp_path):
    assert set(server()) == {"ask_human"}
    assert core.approval_prompt(tmp_path, {}) == ""
    assert "sources" in core.approval_prompt(tmp_path, {"sources": True})


def test_approval_snapshot_reuse_persistence_and_source_invalidation(
    core, server, tmp_path
):
    (tmp_path / "source.md").write_text(
        "# Sources\nLocal measurements", encoding="utf-8"
    )
    (tmp_path / "steps.md").write_text("# Plan\nCompute mean", encoding="utf-8")
    api = server({"sources": True, "plan": True})

    async def scenario():
        q = await approve(core, api, tmp_path, "sources", str(tmp_path / "source.md"))
        assert q["document_path"] == "source.md"
        assert q["context"] == "# Sources\nLocal measurements"
        assert q["document_digest"]
        assert q["auto_approve_at"] is None
        assert ctl.read_question(tmp_path) is None
        reused = payload(
            await api["request_approval"].handler(
                {"stage": "sources", "document_path": "source.md"}
            )
        )
        assert reused == "Согласовано."
        await approve(core, api, tmp_path, "plan", "steps.md")
        # A fresh gate loads approvals from disk for a technical resume.
        gate = core.ApprovalGate(tmp_path, {"sources": True, "plan": True})
        assert (
            await gate(
                {"tool_name": "Agent", "tool_input": {"subagent_type": "writer"}},
                "id",
                None,
            )
            == {}
        )
        (tmp_path / "source.md").write_text("# Sources\nUpdated", encoding="utf-8")
        deny = await gate(
            {"tool_name": "Agent", "tool_input": {"subagent_type": "writer"}},
            "id",
            None,
        )
        assert "sources" in deny["hookSpecificOutput"]["permissionDecisionReason"]
        await approve(core, api, tmp_path, "sources", "source.md")
        deny = await gate(
            {"tool_name": "Agent", "tool_input": {"subagent_type": "writer"}},
            "id",
            None,
        )
        assert "plan" in deny["hookSpecificOutput"]["permissionDecisionReason"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "kind",
    ["missing", "empty", "binary", "outside", "escape", "directory", "extension"],
)
def test_bad_document_has_no_pending_or_timer(core, server, tmp_path, kind):
    file = tmp_path / "draft.md"
    if kind == "empty":
        file.write_text(" \n")
    elif kind == "binary":
        file.write_bytes(b"\x00\xff")
    elif kind in {"outside", "escape"}:
        outside = tmp_path.parent / (tmp_path.name + "-outside.md")
        outside.write_text("outside")
        if kind == "outside":
            file = outside
        else:
            file.symlink_to(outside)
    elif kind == "directory":
        file.mkdir()
    elif kind == "extension":
        file = tmp_path / "draft.txt"
        file.write_text("content")
    result = asyncio.run(
        server({"sources": True, "auto_approve_after_seconds": 1})[
            "request_approval"
        ].handler({"stage": "sources", "document_path": str(file)})
    )
    assert result["isError"]
    assert core.read_pending_question(tmp_path) is None
    assert core.read_last_decision(tmp_path) is None


def test_disabled_stage_does_not_read_file(core, server):
    result = asyncio.run(
        server({"plan": True})["request_approval"].handler(
            {"stage": "sources", "document_path": "/absent/file.md"}
        )
    )
    assert payload(result) == "Согласование этого этапа не требуется."


@pytest.mark.parametrize("change", ["edit", "delete"])
def test_changed_pending_snapshot_rejects_old_answer(core, server, tmp_path, change):
    file = tmp_path / "draft.md"
    file.write_text("Original decision")
    api = server({"sources": True, "auto_approve_after_seconds": 1})

    async def scenario():
        task = asyncio.create_task(
            api["request_approval"].handler(
                {"stage": "sources", "document_path": "draft.md"}
            )
        )
        q = await pending(core, tmp_path)
        if change == "edit":
            file.write_text("Changed decision")
        else:
            file.unlink()
        with pytest.raises(ValueError):
            core.submit_answer(tmp_path, q["qid"], action="approve")
        assert payload(await task).startswith("Согласование утратило силу.")
        assert core.read_pending_question(tmp_path) is None
        assert core.read_last_decision(tmp_path)["status"] == "invalidated"

    asyncio.run(scenario())


def test_revise_then_new_proposal_rejects_late_old_answer(core, server, tmp_path):
    file = tmp_path / "draft.md"
    file.write_text("First proposal")
    api = server({"sources": True})

    async def scenario():
        task = asyncio.create_task(
            api["request_approval"].handler(
                {"stage": "sources", "document_path": "draft.md"}
            )
        )
        q = await pending(core, tmp_path)
        for action, text in [("answer", "yes"), ("revise", " "), ("unknown", "")]:
            with pytest.raises(ValueError):
                core.submit_answer(tmp_path, q["qid"], action=action, text=text)
        result = core.submit_answer(
            tmp_path, q["qid"], action="revise", text="Use real data"
        )
        assert result["status"] == "changes_requested"
        assert payload(await task) == "Нужно доработать.\nUse real data"
        file.write_text("Updated proposal")
        task = asyncio.create_task(
            api["request_approval"].handler(
                {"stage": "sources", "document_path": "draft.md"}
            )
        )
        q2 = await pending(core, tmp_path)
        assert q2["qid"] != q["qid"]
        with pytest.raises(ValueError):
            core.submit_answer(tmp_path, q["qid"], action="approve")
        core.submit_answer(tmp_path, q2["qid"], action="approve")
        assert payload(await task) == "Согласовано."

    asyncio.run(scenario())


def test_ordinary_legacy_answer_does_not_approve_and_single_pending(
    core, server, tmp_path
):
    (tmp_path / "draft.md").write_text("Proposal")
    api = server({"sources": True})

    async def scenario():
        task = asyncio.create_task(
            api["request_approval"].handler(
                {"stage": "sources", "document_path": "draft.md"}
            )
        )
        q = await pending(core, tmp_path)
        ctl.write_answer(tmp_path, "approved")
        await asyncio.sleep(0.15)
        assert not task.done()
        assert (
            await api["ask_human"].handler({"question": "Question?", "context": ""})
        )["isError"]
        assert core.read_pending_question(tmp_path)["qid"] == q["qid"]
        core.cancel_pending(tmp_path)
        assert payload(await task).startswith("Согласование отменено.")
        ask = asyncio.create_task(
            api["ask_human"].handler({"question": "Question?", "context": ""})
        )
        q = await pending(core, tmp_path)
        assert q["stage"] is None
        assert q["auto_approve_at"] is None
        ctl.write_answer(tmp_path, "Legacy answer")
        assert (await ask)["content"][0]["text"] == "Legacy answer"
        gate = core.ApprovalGate(tmp_path, {"sources": True})
        assert (
            await gate(
                {"tool_name": "Agent", "tool_input": {"subagent_type": "librarian"}},
                "id",
                None,
            )
        )["hookSpecificOutput"]["permissionDecision"] == "deny"

    asyncio.run(scenario())


def test_auto_approval_and_manual_wins_same_deadline(
    core, server, tmp_path, monkeypatch
):
    (tmp_path / "draft.md").write_text("Proposal")
    now = [1000.0]
    monkeypatch.setattr(core.time, "time", lambda: now[0])
    api = server({"sources": True, "auto_approve_after_seconds": 2})

    async def scenario():
        task = asyncio.create_task(
            api["request_approval"].handler(
                {"stage": "sources", "document_path": "draft.md"}
            )
        )
        q = await pending(core, tmp_path)
        assert q["auto_approve_at"] == 1002.0
        now[0] = 1002.0
        core.submit_answer(tmp_path, q["qid"], action="revise", text="Change it")
        assert payload(await task) == "Нужно доработать.\nChange it"
        assert core.read_last_decision(tmp_path)["resolution"] == "manual"
        (tmp_path / "draft.md").write_text("Revised proposal")
        task = asyncio.create_task(
            api["request_approval"].handler(
                {"stage": "sources", "document_path": "draft.md"}
            )
        )
        q = await pending(core, tmp_path)
        now[0] = q["auto_approve_at"]
        assert payload(await task) == "Согласовано автоматически."
        assert core.read_last_decision(tmp_path)["resolution"] == "auto"
        with pytest.raises(ValueError):
            core.submit_answer(tmp_path, q["qid"], action="revise", text="Too late")

    asyncio.run(scenario())


def test_timer_off_transport_cancel_and_session_isolation(
    core, server, tmp_path, monkeypatch
):
    (tmp_path / "draft.md").write_text("Proposal")
    monkeypatch.setenv("ASK_HUMAN_TIMEOUT", "0")
    api = server({"sources": True})

    async def scenario():
        task = asyncio.create_task(
            api["request_approval"].handler(
                {"stage": "sources", "document_path": "draft.md"}
            )
        )
        q = await pending(core, tmp_path)
        await asyncio.sleep(0.15)
        assert not task.done()
        assert q["auto_approve_at"] is None
        assert core.read_pending_question(tmp_path / "other") is None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert core.read_pending_question(tmp_path) is None
        assert core.read_last_decision(tmp_path)["status"] == "cancelled"
        result = await api["ask_human"].handler({"question": "Q", "context": ""})
        assert ctl.NO_HUMAN_ANSWER_SENTINEL in result["content"][0]["text"]

    asyncio.run(scenario())


def test_reused_snapshot_tracks_new_document_path(core, server, tmp_path):
    (tmp_path / "first.md").write_text("The same content")
    (tmp_path / "renamed.markdown").write_text("The same content")
    api = server({"sources": True})

    async def scenario():
        await approve(core, api, tmp_path, "sources", "first.md")
        result = payload(
            await api["request_approval"].handler(
                {"stage": "sources", "document_path": "renamed.markdown"}
            )
        )
        assert result == "Согласовано."
        assert "renamed.markdown" in core.approval_prompt(tmp_path, {"sources": True})
        (tmp_path / "renamed.markdown").write_text("Changed new decision")
        gate = core.ApprovalGate(tmp_path, {"sources": True})
        assert (
            await gate(
                {"tool_name": "Task", "tool_input": {"subagent_type": "proposer"}},
                "id",
                None,
            )
        )["hookSpecificOutput"]["permissionDecision"] == "deny"

    asyncio.run(scenario())


def test_invalid_action_type_is_value_error(core, tmp_path):
    with pytest.raises(ValueError):
        core.submit_answer(tmp_path, "id", action=[])


def test_manual_answer_available_through_old_control_channel(core, tmp_path):
    ctl.write_question(tmp_path, "legacy-q", "Choose data")
    result = core.submit_answer(tmp_path, "legacy-q", text="MNIST")
    assert result["status"] == "answered"
    assert ctl.take_answer(tmp_path, "legacy-q") == "MNIST"
    assert core.read_pending_question(tmp_path) is None


def test_changes_to_sources_cancel_pending_plan_before_autoapproval(
    core, server, tmp_path, monkeypatch
):
    source = tmp_path / "source.md"
    source.write_text("Initial sources")
    (tmp_path / "plan.md").write_text("Proposed plan")
    api = server({"sources": True, "plan": True, "auto_approve_after_seconds": 1})

    async def scenario():
        await approve(core, api, tmp_path, "sources", "source.md")
        task = asyncio.create_task(
            api["request_approval"].handler(
                {"stage": "plan", "document_path": "plan.md"}
            )
        )
        q = await pending(core, tmp_path)
        source.write_text("Replacement sources")
        monkeypatch.setattr(core.time, "time", lambda: q["auto_approve_at"] + 10)
        assert payload(await task).startswith("Согласование утратило силу.")
        assert core.read_last_decision(tmp_path)["resolution"] == "system"

    asyncio.run(scenario())


def test_only_sources_approval_allows_solver_with_plan_disabled(core, server, tmp_path):
    (tmp_path / "sources.md").write_text("Sources")
    api = server({"sources": True})

    async def scenario():
        await approve(core, api, tmp_path, "sources", "sources.md")
        for role in ("mle_solver_local", "writer", "reviewer", "unknown"):
            assert (
                await core.ApprovalGate(tmp_path, {"sources": True})(
                    {"tool_name": "Task", "tool_input": {"subagent_type": role}},
                    "id",
                    None,
                )
                == {}
            )

    asyncio.run(scenario())


def test_simultaneous_manual_answers_resolve_exactly_once(core, server, tmp_path):
    import concurrent.futures
    import threading

    (tmp_path / "source.md").write_text("Sources")
    api = server({"sources": True})

    async def scenario():
        task = asyncio.create_task(
            api["request_approval"].handler(
                {"stage": "sources", "document_path": "source.md"}
            )
        )
        q = await pending(core, tmp_path)
        barrier = threading.Barrier(2)

        def submit(action):
            barrier.wait()
            try:
                return core.submit_answer(
                    tmp_path, q["qid"], action=action, text="Change sources"
                )
            except ValueError:
                return "stale"

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit, action) for action in ("approve", "revise")]
            results = [future.result(timeout=2) for future in futures]
        assert results.count("stale") == 1
        accepted = next(result for result in results if isinstance(result, dict))
        assert payload(await task).splitlines()[0] == ("Согласовано." if accepted["status"] == "approved" else "Нужно доработать.")
        assert core.read_last_decision(tmp_path)["status"] == accepted["status"]

    asyncio.run(scenario())


def test_model_receives_short_decision_but_history_keeps_document(core, server, tmp_path):
    document = "# Источники\n" + "Длинное описание данных.\n" * 300
    (tmp_path / "sources.md").write_text(document)
    api = server({"sources": True})
    async def scenario():
        task = asyncio.create_task(api["request_approval"].handler({"stage": "sources", "document_path": "sources.md"}))
        q = await pending(core, tmp_path)
        core.submit_answer(tmp_path, q["qid"], action="approve")
        reply = await task
        assert reply["content"] == [{"type": "text", "text": "Согласовано."}]
        assert core.read_last_decision(tmp_path)["document_text"] == document
        assert q["question"] == "Согласовать источники?"
    asyncio.run(scenario())
