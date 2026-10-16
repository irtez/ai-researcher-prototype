"""Parameterized content metrics for writer benchmark profiles."""

from __future__ import annotations

import re

from .base import BaseMetric, FailType, MetricResult, PaperPackage
from .defaults import (
    BASE_ABSTRACT_POLICY,
    BASE_CONTRIBUTION_POLICY,
    BASE_EXPERIMENT_POLICY,
    BASE_REPRODUCIBILITY_POLICY,
    BASE_SECTION_POLICY,
)
from .profiles import ContributionPolicy, SlotCoveragePolicy


def collect_section_text(
    pkg: PaperPackage,
    target_sections: tuple[str, ...],
    aliases: dict[str, str],
) -> str:
    if not target_sections:
        return pkg.raw_tex

    chunks: list[str] = []
    for section in pkg.sections:
        canonical = aliases.get(section.normalized, section.normalized)
        if canonical in target_sections:
            chunks.append(section.body)
    return " ".join(chunks).strip()


class AbstractSlotCoverage(BaseMetric):
    """Ratio of expected abstract slots present in the abstract text."""

    def __init__(self, policy: SlotCoveragePolicy | None = None) -> None:
        self.policy = policy or BASE_ABSTRACT_POLICY

    def evaluate(self, pkg: PaperPackage) -> list[MetricResult]:
        abstract = pkg.abstract_text.lower()
        if not abstract.strip():
            return [
                MetricResult(
                    name=self.policy.metric_name,
                    value=0.0,
                    fail_type=FailType.SOFT,
                    details={"message": self.policy.empty_message or "no abstract found"},
                )
            ]

        found = {
            slot: any(re.search(pattern, abstract, re.IGNORECASE) for pattern in patterns)
            for slot, patterns in self.policy.slots.items()
        }
        ratio = sum(found.values()) / len(self.policy.slots) if self.policy.slots else 1.0
        return [
            MetricResult(
                name=self.policy.metric_name,
                value=ratio,
                fail_type=FailType.SOFT,
                details={"slots": found},
            )
        ]


class SectionSlotCoverage(BaseMetric):
    """Ratio of expected slots found in a subset of sections."""

    def __init__(
        self,
        policy: SlotCoveragePolicy,
        section_aliases: dict[str, str] | None = None,
    ) -> None:
        self.policy = policy
        self.section_aliases = section_aliases or BASE_SECTION_POLICY.aliases

    def evaluate(self, pkg: PaperPackage) -> list[MetricResult]:
        text = collect_section_text(pkg, self.policy.target_sections, self.section_aliases)
        if not text.strip():
            return [
                MetricResult(
                    name=self.policy.metric_name,
                    value=0.0,
                    fail_type=FailType.SOFT,
                    details={"message": self.policy.empty_message or "no target sections found"},
                )
            ]

        found = {
            slot: any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)
            for slot, patterns in self.policy.slots.items()
        }
        ratio = sum(found.values()) / len(self.policy.slots) if self.policy.slots else 1.0
        return [
            MetricResult(
                name=self.policy.metric_name,
                value=ratio,
                fail_type=FailType.SOFT,
                details={"slots": found},
            )
        ]


class ContributionStatementPresence(BaseMetric):
    """Binary metric for explicit contribution statements."""

    def __init__(
        self,
        policy: ContributionPolicy | None = None,
        section_aliases: dict[str, str] | None = None,
    ) -> None:
        self.policy = policy or BASE_CONTRIBUTION_POLICY
        self.section_aliases = section_aliases or BASE_SECTION_POLICY.aliases

    def evaluate(self, pkg: PaperPackage) -> list[MetricResult]:
        search_text = collect_section_text(pkg, self.policy.target_sections, self.section_aliases)
        searched_in = ",".join(self.policy.target_sections)
        if not search_text.strip():
            search_text = pkg.raw_tex
            searched_in = "full_tex"

        found = any(
            re.search(pattern, search_text, re.IGNORECASE)
            for pattern in self.policy.patterns
        )
        return [
            MetricResult(
                name=self.policy.metric_name,
                value=1.0 if found else 0.0,
                fail_type=FailType.SOFT,
                details={"found": found, "searched_in": searched_in},
            )
        ]


class MetricValuePresence(BaseMetric):
    """Ratio of evaluation-metric mentions that include numeric values nearby."""

    METRIC_NAMES_RE = re.compile(
        r"\b(accuracy|precision|recall|f[1-9][\s-]?score|bleu|rouge|"
        r"perplexity|auc|mAP|map|mrr|ndcg|exact\s+match|em|pass@k)\b",
        re.IGNORECASE,
    )
    NUMERIC_NEARBY_RE = re.compile(r"\d+(?:\.\d+)?(?:\s*%)?")

    def __init__(
        self,
        metric_name: str = "metric_value_presence",
        target_sections: tuple[str, ...] = ("experiments", "results"),
        section_aliases: dict[str, str] | None = None,
    ) -> None:
        self.metric_name = metric_name
        self.target_sections = target_sections
        self.section_aliases = section_aliases or {}

    def evaluate(self, pkg: PaperPackage) -> list[MetricResult]:
        text = collect_section_text(pkg, self.target_sections, self.section_aliases)
        if not text.strip():
            text = pkg.raw_tex

        mentions = list(self.METRIC_NAMES_RE.finditer(text))
        if not mentions:
            return [
                MetricResult(
                    name=self.metric_name,
                    value=1.0,
                    fail_type=FailType.SOFT,
                    details={"message": "no evaluation metric mentions found"},
                )
            ]

        with_value = 0
        without_value: list[str] = []
        for mention in mentions:
            context = text[max(0, mention.start() - 40) : mention.end() + 40]
            if self.NUMERIC_NEARBY_RE.search(context):
                with_value += 1
            else:
                without_value.append(mention.group(0))

        ratio = with_value / len(mentions)
        return [
            MetricResult(
                name=self.metric_name,
                value=ratio,
                fail_type=FailType.SOFT,
                details={
                    "with_value": with_value,
                    "without_value_samples": without_value[:10],
                    "total_mentions": len(mentions),
                },
            )
        ]


class ExperimentSlotCoverage(SectionSlotCoverage):
    def __init__(self) -> None:
        super().__init__(
            policy=BASE_EXPERIMENT_POLICY,
            section_aliases=BASE_SECTION_POLICY.aliases,
        )


class ReproducibilitySlotCoverage(SectionSlotCoverage):
    def __init__(self) -> None:
        super().__init__(
            policy=BASE_REPRODUCIBILITY_POLICY,
            section_aliases=BASE_SECTION_POLICY.aliases,
        )


__all__ = [
    "AbstractSlotCoverage",
    "ContributionStatementPresence",
    "ExperimentSlotCoverage",
    "MetricValuePresence",
    "ReproducibilitySlotCoverage",
    "SectionSlotCoverage",
    "collect_section_text",
]

