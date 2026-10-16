import asyncio

from writer.base.llm_judge.definitions import GroupDefinition
from writer.base.llm_judge.executor import run_grouped_rubric_review
from writer.base.llm_judge.models import CriterionScore, RubricGroupResult


async def _mock_group_caller(group: GroupDefinition, prompt: str) -> RubricGroupResult:
    assert group.group_id in prompt
    assert "Do NOT assign or invent a group score" in prompt
    return RubricGroupResult(
        group_id=group.group_id,
        group_name=group.group_name,
        criterion_results=[
            CriterionScore(
                criterion_id=criterion.criterion_id,
                score=2,
                evidence="Section 1 states this explicitly.",
                comment=f"{criterion.criterion_id} is sufficiently addressed.",
                severity="none",
                confidence="high",
            )
            for criterion in group.criteria
        ],
        group_summary="All criteria are sufficiently addressed.",
    )


def test_grouped_rubric_review_with_mocked_group_calls():
    result = asyncio.run(
        run_grouped_rubric_review(
            "A generic scientific paper with a clear method and limitations.",
            paper_type="other",
            group_caller=_mock_group_caller,
            shuffle_criteria=True,
            shuffle_seed=123,
            repeat_count=2,
        )
    )

    assert result.paper_type == "other"
    assert len(result.applicable_groups) == 5
    assert len(result.group_results) == 10
    assert result.module_scores["distilled_review_quality"] == 1.0
    assert result.group_results[0].criterion_results[0].score == 2


def test_grouped_rubric_review_injects_auto_related_work():
    async def related_work_provider(paper_text: str) -> str:
        assert "clear method" in paper_text
        return "# Related Recent Work\nA close RAG paper."

    async def group_caller(group: GroupDefinition, prompt: str) -> RubricGroupResult:
        assert "# Related Work Context" in prompt
        assert "A close RAG paper." in prompt
        return await _mock_group_caller(group, prompt)

    result = asyncio.run(
        run_grouped_rubric_review(
            "A generic scientific paper with a clear method and limitations.",
            paper_type="other",
            group_caller=group_caller,
            auto_related_work=True,
            related_work_provider=related_work_provider,
        )
    )

    assert len(result.group_results) == 5
