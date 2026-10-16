"""Deterministic LaTeX main-section order checking."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, TypedDict

from holosophos.tools.writer.section_titles import (
    normalize_section_title,
    translate_section_title,
)

ORDER = [
    "abstract",
    "introduction",
    "related_work",
    "method",
    "experiments",
    "results",
    "analysis",
    "discussion",
    "limitations",
    "conclusion",
    "references",
    "appendix",
]
ALIASES = {
    "related work": "related_work",
    "experimental setup": "experiments",
    "evaluation": "experiments",
    "ablations": "analysis",
    "ethics": "limitations",
    "broader impact": "limitations",
    "reproducibility": "limitations",
    "bibliography": "references",
    "error analysis": "analysis",
    "additional analysis": "analysis",
}
SECTION_RE = re.compile(r"\\section\*?(?:\[[^]]*\])?\s*\{(?P<title>[^{}]*)\}")


class SourceLocation(TypedDict):
    file: str
    line: int


class SectionMarker(TypedDict):
    raw: str
    canonical: str
    rank: int
    location: SourceLocation


def analyze_section_order(
    tex_path: str, policy: dict[str, Any] | None = None
) -> dict[str, Any]:
    path = Path(tex_path)
    if not path.is_file():
        return {
            "tool_name": "section_order",
            "detected_sections": [],
            "violations": [],
            "other_headings": [],
            "diagnostics": ["tex_missing"],
        }
    markers: list[SectionMarker] = []
    other: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        text = raw.split("%", 1)[0]
        match = SECTION_RE.search(text)
        if match:
            raw_title = match.group("title").strip()
        elif re.search(r"\\begin\s*\{abstract\}", text):
            raw_title = "Abstract"
        else:
            continue
        normalized = translate_section_title(normalize_section_title(raw_title))
        if normalized.startswith("appendix "):
            normalized = "appendix"
        canonical = ALIASES.get(normalized, normalized.replace(" ", "_"))
        if canonical not in ORDER:
            other.append(
                {
                    "title": raw_title,
                    "location": {"file": str(path), "line": line_number},
                }
            )
            continue
        markers.append(
            {
                "raw": raw_title,
                "canonical": canonical,
                "rank": ORDER.index(canonical),
                "location": {"file": str(path), "line": line_number},
            }
        )
    violations: list[dict[str, Any]] = []
    highest: SectionMarker | None = None
    for marker in markers:
        if highest is not None and marker["rank"] < highest["rank"]:
            violations.append(
                {
                    "section": marker,
                    "after": highest,
                    "message": f"{marker['raw']} appears after {highest['raw']}.",
                }
            )
        elif highest is None or marker["rank"] > highest["rank"]:
            highest = marker
    return {
        "tool_name": "section_order",
        "detected_sections": markers,
        "violations": violations,
        "other_headings": other,
        "diagnostics": [],
    }
