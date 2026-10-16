from writer.base.llm_judge.loader import load_grouped_rubric
from writer.base.llm_judge.prompts import build_group_prompt, ordered_criteria


def test_group_prompt_contains_only_current_group_criteria():
    rubric = load_grouped_rubric()
    group = rubric.group_by_id("D01_framing_positioning_claims")

    prompt = build_group_prompt(rubric, group, "Paper text")

    assert "problem_motivation_clarity" in prompt
    assert "claim_evidence_calibration" in prompt
    assert "method_procedure_detail" not in prompt
    assert "Do NOT assign or invent a group score" in prompt
    assert "Positive example:" in prompt
    assert "evidence expectations:" in prompt
    assert '"score": "0 | 1 | 2"' in prompt


def test_shuffle_changes_relative_order_deterministically():
    rubric = load_grouped_rubric()
    group = rubric.group_by_id("D04_reproducibility_artifacts")

    original = [criterion.criterion_id for criterion in ordered_criteria(group)]
    shuffled_a = [
        criterion.criterion_id
        for criterion in ordered_criteria(group, shuffle=True, shuffle_seed=7)
    ]
    shuffled_b = [
        criterion.criterion_id
        for criterion in ordered_criteria(group, shuffle=True, shuffle_seed=7)
    ]

    assert shuffled_a == shuffled_b
    assert sorted(shuffled_a) == sorted(original)
    assert shuffled_a != original
