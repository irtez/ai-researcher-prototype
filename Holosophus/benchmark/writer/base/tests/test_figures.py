"""Tests for shared figure/table metrics."""

from writer.base.figures import (
    CaptionCoverage,
    DanglingFigureTableReferenceCount,
    FigureTableReferenceCoverage,
    NumberingConsistency,
)


def test_figure_reference_coverage_passes_on_gold(gold_paper):
    result = FigureTableReferenceCoverage().evaluate(gold_paper)[0]
    assert result.value == 1.0
    assert result.details["unreferenced"] == []


def test_caption_coverage_detects_missing_caption(paper_missing_captions):
    result = CaptionCoverage().evaluate(paper_missing_captions)[0]
    assert result.value < 1.0
    assert "fig:overview" in result.details["without_caption_labels"]


def test_dangling_refs_detected(paper_dangling_refs):
    result = DanglingFigureTableReferenceCount().evaluate(paper_dangling_refs)[0]
    assert result.value == 2


def test_numbering_consistency_detects_duplicate_labels(paper_duplicate_labels):
    result = NumberingConsistency().evaluate(paper_duplicate_labels)[0]
    assert result.value == 1
    assert result.details["duplicate_labels"]

