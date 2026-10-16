"""Tests for shared citation metrics."""

from writer.base.citation import BibliographyUsageCoverage, CitationIntegrity


def test_citation_integrity_passes_on_gold(gold_paper):
    result = CitationIntegrity().evaluate(gold_paper)[0]
    assert result.name == "citation_integrity"
    assert result.value == 1.0
    assert result.passed


def test_citation_integrity_detects_broken_keys(paper_broken_citations):
    result = CitationIntegrity().evaluate(paper_broken_citations)[0]
    assert result.value < 1.0
    assert len(result.details["unresolved_keys"]) == 2


def test_bibliography_usage_coverage_detects_unused_entries(paper_unused_bib_entries):
    result = BibliographyUsageCoverage().evaluate(paper_unused_bib_entries)[0]
    assert result.name == "bibliography_usage_coverage"
    assert result.value < 1.0
    assert "unused_entry_2025" in result.details["unused_keys"]

