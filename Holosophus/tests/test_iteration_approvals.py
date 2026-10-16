"""Approval lifetime, preparation-only tools and durable plans without an LLM."""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "holosophos"))
import approval_control as core
from holosophos import research_iterations as history


def start(work, *, kind="initial", policy=None):
    policy = core.normalize_policy(policy or {"plan": True})
    item = history.start_iteration(
        work,
        kind=kind,
        user_message="Task",
        settings={"approvals": policy.model_dump()},
    )
    core.begin_iteration(work, item["id"], policy)
    return item, policy


def approve(work, item, policy, stage="plan", text="Plan", name="proposal.md"):
    path = f"{item['plan_directory'] if stage == 'plan' else item['sources_directory']}/{name}"
    (work / path).write_text(text)
    record, waiting = core._begin(
        work, policy, stage=stage, document_path=path, iteration_id=item["id"]
    )
    assert waiting
    core.submit_answer(work, record["id"], "approve")
    return path, record["id"]


def gate(work, item, policy, name, args=None, **metadata):
    return asyncio.run(
        core.ApprovalGate(work, policy, iteration_id=item["id"])(
            {"tool_name": name, "tool_input": args or {}, **metadata}
        )
    )


def denied(value):
    assert value["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_new_iteration_requires_new_plan_and_keeps_old_decision(tmp_path):
    first, policy = start(tmp_path)
    old_path, qid = approve(tmp_path, first, policy)
    assert not gate(tmp_path, first, policy, "Agent", {"subagent_type": "writer"})
    core.close_iteration(tmp_path, first["id"])
    history.finish_iteration(tmp_path, first["id"], status="complete")
    denied(gate(tmp_path, first, policy, "Agent", {"subagent_type": "writer"}))
    second, policy = start(tmp_path, kind="feedback")
    denied(gate(tmp_path, second, policy, "Agent", {"subagent_type": "mle-solver"}))
    with pytest.raises(ValueError, match="plan.*directory"):
        core._begin(
            tmp_path,
            policy,
            stage="plan",
            document_path=old_path,
            iteration_id=second["id"],
        )
    approve(tmp_path, second, policy, text="A new plan")
    assert (tmp_path / old_path).read_text() == "Plan"
    old = history.get_iteration(tmp_path, first["id"])["approval_state"]
    assert old["stages"]["plan"]["status"] == "approved"
    assert old["decisions"][0]["qid"] == qid
    assert old["decisions"][0]["document_text"] == "Plan"


def test_sources_reuse_but_changed_sources_invalidate_new_plan(tmp_path):
    first, policy = start(tmp_path, policy={"sources": True, "plan": True})
    sources, _ = approve(tmp_path, first, policy, "sources", "Dataset A")
    approve(tmp_path, first, policy)
    core.close_iteration(tmp_path, first["id"])
    history.finish_iteration(tmp_path, first["id"], status="complete")
    second, policy = start(tmp_path, kind="feedback", policy=policy.model_dump())
    assert not gate(tmp_path, second, policy, "Agent", {"subagent_type": "proposer"})
    approve(tmp_path, second, policy)
    (tmp_path / sources).write_text("Dataset B")
    denied(gate(tmp_path, second, policy, "Bash", {"command": "calculate"}))
    with pytest.raises(ValueError, match="sources"):
        core.validate_completion(tmp_path, second["id"], policy)


@pytest.mark.parametrize("sources_enabled", [True, False])
def test_feedback_attachments_invalidate_sources_once(tmp_path, sources_enabled):
    first, policy = start(tmp_path, policy={"sources": True, "plan": True})
    old_sources, _ = approve(tmp_path, first, policy, "sources", "Original inputs")
    approve(tmp_path, first, policy)
    core.close_iteration(tmp_path, first["id"])
    history.finish_iteration(tmp_path, first["id"], status="complete")
    policy = core.normalize_policy({"sources": sources_enabled, "plan": True})
    second = history.start_iteration(tmp_path, kind="feedback", user_message="Use new data",
        settings={"input_files": ["inputs/new.csv"], "approvals": policy.model_dump()})
    core.begin_iteration(tmp_path, second["id"], policy)
    with core._transaction(tmp_path) as (_, state):
        assert not core._approved(state, "sources")
    if sources_enabled:
        denied(gate(tmp_path, second, policy, "Agent", {"subagent_type": "proposer"}))
        assert "new attachments" in core.approval_prompt(tmp_path, policy)
        approve(tmp_path, second, policy, "sources", "New inputs")
    approve(tmp_path, second, policy)
    core.begin_iteration(tmp_path, second["id"], policy)  # Technical reconnect.
    assert not gate(tmp_path, second, policy, "Agent", {"subagent_type": "mle-solver"})
    assert (tmp_path / old_sources).read_text() == "Original inputs"
    assert history.get_iteration(tmp_path, first["id"])["approval_state"]["stages"]["sources"]["status"] == "approved"
    if not sources_enabled:
        core.close_iteration(tmp_path, second["id"])
        history.finish_iteration(tmp_path, second["id"], status="complete")
        third, policy = start(tmp_path, kind="feedback", policy={"sources": True, "plan": True})
        denied(gate(tmp_path, third, policy, "Agent", {"subagent_type": "proposer"}))


@pytest.mark.parametrize(
    "tool,args",
    [
        ("Bash", {"command": "echo corrected > result.md"}),
        ("Write", {"file_path": "result.md", "content": "Result"}),
        ("mcp__mle_kit__bash", {"command": "calculate"}),
        ("mcp__chem__calculate_descriptors", {"smiles": "CCO"}),
        ("Agent", {"subagent_type": "new-experimenter"}),
    ],
)
def test_direct_and_delegated_execution_wait_for_current_plan(tmp_path, tool, args):
    item, policy = start(tmp_path)
    denied(gate(tmp_path, item, policy, tool, args))
    approve(tmp_path, item, policy)
    assert not gate(tmp_path, item, policy, tool, args)


def test_preparation_is_available_without_execution_escape(tmp_path):
    item, policy = start(tmp_path)
    for tool, args in [
        ("Read", {"file_path": "inputs/data.csv"}),
        ("Glob", {}),
        (
            "Write",
            {
                "file_path": item["plan_directory"] + "/short.md",
                "content": "Round values",
            },
        ),
        ("Agent", {"subagent_type": "proposer"}),
    ]:
        assert not gate(tmp_path, item, policy, tool, args)
    assert not gate(
        tmp_path,
        item,
        policy,
        "mcp__academia__generate_research_proposals",
        agent_id="p",
        agent_type="proposer",
    )
    denied(
        gate(
            tmp_path,
            item,
            policy,
            "Bash",
            {"command": "calculate"},
            agent_id="p",
            agent_type="proposer",
        )
    )
    denied(
        gate(
            tmp_path,
            item,
            policy,
            "Agent",
            {"subagent_type": "writer"},
            agent_id="p",
            agent_type="proposer",
        )
    )
    denied(
        gate(
            tmp_path,
            item,
            policy,
            "Write",
            {"file_path": item["plan_directory"] + "/../../result.md"},
        )
    )


def test_old_timer_qid_and_tool_cannot_approve_next_iteration(tmp_path, monkeypatch):
    item, policy = start(
        tmp_path, policy={"plan": True, "auto_approve_after_seconds": 1}
    )
    path = item["plan_directory"] + "/first.md"
    (tmp_path / path).write_text("Plan")
    pending, _ = core._begin(
        tmp_path, policy, stage="plan", document_path=path, iteration_id=item["id"]
    )
    core.close_iteration(tmp_path, item["id"])
    history.finish_iteration(tmp_path, item["id"], status="cancelled")
    other, _ = start(tmp_path, kind="feedback", policy=policy.model_dump())
    monkeypatch.setattr(core.time, "time", lambda: pending["auto_approve_at"] + 20)
    assert core._poll_result(tmp_path, pending["id"])["status"] == "cancelled"
    with pytest.raises(ValueError, match="stale"):
        core.submit_answer(tmp_path, pending["id"], "approve")
    with pytest.raises(ValueError, match="iteration"):
        core._begin(
            tmp_path, policy, stage="plan", document_path=path, iteration_id=item["id"]
        )
    denied(gate(tmp_path, other, policy, "Write", {"file_path": "result.md"}))


@pytest.mark.parametrize("status", ["complete", "error", "cancelled"])
def test_feedback_plan_without_approval_is_kept_for_every_terminal_state(
    tmp_path, status
):
    from result_contract import ResultContract

    item, policy = start(
        tmp_path, kind="feedback", policy={"sources": False, "plan": False}
    )
    run = ResultContract(tmp_path, item["id"])
    assert "request_approval" not in run.query("Round", mode="feedback")
    assert "PLAN_DIRECTORY" in run.query("Round", mode="feedback")
    with pytest.raises(ValueError, match="action plan"):
        run._validate_documents()
    path = tmp_path / item["plan_directory"] / "rounding.md"
    path.write_text("Round existing numbers. Keep the rest unchanged.")
    (tmp_path / item["output_path"]).write_text("Result")
    assert run._validate_documents() == b"Result"
    core.close_iteration(tmp_path, item["id"])
    history.finish_iteration(tmp_path, item["id"], status=status)
    path.write_text("Changed later")
    plan = history.get_iteration(tmp_path, item["id"])["plans"][0]
    assert (
        tmp_path / plan["snapshot_path"]
    ).read_text() == "Round existing numbers. Keep the rest unchanged."


def test_revisions_and_resume_keep_distinct_decisions_and_current_permission(tmp_path):
    item, policy = start(tmp_path)
    path = item["plan_directory"] + "/plan.md"
    (tmp_path / path).write_text("First draft")
    first, _ = core._begin(
        tmp_path, policy, stage="plan", document_path=path, iteration_id=item["id"]
    )
    core.submit_answer(tmp_path, first["id"], "revise", "Use local data")
    (tmp_path / path).write_text("Use local data")
    next_question, _ = core._begin(
        tmp_path, policy, stage="plan", document_path=path, iteration_id=item["id"]
    )
    with pytest.raises(ValueError, match="stale"):
        core.submit_answer(tmp_path, first["id"], "approve")
    core.submit_answer(tmp_path, next_question["id"], "approve")
    core.begin_iteration(tmp_path, item["id"], policy)  # reconnect is idempotent
    assert not gate(tmp_path, item, policy, "Bash", {"command": "run"})
    decisions = history.get_iteration(tmp_path, item["id"])["approval_state"][
        "decisions"
    ]
    assert [(d["status"], d["document_text"]) for d in decisions] == [
        ("changes_requested", "First draft"),
        ("approved", "Use local data"),
    ]


def test_resume_prompt_reuses_approved_plan_and_refreshes_changed_file(tmp_path):
    from result_contract import ResultContract

    item, policy = start(tmp_path, kind="feedback")
    path, _ = approve(tmp_path, item, policy)
    run = ResultContract(tmp_path, item["id"])
    query = run.query("Continue", mode="resume")
    assert f"reuse the unchanged approved document at {path}" in query
    assert "Reuse this iteration's existing action plan" in query
    assert "write a new Markdown action plan" not in query
    assert 'Call request_approval(stage="plan"' not in query
    (tmp_path / path).write_text("Changed scope")
    changed = run.query("Continue", mode="resume")
    assert "reuse the unchanged approved document" not in changed
    assert 'Call request_approval(stage="plan"' in changed


@pytest.mark.parametrize("tool", [
    "pubchem_search_by_formula", "pubchem_search_by_smiles",
    "pubchem_search_by_exact_mass", "pubchem_search_by_mass_range",
    "pubchem_get_by_cids", "pubchem_convert_identifier",
    "pubchem_search_by_informal_name", "pubchem_get_computed_properties",
    "search_chemx_cytotox", "search_chemx_seltox", "search_chemx_synergy",
    "search_chemx_nanozymes", "search_chemx_benzimidazoles",
    "search_chemx_oxazolidinones", "search_chemx_complexes",
    "search_chemx_eyedrops", "search_chemx_co_crystals", "search_chemx_nanomag",
    "search_connected_datasets", "query_dataset_sql",
])
def test_chemical_retrieval_waits_for_sources_but_not_experiment_plan(tmp_path, tool):
    item, policy = start(tmp_path, policy={"sources": True, "plan": True})
    metadata = {"agent_id": "librarian-1", "agent_type": "librarian"}
    name = "mcp__chem__" + tool
    denied(gate(tmp_path, item, policy, name, **metadata))
    approve(tmp_path, item, policy, "sources")
    assert not gate(tmp_path, item, policy, name, **metadata)
    denied(gate(tmp_path, item, policy, "mcp__chem__calculate_descriptors", **metadata))
    denied(gate(tmp_path, item, policy, "mcp__chem__build_structure_research_report", **metadata))


def test_uploaded_tables_can_be_read_to_prepare_sources_document(tmp_path):
    item, policy = start(tmp_path, policy={"sources": True, "plan": True})
    assert not gate(tmp_path, item, policy, "mcp__chem__read_data_file", {"file_name": "data.csv"})


@pytest.mark.parametrize("tool", ["list_datasets", "describe_dataset"])
def test_dataset_metadata_is_available_before_sources_approval(tmp_path, tool):
    item, policy = start(tmp_path, policy={"sources": True, "plan": True})
    assert not gate(tmp_path, item, policy, "mcp__chem__" + tool, {"dataset_id": "benzimidazoles"})


def test_initial_approval_context_explains_workspace_preparation(tmp_path):
    from result_contract import ResultContract
    item, policy = start(tmp_path, policy={"sources": True, "plan": True})
    run = ResultContract(tmp_path, item["id"])
    query = run.query("Inspect inputs", mode="initial")
    assert (tmp_path / item["sources_directory"]).is_dir()
    assert (tmp_path / item["plan_directory"]).is_dir()
    assert "Tool access during this iteration" in query
    assert "already created" in query
    assert "ls/pwd/find/mkdir" in query
    for name in ("use_glob", "use_grep"):
        tool = "mcp__mle_kit__" + name
        assert tool in query
        assert not gate(tmp_path, item, policy, tool, {"pattern": "*", "path": tmp_path.name})
    assert f'path="{tmp_path.name}"' in query
    assert "use Glob to discover" not in query
    # Native search is allowed for CLI builds that actually provide it.
    for name in ("Glob", "Grep"):
        assert not gate(tmp_path, item, policy, name, {"pattern": "input"})
    denied(gate(tmp_path, item, policy, "Bash", {"command": "ls -la ."}))
    assert core.approval_prompt(tmp_path, {}) == ""


@pytest.mark.parametrize("sources,plan", [(True, True), (True, False), (False, True)])
def test_tool_access_instructions_follow_enabled_stages(tmp_path, sources, plan):
    item, policy = start(tmp_path, policy={"sources": sources, "plan": plan})
    query = core.approval_prompt(tmp_path, policy)
    assert ("Before sources approval:" in query) == sources
    assert ("Before plan approval:" in query) == plan
    assert "After all enabled stages are approved:" in query
    # An available retrieval/delegation tool follows the sources stage;
    # result writes, shell and experimenters follow all selected stages.
    preparation = [
        ("WebSearch", {}),
        ("Agent", {"subagent_type": "librarian"}),
        ("Agent", {"subagent_type": "proposer"}),
    ]
    execution = [
        ("Bash", {"command": "ls"}),
        ("Write", {"file_path": item["output_path"], "content": "Result"}),
        ("Agent", {"subagent_type": "mle-solver"}),
    ]
    for name, args in preparation:
        assert bool(gate(tmp_path, item, policy, name, args)) == sources
    if sources:
        approve(tmp_path, item, policy, "sources")
    for name, args in preparation:
        assert not gate(tmp_path, item, policy, name, args)
    for name, args in execution:
        assert bool(gate(tmp_path, item, policy, name, args)) == plan
    if plan:
        approve(tmp_path, item, policy)
    for name, args in execution:
        assert not gate(tmp_path, item, policy, name, args)
