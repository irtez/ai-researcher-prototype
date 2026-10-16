from writer.base.llm_judge.aggregation import normalize_scores
from writer.base.llm_judge.models import CriterionScore


def _score(criterion_id: str, score: int | str) -> CriterionScore:
    return CriterionScore(
        criterion_id=criterion_id,
        score=score,
        evidence="Section 1",
        comment="comment",
        severity="none",
        confidence="high",
    )


def test_normalize_scores_excludes_na():
    assert normalize_scores([_score("a", 2), _score("b", 1), _score("c", "N/A")]) == 0.75


def test_normalize_scores_all_na_returns_none():
    assert normalize_scores([_score("a", "N/A")]) is None
