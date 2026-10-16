from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Review mode constants
# ---------------------------------------------------------------------------

REVIEW_MODE_GENERAL = "general"
REVIEW_MODE_WRITER_SPECIFIC_JUDGE = "writer_specific_judge"

ReviewMode = Literal["general", "writer_specific_judge"]

# Deferred import to avoid circular dependency — GroupedRubricReviewResponse is
# exported from writer_specific_judge, which itself imports from llm_judge.
# Direct import here would create a cycle since review.py imports both models
# and writer_specific_judge.  Callers should import GroupedRubricReviewResponse
# from ``reviewer_patches.writer_specific_judge`` or from ``reviewer_patches``
# top-level __init__.py (which defers appropriately).

SUPPORTED_REVIEW_MODES: tuple[str, ...] = (
    REVIEW_MODE_GENERAL,
    REVIEW_MODE_WRITER_SPECIFIC_JUDGE,
)


def validate_review_mode(mode: str) -> ReviewMode:
    """Validate *mode* is a supported ReviewMode and return it typed.

    Raises :class:`ValueError` with a descriptive message for unknown modes.
    """
    if mode not in SUPPORTED_REVIEW_MODES:
        raise ValueError(
            f"Unknown review_mode {mode!r}. "
            f"Supported modes: {sorted(SUPPORTED_REVIEW_MODES)}"
        )
    return mode  # type: ignore[return-value]

class AspectItem(BaseModel):
    strengths: List[str] = Field(default_factory=list)
    weaknesses: List[str] = Field(default_factory=list)
    reasoning: str = ""
    score: int = Field(ge=1, le=4)

class ReasoningItem(BaseModel):
    reasoning: str = ""
    score: int = Field(ge=1, le=10)

class AnalysisJson(BaseModel):
    """
    Structured analysis output from section_parser.
    """
    goals: List[str] = Field(default_factory=list)
    methods: List[str] = Field(default_factory=list)
    datasets: List[str] = Field(default_factory=list)
    claims: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    key_contributions: List[str] = Field(default_factory=list)
    class Config:
        extra = "forbid"


class ArgumentJson(BaseModel):
    """
    Structured argument output from a3pr_pipeline.
    Strict schema for Azure JSON mode compatibility.
    """
    analysis_summary: str = ""
    argument_structure: List[str] = Field(default_factory=list)
    assessment_summary: str = ""
    key_findings: List[str] = Field(default_factory=list)
    class Config:
        extra = "forbid"  # additionalProperties: false


class ExtendedReviewResponse(BaseModel):
    """
    Enhanced review response with 7 detailed criteria (Tier 1, Item 2).
    
    Criteria:
    1. Originality (1-4)
    2. Importance of research question (1-4)
    3. Claim support (1-4)
    4. Experimental soundness (1-4)
    5. Clarity of writing (1-4)
    6. Value to research community (1-4)
    7. Contextualization vs prior work (1-4)
    """
    summary: str = ""
    
    originality: AspectItem = Field(default_factory=AspectItem)
    importance: AspectItem = Field(default_factory=AspectItem)
    claim_support: AspectItem = Field(default_factory=AspectItem)
    experimental_soundness: AspectItem = Field(default_factory=AspectItem)
    clarity: AspectItem = Field(default_factory=AspectItem)
    community_value: AspectItem = Field(default_factory=AspectItem)
    contextualization: AspectItem = Field(default_factory=AspectItem)
    
    questions: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    
    overall_score_raw: float = 0.0
    overall: ReasoningItem = Field(default_factory=ReasoningItem)
    
    confidence: ReasoningItem = Field(default_factory=ReasoningItem)
    format_issues: List[str] = Field(default_factory=list)
    
    analysis_json: Optional[AnalysisJson] = None
    argument_json: Optional[ArgumentJson] = None
    
    class Config:
        extra = "forbid"


WEIGHTS = {
    "originality": 0.20,
    "importance": 0.15,
    "claim_support": 0.18,
    "experimental_soundness": 0.17,
    "clarity": 0.10,
    "community_value": 0.12,
    "contextualization": 0.08,
}


def compute_overall_score(scores: dict) -> float:
    """
    Compute weighted overall score from 7 criteria (each 1-4).
    Returns weighted float in [1, 4].
    """
    weighted_sum = sum(scores[k] * WEIGHTS[k] for k in WEIGHTS if k in scores)
    total_weight = sum(WEIGHTS.values())
    return weighted_sum / total_weight


def map_to_overall_score(weighted_score: float) -> int:
    """Map weighted criteria score (1-4 range) to 1-10 overall scale."""
    return max(1, min(10, round(1 + (weighted_score - 1) * 3)))


ReviewResponse = ExtendedReviewResponse
