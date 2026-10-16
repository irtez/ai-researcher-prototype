"""Verify that batch review functions propagate review_mode correctly."""
import inspect

import pytest

from models import REVIEW_MODE_GENERAL, REVIEW_MODE_WRITER_SPECIFIC_JUDGE
from batch_review import review_with_repeats, review_dataset, ensemble_review


class TestBatchReviewModeSignatures:
    """Ensure batch functions accept review_mode without breaking existing callers."""

    def test_review_with_repeats_has_review_mode_param(self):
        sig = inspect.signature(review_with_repeats)
        assert "review_mode" in sig.parameters

    def test_review_with_repeats_defaults_to_general(self):
        sig = inspect.signature(review_with_repeats)
        assert sig.parameters["review_mode"].default == REVIEW_MODE_GENERAL

    def test_review_dataset_has_review_mode_param(self):
        sig = inspect.signature(review_dataset)
        assert "review_mode" in sig.parameters

    def test_review_dataset_defaults_to_general(self):
        sig = inspect.signature(review_dataset)
        assert sig.parameters["review_mode"].default == REVIEW_MODE_GENERAL
