"""ACL-specific structural metrics built on top of shared primitives."""

from __future__ import annotations

from writer.acl.profile import ACL_SECTION_POLICY
from writer.base.structural import (
    RequiredSectionNonEmptiness as BaseRequiredSectionNonEmptiness,
    RequiredSectionPresence as BaseRequiredSectionPresence,
    SectionLengthAdequacy as BaseSectionLengthAdequacy,
    SectionOrderValidity as BaseSectionOrderValidity,
)


class RequiredSectionPresence(BaseRequiredSectionPresence):
    def __init__(self) -> None:
        super().__init__(policy=ACL_SECTION_POLICY)


class RequiredSectionNonEmptiness(BaseRequiredSectionNonEmptiness):
    def __init__(self) -> None:
        super().__init__(policy=ACL_SECTION_POLICY)


class SectionOrderValidity(BaseSectionOrderValidity):
    def __init__(self) -> None:
        super().__init__(policy=ACL_SECTION_POLICY)


class SectionLengthAdequacy(BaseSectionLengthAdequacy):
    def __init__(self) -> None:
        super().__init__(policy=ACL_SECTION_POLICY)

