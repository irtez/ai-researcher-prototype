"""Smoke tests for ACL evaluator wiring."""

from writer.acl.metrics.evaluator import ALL_METRICS, Evaluator


def test_acl_evaluator_runs_full_metric_bundle(gold_paper):
    results = Evaluator().evaluate(gold_paper)
    metric_names = {result.name for result in results}
    assert len(results) == len(ALL_METRICS)
    assert "acl_page_limit_compliance" in metric_names
    assert "acl_anonymization_compliance" in metric_names
    assert "required_section_presence" in metric_names

