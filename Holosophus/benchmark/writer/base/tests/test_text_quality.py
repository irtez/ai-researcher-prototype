"""Tests for shared text quality metrics."""

from writer.base.content import MetricValuePresence
from writer.base.text_quality import NotationConsistency, SpellingErrors


def test_metric_value_presence_passes_on_gold(gold_paper):
    result = MetricValuePresence().evaluate(gold_paper)[0]
    assert result.name == "metric_value_presence"
    assert result.value >= 0.75


def test_metric_value_presence_detects_missing_numbers(paper_metric_mentions_without_values):
    result = MetricValuePresence().evaluate(paper_metric_mentions_without_values)[0]
    assert result.value < 1.0
    assert result.details["without_value_samples"]


def test_notation_consistency_detects_variant_mixing(paper_inconsistent_notation):
    result = NotationConsistency().evaluate(paper_inconsistent_notation)[0]
    assert result.name == "notation_consistency"
    assert result.value > 0


def test_spelling_errors_runs_on_gold(gold_paper):
    result = SpellingErrors().evaluate(gold_paper)[0]
    assert result.name == "spelling_errors"
    assert result.value >= 0

