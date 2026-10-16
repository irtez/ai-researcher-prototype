"""Compatibility tests for canonical agent prompt composition."""

import pytest
from holosophos.files import PROMPTS_DIR_PATH
from holosophos.prompt_composition import compose_prompt_storage


def test_compose_prompt_storage_with_pinned_codearkt() -> None:
    prompts = compose_prompt_storage(
        PROMPTS_DIR_PATH / "writer.yaml",
        PROMPTS_DIR_PATH / "codearkt_framework.yaml",
    )

    system = prompts.system.render(current_date="2026-06-29", tools=[])

    assert "requested scope" in system
    assert "paper_standard.md" in system
    assert "start_writer_check" in system
    assert prompts.plan is not None


@pytest.mark.parametrize("role", ["system", "librarian", "proposer", "mle_solver", "mle_solver_remote", "writer", "reviewer"])
def test_codearkt_renders_its_own_execution_and_output_contract(role):
    prompts = compose_prompt_storage(
        PROMPTS_DIR_PATH / f"{role}.yaml", PROMPTS_DIR_PATH / "codearkt_framework.yaml"
    )
    text = prompts.system.render(current_date="2026-09-11", tools=[])
    assert "<execute>" in text and "<final_answer>" in text
    for cc_only in ("SESSION_WORKSPACE", "OUTPUT_PATH", "EXPERIMENT_SUMMARY_PATH", "request_approval", "native Bash", "end your response immediately"):
        assert cc_only not in text
    if role in ("mle_solver", "mle_solver_remote"):
        assert "mle/log.jsonl" in text
        assert "Long jobs should be blocking" in text
    assert prompts.plan.render(current_date="2026-09-11", tools=[], conversation="Task")
