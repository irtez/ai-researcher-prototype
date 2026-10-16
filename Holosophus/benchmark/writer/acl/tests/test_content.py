"""Tests for ACL-specific content metrics."""

from writer.acl.content import (
    AbstractSlotCoverage,
    ContributionStatementPresence,
    ExperimentSlotCoverage,
    ReproducibilitySlotCoverage,
)


def test_acl_abstract_slot_coverage_passes_on_gold(gold_paper):
    result = AbstractSlotCoverage().evaluate(gold_paper)[0]
    assert result.value == 1.0


def test_acl_abstract_slot_coverage_handles_missing_abstract(paper_missing_abstract):
    result = AbstractSlotCoverage().evaluate(paper_missing_abstract)[0]
    assert result.value == 0.0


def test_acl_contribution_statement_detects_missing_signal(paper_no_contributions):
    result = ContributionStatementPresence().evaluate(paper_no_contributions)[0]
    assert result.value == 0.0


def test_acl_experiment_slot_coverage_passes_on_gold(gold_paper):
    result = ExperimentSlotCoverage().evaluate(gold_paper)[0]
    assert result.value == 1.0


def test_acl_reproducibility_slot_coverage_passes_on_gold(gold_paper):
    result = ReproducibilitySlotCoverage().evaluate(gold_paper)[0]
    assert result.value >= 0.75

