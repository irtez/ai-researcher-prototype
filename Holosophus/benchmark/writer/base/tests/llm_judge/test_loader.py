import json

import pytest

from writer.base.llm_judge.loader import load_grouped_rubric, select_groups_for_paper_type


def test_load_grouped_rubric_definitions():
    rubric = load_grouped_rubric()

    assert rubric.rubric_id == "conference_reviewer_distilled_20_rubric_en_v3"
    assert rubric.allowed_scores == ["0", "1", "2"]
    first_criterion = rubric.group_by_id("D01_framing_positioning_claims").criteria[0]
    assert first_criterion.criterion_id == "problem_motivation_clarity"
    assert first_criterion.meaning
    assert first_criterion.prompt_guidance
    assert first_criterion.examples


def test_select_groups_for_paper_type():
    rubric = load_grouped_rubric()

    generic = select_groups_for_paper_type("other", rubric)
    ml = select_groups_for_paper_type("ml_experimental", rubric)
    empirical = select_groups_for_paper_type("empirical", rubric)

    expected = [
        "D01_framing_positioning_claims",
        "D02_method_evidence_robustness",
        "D03_interpretation_limitations_discussion",
        "D04_reproducibility_artifacts",
        "D05_statistics_llm_and_writing",
    ]
    assert [group.group_id for group in generic] == expected
    assert [group.group_id for group in ml] == expected
    assert [group.group_id for group in empirical] == expected


def test_legacy_grouped_rubric_format_is_rejected(tmp_path):
    legacy_path = tmp_path / "legacy.json"
    legacy_path.write_text(
        json.dumps(
            {
                "grouped_rubric_id": "old",
                "version": "0",
                "language": "en",
                "score_scale": {"allowed_values": [0, 1, 2, "N/A"]},
                "groups": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="conference_reviewer_rubric.json schema"):
        load_grouped_rubric(legacy_path)
