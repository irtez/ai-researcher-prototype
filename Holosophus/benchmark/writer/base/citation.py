"""Citation and bibliography metrics shared across writer profiles."""

from __future__ import annotations

from .base import BaseMetric, FailType, MetricResult, PaperPackage


class CitationIntegrity(BaseMetric):
    """Ratio of cite keys that resolve to bibliography entries."""

    def evaluate(self, pkg: PaperPackage) -> list[MetricResult]:
        if not pkg.cite_keys_used:
            return [
                MetricResult(
                    name="citation_integrity",
                    value=1.0,
                    fail_type=FailType.HARD,
                    details={"message": "no citations used"},
                )
            ]

        bib_set = set(pkg.bib_keys_defined)
        unique_cite = list(dict.fromkeys(pkg.cite_keys_used))
        resolved = [key for key in unique_cite if key in bib_set]
        unresolved = [key for key in unique_cite if key not in bib_set]
        return [
            MetricResult(
                name="citation_integrity",
                value=len(resolved) / len(unique_cite),
                fail_type=FailType.HARD,
                details={
                    "resolved": len(resolved),
                    "unresolved_keys": unresolved,
                    "total_unique_cite_keys": len(unique_cite),
                },
            )
        ]


class BibliographyUsageCoverage(BaseMetric):
    """Ratio of bibliography entries that are cited in the paper."""

    def evaluate(self, pkg: PaperPackage) -> list[MetricResult]:
        if not pkg.bib_keys_defined:
            return [
                MetricResult(
                    name="bibliography_usage_coverage",
                    value=1.0,
                    fail_type=FailType.SOFT,
                    details={"message": "no bibliography entries"},
                )
            ]

        cited_set = set(pkg.cite_keys_used)
        used = [key for key in pkg.bib_keys_defined if key in cited_set]
        unused = [key for key in pkg.bib_keys_defined if key not in cited_set]
        return [
            MetricResult(
                name="bibliography_usage_coverage",
                value=len(used) / len(pkg.bib_keys_defined),
                fail_type=FailType.SOFT,
                details={
                    "used": len(used),
                    "unused_keys": unused,
                    "total_bib_entries": len(pkg.bib_keys_defined),
                },
            )
        ]

