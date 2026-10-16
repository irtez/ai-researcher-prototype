"""Tests for the generic writer/base structural bundle."""

from writer.base.structural import (
    RequiredSectionNonEmptiness,
    RequiredSectionPresence,
    SectionLengthAdequacy,
    SectionOrderValidity,
)


def test_base_required_section_presence_passes_on_gold(gold_paper):
    result = RequiredSectionPresence().evaluate(gold_paper)[0]
    assert result.value == 1.0


def test_base_required_section_non_emptiness_detects_stub(paper_empty_limitations):
    result = RequiredSectionNonEmptiness().evaluate(paper_empty_limitations)[0]
    assert result.value < 1.0


def test_base_section_order_validity_detects_wrong_order(paper_wrong_section_order):
    result = SectionOrderValidity().evaluate(paper_wrong_section_order)[0]
    assert result.value == 0.0


def test_base_section_length_adequacy_passes_on_gold(gold_paper):
    result = SectionLengthAdequacy().evaluate(gold_paper)[0]
    assert result.value >= 0.8

