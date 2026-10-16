"""Render the actual prompts without model calls; keep framework contracts apart."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import research_context
import research_profiles
import run_research
import docker_bash
from result_contract import RESULT_PROMPT
from sync_claude_agents import render_body

PROMPTS = Path(__file__).resolve().parents[1] / "holosophos/holosophos/prompts"
ROLES = ["system", "librarian", "proposer", "mle_solver", "mle_solver_remote", "writer", "reviewer"]


def cc_prompts(workdir, profile):
    data = {name: yaml.safe_load((PROMPTS / f"{name}.yaml").read_text()) for name in ROLES}
    if profile == "chemistry":
        agents = research_profiles.load_chemistry_agents()
        manager = research_profiles.load_chemistry_manager_prompt()
    else:
        manager = run_research.load_manager_prompt()
        agents = {name.replace("_", "-"): {
            "prompt": render_body(item["canon"], item["claude_code"]),
            "tools": item["claude_code"]["tools"],
        } for name, item in data.items() if name != "system"}
    options = {"system_prompt": manager + "\n\n" + RESULT_PROMPT,
               "agents": {name: SimpleNamespace(prompt=a["prompt"], tools=a["tools"], disallowedTools=[])
                          for name, a in agents.items()}}
    research_context.configure_options(options, workdir)
    docker_bash.configure_options(options, workdir)
    return options


@pytest.mark.parametrize("profile", ["general", "chemistry"])
def test_cc_roles_share_language_and_paths_but_own_their_artifact_contracts(tmp_path, profile):
    options = cc_prompts(tmp_path, profile)
    manager = options["system_prompt"]
    for prompt in [manager, *(role.prompt for role in options["agents"].values())]:
        assert prompt.count("## Session files") == 1
        assert prompt.count("## Output language") == 1
        assert "Russian" in prompt
        assert "English by default" not in prompt
        assert "Workspace: your current" not in prompt
        assert "The duplicate local" not in prompt
        assert "<execute>" not in prompt
    assert "end your response immediately" in manager
    assert "Do not call tools to wait" in manager
    assert "Final answer:" not in manager
    assert "20+ sentences" not in manager
    assert "EXPERIMENT_SUMMARY_DIRECTORY" in manager
    for specialist_detail in ("phase, summary, artifacts", "papers/<arxiv_id>", "includegraphics", "mle/checkpoints/"):
        assert specialist_detail not in manager
    for name in ("mle-solver", "mle-solver-remote"):
        if name not in options["agents"]:
            continue
        prompt = options["agents"][name].prompt
        assert prompt.count("## Experiment summary") == 1
        assert '"phase"' in prompt and '"summary"' in prompt and '"artifacts"' in prompt
        assert "findings or limitations fields" in prompt
        for stale in ("previous calls", "all the time in the universe", "Commit changes from time to time", "mle/log.jsonl", "mcp__mle_kit__hf_datasets_search"):
            assert stale not in prompt
    assert "papers/index.md" in options["agents"]["librarian"].prompt
    assert "figures/paperbanana/" in options["agents"]["writer"].prompt
