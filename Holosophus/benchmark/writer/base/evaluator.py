"""Reusable evaluator for writer benchmark profiles."""

from __future__ import annotations

import json
from pathlib import Path

from .base import BaseMetric, FailType, MetricResult, PaperPackage
from .citation import BibliographyUsageCoverage, CitationIntegrity
from .content import (
    AbstractSlotCoverage,
    ContributionStatementPresence,
    ExperimentSlotCoverage,
    MetricValuePresence,
    ReproducibilitySlotCoverage,
)
from .figures import (
    CaptionCoverage,
    DanglingFigureTableReferenceCount,
    FigureTableReferenceCoverage,
    NumberingConsistency,
)
from .structural import (
    RequiredSectionNonEmptiness,
    RequiredSectionPresence,
    SectionLengthAdequacy,
    SectionOrderValidity,
)
from .text_quality import GrammarErrors, NotationConsistency, SpellingErrors

ALL_METRICS: list[type[BaseMetric]] = [
    SpellingErrors,
    GrammarErrors,
    RequiredSectionPresence,
    RequiredSectionNonEmptiness,
    SectionOrderValidity,
    SectionLengthAdequacy,
    CitationIntegrity,
    BibliographyUsageCoverage,
    FigureTableReferenceCoverage,
    DanglingFigureTableReferenceCount,
    CaptionCoverage,
    NumberingConsistency,
    AbstractSlotCoverage,
    ContributionStatementPresence,
    ExperimentSlotCoverage,
    ReproducibilitySlotCoverage,
    MetricValuePresence,
    NotationConsistency,
]


class Evaluator:
    """Run a configurable list of metrics on a PaperPackage."""

    def __init__(self, metric_classes: list[type[BaseMetric]] | None = None) -> None:
        self.metric_classes = metric_classes or ALL_METRICS
        self.metrics = [metric_class() for metric_class in self.metric_classes]

    def evaluate(self, pkg: PaperPackage) -> list[MetricResult]:
        results: list[MetricResult] = []
        for metric in self.metrics:
            results.extend(metric.evaluate(pkg))
        return results

    def evaluate_directory(self, path: str | Path) -> list[MetricResult]:
        return self.evaluate(PaperPackage.from_directory(path))

    @staticmethod
    def report(results: list[MetricResult]) -> dict:
        hard_results = [result for result in results if result.fail_type == FailType.HARD]
        soft_results = [result for result in results if result.fail_type == FailType.SOFT]
        hard_passed = sum(result.passed for result in hard_results)
        soft_passed = sum(result.passed for result in soft_results)
        return {
            "summary": {
                "total_metrics": len(results),
                "hard_passed": f"{hard_passed}/{len(hard_results)}",
                "soft_passed": f"{soft_passed}/{len(soft_results)}",
            },
            "results": [
                {
                    "name": result.name,
                    "value": result.value,
                    "fail_type": result.fail_type.value,
                    "passed": result.passed,
                    "details": result.details,
                }
                for result in results
            ],
        }

    @staticmethod
    def print_report(results: list[MetricResult]) -> None:
        print(f"\n{'=' * 70}")
        print("WRITER BENCHMARK REPORT")
        print(f"{'=' * 70}\n")

        hard_results = [result for result in results if result.fail_type == FailType.HARD]
        soft_results = [result for result in results if result.fail_type == FailType.SOFT]
        for label, group in (("HARD metrics", hard_results), ("SOFT metrics", soft_results)):
            print(f"--- {label} ---")
            for result in group:
                icon = "+" if result.passed else "-"
                status = "PASS" if result.passed else "FAIL"
                value = f"{result.value:.2%}" if isinstance(result.value, float) else str(result.value)
                print(f"  [{icon}] {result.name:<45s} {value:>8s}  ({status})")
            print()

        hard_passed = sum(result.passed for result in hard_results)
        soft_passed = sum(result.passed for result in soft_results)
        print(f"Hard: {hard_passed}/{len(hard_results)}  |  Soft: {soft_passed}/{len(soft_results)}")
        print(f"{'=' * 70}\n")

    @staticmethod
    def to_json(results: list[MetricResult]) -> str:
        return json.dumps(Evaluator.report(results), indent=2, ensure_ascii=False)

