"""Profile dataclasses for venue-specific writer metric policies."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SectionPolicy:
    required_sections: tuple[str, ...]
    ordered_sections: tuple[str, ...]
    aliases: dict[str, str]
    min_section_words: int = 40


@dataclass(frozen=True)
class SlotCoveragePolicy:
    metric_name: str
    slots: dict[str, tuple[str, ...]]
    target_sections: tuple[str, ...] = ()
    empty_message: str = ""


@dataclass(frozen=True)
class ContributionPolicy:
    metric_name: str
    patterns: tuple[str, ...]
    target_sections: tuple[str, ...] = ("introduction",)


@dataclass(frozen=True)
class NotationPolicy:
    metric_name: str
    variant_groups: tuple[tuple[str, ...], ...]

