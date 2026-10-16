"""Generic Pydantic output models for grouped rubric judging."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .definitions import PaperType


ScoreValue = int | Literal["N/A"]
Severity = Literal["minor", "major", "critical", "none"]
Confidence = Literal["low", "medium", "high"]


class CriterionScore(BaseModel):
    criterion_id: str
    score: ScoreValue
    evidence: str
    comment: str
    missing_info: list[str] = Field(default_factory=list)
    severity: Severity
    confidence: Confidence

    @field_validator("score")
    @classmethod
    def validate_score(cls, score: ScoreValue) -> ScoreValue:
        if score == "N/A":
            return score
        if score not in (0, 1, 2):
            raise ValueError("score must be 0, 1, 2, or 'N/A'")
        return score


class RubricGroupResult(BaseModel):
    group_id: str
    group_name: str
    criterion_results: list[CriterionScore]
    group_summary: str


class ModuleScore(BaseModel):
    module_id: str
    score: float | None
    applicable_criteria: int = 0
    total_criteria: int = 0


class GroupedRubricSummary(BaseModel):
    main_problem: str = ""
    top_strengths: list[str] = Field(default_factory=list)
    top_weaknesses: list[str] = Field(default_factory=list)
    overall_discussion_persuasiveness: int | None = Field(default=None, ge=0, le=2)
    overall_recommendation: Literal[
        "strong_accept",
        "weak_accept",
        "borderline",
        "weak_reject",
        "strong_reject",
    ] | None = None


class GroupedRubricReviewResponse(BaseModel):
    paper_id: str | None = None
    paper_type: PaperType
    applicable_groups: list[str]
    group_results: list[RubricGroupResult]
    module_scores: dict[str, float | None]
    main_problem: str
    top_strengths: list[str]
    top_weaknesses: list[str]
    overall_discussion_persuasiveness: int | None = Field(default=None, ge=0, le=2)
    overall_recommendation: Literal[
        "strong_accept",
        "weak_accept",
        "borderline",
        "weak_reject",
        "strong_reject",
    ] | None = None
    module_score_details: dict[str, ModuleScore] = Field(default_factory=dict)
