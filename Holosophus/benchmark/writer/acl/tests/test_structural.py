"""Tests for ACL structural metrics."""

from writer.acl.structural import (
    RequiredSectionNonEmptiness,
    RequiredSectionPresence,
    SectionLengthAdequacy,
    SectionOrderValidity,
)


def test_acl_required_section_presence_passes_on_gold(gold_paper):
    result = RequiredSectionPresence().evaluate(gold_paper)[0]
    assert result.value == 1.0


def test_acl_required_section_presence_detects_missing_limitations(paper_missing_limitations):
    result = RequiredSectionPresence().evaluate(paper_missing_limitations)[0]
    assert result.value < 1.0
    assert "limitations" in result.details["missing"]


def test_acl_required_section_non_emptiness_detects_stub(paper_empty_limitations):
    result = RequiredSectionNonEmptiness().evaluate(paper_empty_limitations)[0]
    assert result.value < 1.0
    assert "limitations" in result.details["empty_stubs"]


def test_acl_section_order_validity_detects_swapped_sections(paper_wrong_section_order):
    result = SectionOrderValidity().evaluate(paper_wrong_section_order)[0]
    assert result.value == 0.0


def test_acl_section_length_adequacy_passes_on_gold(gold_paper):
    result = SectionLengthAdequacy().evaluate(gold_paper)[0]
    assert result.value >= 0.8

