"""Import smoke-test: verify that reviewer_patches/writer_specific_judge exports
the full canonical surface and that the old llm_judge __init__ surface is
still importable (compatibility).
"""
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# writer_specific_judge canonical surface
# ---------------------------------------------------------------------------

def test_writer_specific_judge_imports_from_reviewer_patches():
    from writer_specific_judge import (
        GroupedRubricReviewResponse,
        RubricGroupResult,
        CriterionScore,
        run_grouped_rubric_review,
        compute_module_scores,
        load_grouped_rubric,
    )
    assert GroupedRubricReviewResponse is not None
    assert run_grouped_rubric_review is not None


def test_canonical_modules_live_under_reviewer_patches():
    import writer_specific_judge.executor as executor
    import writer_specific_judge.loader as loader
    import writer_specific_judge.prompts as prompts

    assert "reviewer_patches/writer_specific_judge/executor.py" in executor.__file__
    assert "reviewer_patches/writer_specific_judge/loader.py" in loader.__file__
    assert "reviewer_patches/writer_specific_judge/prompts.py" in prompts.__file__


def test_reviewer_patches_top_level_exports():
    """Top-level __init__.py must re-export the canonical mode surface."""
    import models
    assert hasattr(models, "REVIEW_MODE_GENERAL")
    assert hasattr(models, "REVIEW_MODE_WRITER_SPECIFIC_JUDGE")
    assert hasattr(models, "validate_review_mode")


# ---------------------------------------------------------------------------
# llm_judge compatibility surface (benchmark tree must still work)
# ---------------------------------------------------------------------------

def test_llm_judge_compat_still_importable():
    """The old llm_judge path must remain importable for backward compat."""
    benchmark_root = Path(__file__).resolve().parents[2] / "benchmark"
    if str(benchmark_root) not in sys.path:
        sys.path.insert(0, str(benchmark_root))

    from writer.base.llm_judge import (
        GroupedRubricReviewResponse,
        run_grouped_rubric_review,
    )
    assert GroupedRubricReviewResponse is not None
    assert run_grouped_rubric_review is not None
