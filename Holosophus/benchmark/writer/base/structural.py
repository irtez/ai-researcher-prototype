"""Parameterized structural metrics shared across writer benchmark profiles."""

from __future__ import annotations

from .defaults import BASE_SECTION_POLICY
from .base import BaseMetric, FailType, MetricResult, PaperPackage, normalize_section_name
from .profiles import SectionPolicy


def resolve_section_alias(normalized: str, policy: SectionPolicy) -> str:
    return policy.aliases.get(normalized, normalized)


def build_section_body_map(pkg: PaperPackage, policy: SectionPolicy) -> dict[str, str]:
    section_map: dict[str, str] = {}
    for section in pkg.sections:
        canonical = resolve_section_alias(section.normalized, policy)
        if canonical not in section_map:
            section_map[canonical] = section.body
    return section_map


class RequiredSectionPresence(BaseMetric):
    """Ratio of profile-required sections that are present."""

    def __init__(
        self,
        policy: SectionPolicy | None = None,
        metric_name: str = "required_section_presence",
    ) -> None:
        self.policy = policy or BASE_SECTION_POLICY
        self.metric_name = metric_name

    def evaluate(self, pkg: PaperPackage) -> list[MetricResult]:
        found_normalized = {
            resolve_section_alias(section.normalized, self.policy)
            for section in pkg.sections
        }

        if pkg.abstract_text.strip():
            found_normalized.add("abstract")

        has_references = bool(pkg.bib_keys_defined) or "\\bibliography{" in pkg.raw_tex
        if has_references or "\\begin{thebibliography}" in pkg.raw_tex:
            found_normalized.add("references")

        present = [
            section
            for section in self.policy.required_sections
            if section in found_normalized
        ]
        missing = [
            section
            for section in self.policy.required_sections
            if section not in found_normalized
        ]
        ratio = len(present) / len(self.policy.required_sections) if self.policy.required_sections else 1.0
        return [
            MetricResult(
                name=self.metric_name,
                value=ratio,
                fail_type=FailType.HARD,
                details={"present": present, "missing": missing},
            )
        ]


class RequiredSectionNonEmptiness(BaseMetric):
    """Ratio of present required sections that are not stubs."""

    def __init__(
        self,
        policy: SectionPolicy | None = None,
        metric_name: str = "required_section_non_emptiness",
    ) -> None:
        self.policy = policy or BASE_SECTION_POLICY
        self.metric_name = metric_name

    def evaluate(self, pkg: PaperPackage) -> list[MetricResult]:
        section_map = build_section_body_map(pkg, self.policy)
        non_empty: list[str] = []
        empty_stubs: list[str] = []
        missing: list[str] = []

        for section_name in self.policy.required_sections:
            if section_name == "references":
                continue
            if section_name == "abstract":
                abstract_words = len(pkg.abstract_text.split())
                if not pkg.abstract_text.strip():
                    missing.append("abstract")
                elif abstract_words >= self.policy.min_section_words:
                    non_empty.append("abstract")
                else:
                    empty_stubs.append("abstract")
                continue

            body = section_map.get(section_name)
            if body is None:
                missing.append(section_name)
                continue

            if len(body.split()) >= self.policy.min_section_words:
                non_empty.append(section_name)
            else:
                empty_stubs.append(section_name)

        total_present = len(non_empty) + len(empty_stubs)
        ratio = len(non_empty) / total_present if total_present else 0.0
        return [
            MetricResult(
                name=self.metric_name,
                value=ratio,
                fail_type=FailType.HARD,
                details={
                    "non_empty": non_empty,
                    "empty_stubs": empty_stubs,
                    "missing": missing,
                },
            )
        ]


class SectionOrderValidity(BaseMetric):
    """Binary score for profile-specific section ordering."""

    def __init__(
        self,
        policy: SectionPolicy | None = None,
        metric_name: str = "section_order_validity",
    ) -> None:
        self.policy = policy or BASE_SECTION_POLICY
        self.metric_name = metric_name

    def evaluate(self, pkg: PaperPackage) -> list[MetricResult]:
        found_order: list[str] = []
        for section in pkg.sections:
            canonical = resolve_section_alias(section.normalized, self.policy)
            if canonical in self.policy.ordered_sections and canonical not in found_order:
                found_order.append(canonical)

        expected = [
            section
            for section in self.policy.ordered_sections
            if section in found_order
        ]
        valid = 1.0 if found_order == expected else 0.0
        return [
            MetricResult(
                name=self.metric_name,
                value=valid,
                fail_type=FailType.SOFT,
                details={"found_order": found_order, "expected_order": expected},
            )
        ]


class SectionLengthAdequacy(BaseMetric):
    """Ratio of relevant sections that meet the minimum word count."""

    def __init__(
        self,
        policy: SectionPolicy | None = None,
        metric_name: str = "section_length_adequacy",
    ) -> None:
        self.policy = policy or BASE_SECTION_POLICY
        self.metric_name = metric_name

    def evaluate(self, pkg: PaperPackage) -> list[MetricResult]:
        section_lengths: dict[str, int] = {}
        for section in pkg.sections:
            canonical = resolve_section_alias(section.normalized, self.policy)
            if canonical not in section_lengths:
                section_lengths[canonical] = len(section.body.split())

        checkable = [
            section
            for section in self.policy.ordered_sections
            if section not in {"references"} and section in section_lengths
        ]
        if not checkable and pkg.abstract_text.strip():
            checkable = ["abstract"]

        word_counts = {section: section_lengths[section] for section in checkable if section != "abstract"}
        if "abstract" in checkable:
            word_counts["abstract"] = len(pkg.abstract_text.split())

        if not word_counts:
            return [
                MetricResult(
                    name=self.metric_name,
                    value=0.0,
                    fail_type=FailType.SOFT,
                    details={"message": "no checkable sections found"},
                )
            ]

        adequate = [
            section
            for section, count in word_counts.items()
            if count >= self.policy.min_section_words
        ]
        too_short = [
            section
            for section, count in word_counts.items()
            if count < self.policy.min_section_words
        ]
        ratio = len(adequate) / len(word_counts)
        return [
            MetricResult(
                name=self.metric_name,
                value=ratio,
                fail_type=FailType.SOFT,
                details={
                    "adequate": adequate,
                    "too_short": too_short,
                    "word_counts": word_counts,
                },
            )
        ]


__all__ = [
    "RequiredSectionPresence",
    "RequiredSectionNonEmptiness",
    "SectionOrderValidity",
    "SectionLengthAdequacy",
    "build_section_body_map",
    "normalize_section_name",
    "resolve_section_alias",
]

