import asyncio
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import research_profiles as profiles


@pytest.mark.parametrize("profile", ["general", "chemistry"])
def test_writer_reference_paths_are_readable_from_session_cwd(tmp_path, monkeypatch, profile):
    import re
    import run_research
    import sync_claude_agents as sync

    if profile == "chemistry":
        prompt = profiles.load_chemistry_agents()["writer"]["prompt"]
    else:
        generated = tmp_path / "agents"
        generated.mkdir()
        _, text = sync.render_one(sync.PROMPTS_DIR / "writer.yaml")
        (generated / "writer.md").write_text(text)
        monkeypatch.setattr(run_research, "AGENTS_DIR", generated)
        prompt = run_research.load_agent("writer")["prompt"]
    session = tmp_path / "session"
    session.mkdir()
    monkeypatch.chdir(session)
    paths = re.findall(r"(/[^`\s]+skills_registry\.yaml)", prompt)
    assert paths
    assert f"Source project directory: {sync.PROJECT_DIR}" in prompt
    for value in paths:
        registry = Path(value)
        assert registry.is_file()
        for skill in yaml.safe_load(registry.read_text())["skills"]:
            path = skill["path"]
            root = sync.PROJECT_DIR if path.startswith("holosophos/") else registry.parent
            assert (root / path).is_file(), path


def test_chemistry_defaults_local_and_allows_explicit_modes():
    assert profiles.compute_mode("chemistry", None) == "Local"
    assert profiles.compute_mode("chemistry", "Local") == "Local"
    assert profiles.compute_mode("general", "GPU") == "GPU"
    for mode in ("Auto", "GPU"):
        assert profiles.compute_mode("chemistry", mode) == mode
    with pytest.raises(ValueError):
        profiles.compute_mode("chemistry", "unknown")


def test_profile_merge_cannot_mutate_common_config():
    original = {
        "canon": {"role": "general", "guidelines": "keep"},
        "claude_code": {"tools": ["Read"]},
    }
    merged = profiles.merge_config(original, {"canon": {"role": "chemistry"}})
    merged["claude_code"]["tools"].append("Write")
    assert original["canon"]["role"] == "general"
    assert original["claude_code"]["tools"] == ["Read"]
    assert merged["canon"]["guidelines"] == "keep"


def _call(tool, args=None):
    return asyncio.run(
        profiles.ChemistryToolGate("session")(
            dict(tool_name=tool, tool_input=args or {}), None, None
        )
    )


def test_remote_tools_denied_even_for_manager():
    output = _call("mcp__mle_kit__remote_bash", {"command": "echo bad"})
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_native_read_keeps_its_original_path():
    assert _call("Read", {"file_path": "inputs/graph.png"}) == {}


def test_mcp_review_gets_profile_and_session_path():
    output = _call(
        "mcp__academia__review_pdf_paper",
        {"pdf_filename": "paper.pdf", "research_profile": "general"},
    )
    args = output["hookSpecificOutput"]["updatedInput"]
    assert args == {
        "pdf_filename": "session/paper.pdf",
        "research_profile": "chemistry",
    }


def test_download_and_review_use_the_same_session():
    download = _call(
        "mcp__academia__download_pdf_paper",
        {"pdf_url": "https://example.invalid/paper"},
    )
    assert download["hookSpecificOutput"]["updatedInput"]["session_id"] == "session"
    review = _call(
        "mcp__academia__review_pdf_paper", {"pdf_filename": "session/paper.pdf"}
    )
    assert (
        review["hookSpecificOutput"]["updatedInput"]["pdf_filename"]
        == "session/paper.pdf"
    )


def test_chemical_file_call_cannot_select_another_session():
    output = _call(
        "mcp__chem__read_data_file", {"file_name": "data.csv", "session_id": "other"}
    )
    assert output["hookSpecificOutput"]["updatedInput"]["session_id"] == "session"


@pytest.mark.parametrize("tool", ["search_connected_datasets", "build_structure_research_report"])
def test_mixed_dataset_sources_keep_mapping_and_bind_only_session(tool):
    sources = [
        {"dataset_id": "benzimidazoles"},
        {"dataset_id": "oxazolidinones"},
        {"file_path": "inputs/molecules.csv", "smiles_field": "structure"},
        "inputs/second.json",
    ]
    output = _call("mcp__chem__" + tool, {"sources": sources, "session_id": "other"})
    assert output["hookSpecificOutput"]["updatedInput"] == {
        "sources": sources, "session_id": "session"
    }


@pytest.mark.parametrize("tool", ["list_datasets", "describe_dataset", "query_dataset_sql"])
def test_registered_dataset_calls_do_not_receive_file_or_session_parameters(tool):
    assert _call("mcp__chem__" + tool, {"dataset_id": "benzimidazoles"}) == {}


def test_chemistry_roles_use_shared_dataset_catalog():
    for role, agent in profiles.load_chemistry_agents().items():
        datasets = {"mcp__chem__" + name for name in (
            "list_datasets", "describe_dataset", "query_dataset_sql", "search_connected_datasets"
        )}
        if role in {"librarian", "proposer", "mle-solver"}:
            assert datasets <= set(agent["tools"])
            assert "describe_dataset" in agent["prompt"]
        else:
            assert not datasets.intersection(agent["tools"])
        assert not any("search_chemx_" in tool for tool in agent["tools"])


def test_chemical_reader_accepts_ui_attachment_path():
    output = _call(
        "mcp__chem__read_data_file",
        {"file_name": "inputs/molecules.csv", "session_id": "other"},
    )
    assert output["hookSpecificOutput"]["updatedInput"] == {
        "file_name": "molecules.csv",
        "session_id": "session",
    }


def test_mcp_file_traversal_is_denied():
    output = _call("mcp__academia__describe_image", {"path": "../other/secret.png"})
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_mcp_accepts_absolute_path_in_actual_session_workspace(tmp_path):
    gate = profiles.ChemistryToolGate("session", tmp_path / "session")
    output = asyncio.run(
        gate(
            {
                "tool_name": "mcp__academia__read_pdf",
                "tool_input": {"pdf_path": str(tmp_path / "session/inputs/paper.pdf")},
            },
            None,
            None,
        )
    )
    assert (
        output["hookSpecificOutput"]["updatedInput"]["pdf_path"]
        == "session/inputs/paper.pdf"
    )


def test_reference_tools_use_their_actual_filename_parameters():
    output = _call(
        "mcp__academia__verify_references",
        {"tex_filename": "paper.tex", "bib_filename": "references.bib"},
    )
    assert output["hookSpecificOutput"]["updatedInput"] == {
        "tex_filename": "session/paper.tex",
        "bib_filename": "session/references.bib",
    }
    output = _call(
        "mcp__academia__verify_references_deep", {"pdf_filename": "paper.pdf"}
    )
    assert (
        output["hookSpecificOutput"]["updatedInput"]["pdf_filename"]
        == "session/paper.pdf"
    )
