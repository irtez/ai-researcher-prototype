"""Canonical writer-specific grouped-rubric implementation.

This package now owns the actual grouped-rubric judge logic used by
``reviewer_patches``. The old ``benchmark/writer/base/llm_judge`` package is
kept only as a compatibility layer on top of these modules.
"""

from .aggregation import compute_module_score_details, compute_module_scores, normalize_scores
from .definitions import CriterionDefinition, GroupDefinition, RubricDefinition
from .executor import openrouter_group_caller, run_grouped_rubric_review
from .loader import load_grouped_rubric, select_groups_for_paper_type
from .models import CriterionScore, GroupedRubricReviewResponse, RubricGroupResult
from .related_work import enrich_with_related_work, extract_keywords_openrouter, extract_keywords_regex

__all__ = [
    "CriterionDefinition",
    "CriterionScore",
    "GroupDefinition",
    "GroupedRubricReviewResponse",
    "RubricDefinition",
    "RubricGroupResult",
    "compute_module_score_details",
    "compute_module_scores",
    "enrich_with_related_work",
    "extract_keywords_openrouter",
    "extract_keywords_regex",
    "load_grouped_rubric",
    "normalize_scores",
    "openrouter_group_caller",
    "run_grouped_rubric_review",
    "select_groups_for_paper_type",
]
