"""Rubric definitions: metric meaning and prompt guidance live here."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


PaperType = Literal[
    "ml_experimental",
    "llm_system",
    "theoretical",
    "empirical",
    "survey",
    "mixed",
    "other",
]


class CriterionDefinition(BaseModel):
    """Meaning of one metric, independent from any model output schema."""

    criterion_id: str
    name: str
    meaning: str
    question: str
    category: str = ""
    score_descriptions: dict[str, str]
    prompt_guidance: list[str] = Field(default_factory=list)
    evidence_expectations: list[str] = Field(default_factory=list)
    missing_info_examples: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)
    severity_guidance: dict[str, str] = Field(default_factory=dict)
    required_evidence: str = (
        "Cite concrete paper evidence: section name, figure/table, page, quote, or explicit absence."
    )
    na_policy: str = (
        "Use N/A only when the criterion is genuinely not applicable to this paper type; "
        "do not use N/A for missing information that should have been reported."
    )
    required_comment_pattern: str | None = None


class GroupDefinition(BaseModel):
    group_id: str
    module_id: str
    group_name: str
    meaning: str
    criteria: list[CriterionDefinition]
    recommended_source_sections: list[str] = Field(default_factory=list)
    prompt_guidance: list[str] = Field(default_factory=list)
    review_focus: dict[str, str] = Field(default_factory=dict)

    @property
    def criterion_ids(self) -> list[str]:
        return [criterion.criterion_id for criterion in self.criteria]


class RubricDefinition(BaseModel):
    rubric_id: str
    version: str
    language: str
    description: str
    score_scale: dict[str, str]
    groups: list[GroupDefinition]
    allowed_scores: list[str] = Field(default_factory=lambda: ["0", "1", "2", "N/A"])
    reviewer_mindset: list[str] = Field(default_factory=list)
    prompt_rules: list[str] = Field(default_factory=list)

    def group_by_id(self, group_id: str) -> GroupDefinition:
        for group in self.groups:
            if group.group_id == group_id:
                return group
        raise KeyError(f"Unknown rubric group_id: {group_id}")
