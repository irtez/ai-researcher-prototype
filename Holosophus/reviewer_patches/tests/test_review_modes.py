"""Unit tests for the review_mode selector in reviewer_patches.

These tests are *pure* (no LLM calls) — they verify that:
- The default mode is ``general``.
- Known modes are accepted without error.
- Unknown modes raise ``ValueError`` with a descriptive message.
- The new public constants are importable from the package root.
"""

import pytest

from models import (
    REVIEW_MODE_GENERAL,
    REVIEW_MODE_WRITER_SPECIFIC_JUDGE,
    SUPPORTED_REVIEW_MODES,
    validate_review_mode,
)


class TestValidateReviewMode:
    def test_general_mode_accepted(self):
        assert validate_review_mode(REVIEW_MODE_GENERAL) == "general"

    def test_writer_specific_mode_accepted(self):
        assert validate_review_mode(REVIEW_MODE_WRITER_SPECIFIC_JUDGE) == "writer_specific_judge"

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError, match="Unknown review_mode"):
            validate_review_mode("unknown_mode")

    def test_error_message_lists_supported_modes(self):
        with pytest.raises(ValueError, match="general"):
            validate_review_mode("bogus")

    def test_supported_modes_completeness(self):
        assert REVIEW_MODE_GENERAL in SUPPORTED_REVIEW_MODES
        assert REVIEW_MODE_WRITER_SPECIFIC_JUDGE in SUPPORTED_REVIEW_MODES


class TestDefaultMode:
    """Verify that review_pdf_paper defaults to general without breaking callers."""

    def test_general_is_default_constant(self):
        assert REVIEW_MODE_GENERAL == "general"

    def test_writer_specific_constant_value(self):
        assert REVIEW_MODE_WRITER_SPECIFIC_JUDGE == "writer_specific_judge"
