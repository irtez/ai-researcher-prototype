"""ACL-specific content metrics."""

from __future__ import annotations

from writer.acl.profile import (
    ACL_ABSTRACT_POLICY,
    ACL_CONTRIBUTION_POLICY,
    ACL_EXPERIMENT_POLICY,
    ACL_NOTATION_POLICY,
    ACL_REPRO_POLICY,
    ACL_SECTION_POLICY,
)
from writer.base.content import (
    AbstractSlotCoverage as BaseAbstractSlotCoverage,
    ContributionStatementPresence as BaseContributionStatementPresence,
    MetricValuePresence as BaseMetricValuePresence,
    SectionSlotCoverage as BaseSectionSlotCoverage,
)
from writer.base.text_quality import NotationConsistencyMetric


class AbstractSlotCoverage(BaseAbstractSlotCoverage):
    def __init__(self) -> None:
        super().__init__(policy=ACL_ABSTRACT_POLICY)


class ContributionStatementPresence(BaseContributionStatementPresence):
    def __init__(self) -> None:
        super().__init__(
            policy=ACL_CONTRIBUTION_POLICY,
            section_aliases=ACL_SECTION_POLICY.aliases,
        )


class ExperimentSlotCoverage(BaseSectionSlotCoverage):
    def __init__(self) -> None:
        super().__init__(
            policy=ACL_EXPERIMENT_POLICY,
            section_aliases=ACL_SECTION_POLICY.aliases,
        )


class ReproducibilitySlotCoverage(BaseSectionSlotCoverage):
    def __init__(self) -> None:
        super().__init__(
            policy=ACL_REPRO_POLICY,
            section_aliases=ACL_SECTION_POLICY.aliases,
        )


class MetricValuePresence(BaseMetricValuePresence):
    def __init__(self) -> None:
        super().__init__(section_aliases=ACL_SECTION_POLICY.aliases)


class NotationConsistency(NotationConsistencyMetric):
    def __init__(self) -> None:
        super().__init__(policy=ACL_NOTATION_POLICY)

