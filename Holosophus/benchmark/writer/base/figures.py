"""Figure and table metrics shared across writer profiles."""

from __future__ import annotations

import re

from .base import BaseMetric, FailType, MetricResult, PaperPackage

_FLOAT_LABEL_PREFIX = re.compile(r"^(fig|tab|figure|table)[:_.-]", re.IGNORECASE)


def is_float_label(label: str) -> bool:
    return bool(_FLOAT_LABEL_PREFIX.match(label))


class FigureTableReferenceCoverage(BaseMetric):
    """Ratio of figure/table labels that are referenced in the text."""

    def evaluate(self, pkg: PaperPackage) -> list[MetricResult]:
        float_labels: list[str] = []
        for float_env in pkg.floats:
            float_labels.extend(float_env.labels)

        if not float_labels:
            return [
                MetricResult(
                    name="figure_table_reference_coverage",
                    value=1.0,
                    fail_type=FailType.SOFT,
                    details={"message": "no float labels found"},
                )
            ]

        ref_set = set(pkg.refs_used)
        referenced = [label for label in float_labels if label in ref_set]
        unreferenced = [label for label in float_labels if label not in ref_set]
        return [
            MetricResult(
                name="figure_table_reference_coverage",
                value=len(referenced) / len(float_labels),
                fail_type=FailType.SOFT,
                details={
                    "referenced": referenced,
                    "unreferenced": unreferenced,
                    "total_float_labels": len(float_labels),
                },
            )
        ]


class DanglingFigureTableReferenceCount(BaseMetric):
    """Count references that point to missing figure/table labels."""

    def evaluate(self, pkg: PaperPackage) -> list[MetricResult]:
        defined_labels = set(pkg.labels_defined)
        float_refs = [ref for ref in pkg.refs_used if is_float_label(ref)]
        dangling = [ref for ref in float_refs if ref not in defined_labels]
        return [
            MetricResult(
                name="dangling_figure_table_reference_count",
                value=len(dangling),
                fail_type=FailType.HARD,
                details={"dangling_refs": dangling, "total_float_refs": len(float_refs)},
            )
        ]


class CaptionCoverage(BaseMetric):
    """Ratio of float environments that contain captions."""

    def evaluate(self, pkg: PaperPackage) -> list[MetricResult]:
        if not pkg.floats:
            return [
                MetricResult(
                    name="caption_coverage",
                    value=1.0,
                    fail_type=FailType.HARD,
                    details={"message": "no figure/table environments found"},
                )
            ]

        with_caption = [float_env for float_env in pkg.floats if float_env.has_caption]
        without_caption = [float_env for float_env in pkg.floats if not float_env.has_caption]
        return [
            MetricResult(
                name="caption_coverage",
                value=len(with_caption) / len(pkg.floats),
                fail_type=FailType.HARD,
                details={
                    "with_caption": len(with_caption),
                    "without_caption_labels": [
                        label
                        for float_env in without_caption
                        for label in (float_env.labels or [f"unlabeled_{float_env.kind}"])
                    ],
                    "total_floats": len(pkg.floats),
                },
            )
        ]


class NumberingConsistency(BaseMetric):
    """Count duplicate labels and related numbering inconsistencies."""

    def evaluate(self, pkg: PaperPackage) -> list[MetricResult]:
        seen: dict[str, int] = {}
        for label in pkg.labels_defined:
            seen[label] = seen.get(label, 0) + 1

        duplicates = {label: count for label, count in seen.items() if count > 1}
        issues = [
            f"duplicate label '{label}' ({count} times)"
            for label, count in duplicates.items()
        ]
        return [
            MetricResult(
                name="numbering_consistency",
                value=len(issues),
                fail_type=FailType.SOFT,
                details={"issues": issues, "duplicate_labels": duplicates},
            )
        ]

