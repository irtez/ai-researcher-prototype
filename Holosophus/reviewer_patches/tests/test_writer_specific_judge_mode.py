"""Tests for the writer_specific_judge routing within review_pdf_paper.

These are pure signature / routing tests — no LLM calls are made.
The integration smoke (actual PDF → LLM) is handled by the quickstart.md manual
steps and the benchmark CI.
"""
import inspect

import pytest

from models import REVIEW_MODE_GENERAL, REVIEW_MODE_WRITER_SPECIFIC_JUDGE
from review import review_pdf_paper, review_pdf_paper_by_url


class TestReviewPdfPaperSignature:
    def test_review_mode_param_exists(self):
        sig = inspect.signature(review_pdf_paper)
        assert "review_mode" in sig.parameters

    def test_review_mode_defaults_to_general(self):
        sig = inspect.signature(review_pdf_paper)
        assert sig.parameters["review_mode"].default == REVIEW_MODE_GENERAL

    def test_paper_type_param_exists(self):
        sig = inspect.signature(review_pdf_paper)
        assert "paper_type" in sig.parameters

    def test_paper_id_param_exists(self):
        sig = inspect.signature(review_pdf_paper)
        assert "paper_id" in sig.parameters

    def test_rubric_path_param_exists(self):
        sig = inspect.signature(review_pdf_paper)
        assert "rubric_path" in sig.parameters

    def test_review_pdf_paper_by_url_has_review_mode(self):
        sig = inspect.signature(review_pdf_paper_by_url)
        assert "review_mode" in sig.parameters


class TestWriterSpecificModeRouting:
    """Routing unit test using validate_review_mode."""

    def test_validate_accepts_writer_specific(self):
        from models import validate_review_mode
        assert validate_review_mode(REVIEW_MODE_WRITER_SPECIFIC_JUDGE) == REVIEW_MODE_WRITER_SPECIFIC_JUDGE

    def test_validate_accepts_general(self):
        from models import validate_review_mode
        assert validate_review_mode(REVIEW_MODE_GENERAL) == REVIEW_MODE_GENERAL

    def test_validate_rejects_unknown(self):
        from models import validate_review_mode
        with pytest.raises(ValueError, match="Unknown review_mode"):
            validate_review_mode("nonexistent_mode")
