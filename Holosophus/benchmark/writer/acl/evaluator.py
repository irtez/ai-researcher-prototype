"""ACL writer benchmark evaluator and CLI."""

from __future__ import annotations

import json
import sys

from writer.acl.citation import BibliographyUsageCoverage, CitationIntegrity
from writer.acl.content import (
    AbstractSlotCoverage,
    ContributionStatementPresence,
    ExperimentSlotCoverage,
    MetricValuePresence,
    ReproducibilitySlotCoverage,
)
from writer.acl.figures import (
    CaptionCoverage,
    DanglingFigureTableReferenceCount,
    FigureTableReferenceCoverage,
    NumberingConsistency,
)
from writer.acl.formatting import (
    ACLAbstractLengthCompliance,
    ACLAnonymizationCompliance,
    ACLFormatCompliance,
    ACLPageLimitCompliance,
    ACLResponsibleNLPChecklistCompliance,
    ACLReviewVersionCompliance,
)
from writer.acl.structural import (
    RequiredSectionNonEmptiness,
    RequiredSectionPresence,
    SectionLengthAdequacy,
    SectionOrderValidity,
)
from writer.base.evaluator import Evaluator as BaseEvaluator
from writer.acl.text_quality import GrammarErrors, NotationConsistency, SpellingErrors

ALL_METRICS = [
    SpellingErrors,
    GrammarErrors,
    RequiredSectionPresence,
    RequiredSectionNonEmptiness,
    SectionOrderValidity,
    AbstractSlotCoverage,
    ContributionStatementPresence,
    ExperimentSlotCoverage,
    ReproducibilitySlotCoverage,
    CitationIntegrity,
    BibliographyUsageCoverage,
    FigureTableReferenceCoverage,
    DanglingFigureTableReferenceCount,
    NumberingConsistency,
    CaptionCoverage,
    MetricValuePresence,
    SectionLengthAdequacy,
    NotationConsistency,
    ACLPageLimitCompliance,
    ACLAbstractLengthCompliance,
    ACLFormatCompliance,
    ACLReviewVersionCompliance,
    ACLAnonymizationCompliance,
    ACLResponsibleNLPChecklistCompliance,
]


class Evaluator(BaseEvaluator):
    def __init__(self, metric_classes=None) -> None:
        super().__init__(metric_classes=metric_classes or ALL_METRICS)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m writer.acl.evaluator <paper_directory> [--json]")
        sys.exit(1)

    paper_dir = sys.argv[1]
    as_json = "--json" in sys.argv
    evaluator = Evaluator()
    results = evaluator.evaluate_directory(paper_dir)

    if as_json:
        print(json.dumps(evaluator.report(results), indent=2, ensure_ascii=False))
    else:
        evaluator.print_report(results)


if __name__ == "__main__":
    main()

