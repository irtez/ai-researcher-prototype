"""Tests for the generic writer/base content bundle."""

from writer.base.content import (
    AbstractSlotCoverage,
    ContributionStatementPresence,
    ExperimentSlotCoverage,
    ReproducibilitySlotCoverage,
)


def test_base_abstract_slot_coverage_passes_on_gold(gold_paper):
    result = AbstractSlotCoverage().evaluate(gold_paper)[0]
    assert result.value == 1.0


def test_base_contribution_statement_detects_missing_signal(paper_no_contributions):
    result = ContributionStatementPresence().evaluate(paper_no_contributions)[0]
    assert result.value == 0.0


def test_base_experiment_slot_coverage_passes_on_gold(gold_paper):
    result = ExperimentSlotCoverage().evaluate(gold_paper)[0]
    assert result.value == 1.0


def test_base_reproducibility_slot_coverage_passes_on_gold(gold_paper):
    result = ReproducibilitySlotCoverage().evaluate(gold_paper)[0]
    assert result.value >= 0.75

