"""Measure Writer-agent scientific-paper section lengths from rendered PDFs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz  # type: ignore[import-untyped]
from holosophos.tools.writer.section_titles import (
    normalize_section_title,
    translate_section_title,
)

SECTION_ALIASES: dict[str, list[str]] = {
    "Abstract": ["abstract"],
    "Introduction": ["introduction", "intro"],
    "Related Work": ["related work", "background", "prior work", "literature review"],
    "Method": ["method", "methods", "methodology", "approach", "model", "framework"],
    "Experimental Setup": [
        "experimental setup",
        "experiments setup",
        "setup",
        "implementation details",
        "training details",
    ],
    "Results": ["results", "evaluation", "main results", "experiments"],
    "Analysis / Ablations": [
        "analysis",
        "ablation",
        "ablations",
        "ablation study",
        "error analysis",
        "additional analysis",
    ],
    "Limitations / Ethics / Reproducibility": [
        "limitations",
        "ethics",
        "broader impact",
        "reproducibility",
        "reproducibility statement",
    ],
    "Conclusion": [
        "conclusion",
        "conclusions",
        "discussion and conclusion",
        "discussion and conclusions",
        "concluding remarks",
    ],
}

OTHER_SECTION_TITLES = {
    "acknowledgments",
    "acknowledgements",
    "bibliography",
    "discussion",
    "references",
    "appendix",
}

MAIN_CONTENT_END_TITLES = {
    "acknowledgments",
    "acknowledgements",
    "bibliography",
    "references",
}

KNOWN_SECTION_TITLES = {
    alias
    for aliases in SECTION_ALIASES.values()
    for alias in aliases
} | OTHER_SECTION_TITLES

NUMBERED_HEADING_RE = re.compile(
    r"^(?:[A-ZА-ЯЁ]|\d+(?:\.\d+)*)(?:\s+|\.\s+)"
    r"([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё][\w ,/&:-]{1,80})$"
)
APPENDIX_HEADING_RE = re.compile(
    r"^(?:Appendix|Приложение|Приложения)(?:\s+[A-ZА-ЯЁ])?(?:[:.]\s+.+)?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SectionBoundary:
    """A detected scientific or terminal section boundary in PDF coordinates."""

    title: str
    page_index: int
    page_height: float
    x: float
    y: float | None
    font_size: float
    source: str

    @property
    def has_rendered_position(self) -> bool:
        return self.source == "rendered_heading" and self.y is not None


def analyze_paper_length(pdf_path: str) -> dict[str, Any]:
    """Return total and per-section lengths for a scientific-paper PDF.

    The Writer agent uses this deterministic report to assess major paper
    sections, rather than individual prose paragraphs. Fractional pages use
    rendered heading coordinates when available; outline-only sections use a
    page-level fallback and report that reduced precision in diagnostics.
    """
    diagnostics: list[str] = []
    path = Path(pdf_path)

    if not path.is_file():
        return {
            "tool_name": "paper_length",
            "total_pages": None,
            "main_content_pages": None,
            "main_content_measurement_source": None,
            "sections": _missing_component_sections(),
            "other_detected_headings": [],
            "diagnostics": ["pdf_missing"],
        }

    try:
        document = fitz.open(path)
    except Exception:  # noqa: BLE001 -- MuPDF failures become a diagnostic report.
        return {
            "tool_name": "paper_length",
            "total_pages": None,
            "main_content_pages": None,
            "main_content_measurement_source": None,
            "sections": _missing_component_sections(),
            "other_detected_headings": [],
            "diagnostics": ["pdf_parse_failed"],
        }

    try:
        total_pages = document.page_count
        rendered = _detect_section_headings(document)
        outlined = _detect_outline_boundaries(document)
        headings, boundary_diagnostics = _select_component_boundaries(
            rendered,
            outlined,
        )
        diagnostics.extend(boundary_diagnostics)

        if not headings:
            diagnostics.append("section_heading_not_found")
        if not any(not _is_terminal_heading(heading.title) for heading in headings):
            diagnostics.append("main_content_heading_not_found")

        sections, other_detected_headings, missing_diagnostics = _canonical_component_measurements(
            headings,
            total_pages,
        )
        diagnostics.extend(missing_diagnostics)
        main_content_pages, main_content_source, main_content_diagnostics = (
            _main_content_measurement(headings, total_pages)
        )
        diagnostics.extend(main_content_diagnostics)

        return {
            "tool_name": "paper_length",
            "total_pages": total_pages,
            "main_content_pages": main_content_pages,
            "main_content_measurement_source": main_content_source,
            "sections": sections,
            "other_detected_headings": other_detected_headings,
            "diagnostics": diagnostics,
        }
    finally:
        document.close()


def _detect_section_headings(document: fitz.Document) -> list[SectionBoundary]:
    headings: list[SectionBoundary] = []
    seen: set[tuple[str, int]] = set()

    for page_index in range(document.page_count):
        page = document.load_page(page_index)
        page_height = float(page.rect.height)
        page_dict = page.get_text("dict")

        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:
                continue

            for line in block.get("lines", []):
                text = _line_text(line)
                title = _heading_candidate_from_line(text)

                if title is None:
                    continue

                key = (_normalize_title(title), page_index)
                if key in seen:
                    continue

                seen.add(key)
                bbox = line.get("bbox", [0.0, 0.0, 0.0, 0.0])

                headings.append(
                    SectionBoundary(
                        title=title,
                        page_index=page_index,
                        page_height=page_height,
                        x=float(bbox[0]),
                        y=float(bbox[1]),
                        font_size=_max_font_size(line),
                        source="rendered_heading",
                    )
                )

    return sorted(headings, key=lambda heading: (heading.page_index, heading.y, heading.x))


def _detect_outline_boundaries(document: fitz.Document) -> list[SectionBoundary]:
    """Return page-level section candidates from PDF bookmarks when available."""
    try:
        toc = document.get_toc(simple=False)
    except Exception:  # noqa: BLE001 -- Malformed outlines use rendered headings.
        return []

    boundaries: list[SectionBoundary] = []
    seen: set[tuple[str, int]] = set()

    for entry in toc:
        if len(entry) < 3:
            continue

        raw_title, one_based_page = entry[1], entry[2]
        if not isinstance(raw_title, str) or not isinstance(one_based_page, int):
            continue

        title = _heading_candidate_from_line(raw_title)
        page_index = one_based_page - 1
        if title is None or not 0 <= page_index < document.page_count:
            continue

        key = (_normalize_title(title), page_index)
        if key in seen:
            continue

        seen.add(key)
        boundaries.append(
            SectionBoundary(
                title=title,
                page_index=page_index,
                page_height=float(document.load_page(page_index).rect.height),
                x=0.0,
                y=None,
                font_size=0.0,
                source="outline_page_fallback",
            )
        )

    return sorted(boundaries, key=lambda boundary: (boundary.page_index, boundary.title))


def _select_component_boundaries(
    rendered: list[SectionBoundary],
    outlined: list[SectionBoundary],
) -> tuple[list[SectionBoundary], list[str]]:
    """Prefer rendered positions while retaining outline-only components."""
    selected: list[SectionBoundary] = []
    diagnostics: list[str] = []

    for component in SECTION_ALIASES:
        rendered_matches = [
            boundary
            for boundary in rendered
            if _component_for_heading(boundary.title) == component
        ]
        outline_matches = [
            boundary
            for boundary in outlined
            if _component_for_heading(boundary.title) == component
        ]

        if rendered_matches:
            # Rendered coordinates reflect the visible layout and enable decimals.
            selected.extend(rendered_matches)
            if outline_matches and not any(
                rendered_boundary.page_index == outline_boundary.page_index
                for rendered_boundary in rendered_matches
                for outline_boundary in outline_matches
            ):
                diagnostics.append(f"outline_rendered_page_mismatch: {component}")
        elif outline_matches:
            selected.extend(outline_matches)
            diagnostics.append(f"outline_page_fallback: {component}")

    rendered_terminals = [
        boundary for boundary in rendered if _is_terminal_heading(boundary.title)
    ]
    outline_terminals = [
        boundary for boundary in outlined if _is_terminal_heading(boundary.title)
    ]
    # Acknowledgments, References, Bibliography, and Appendix end main content.
    selected.extend(rendered_terminals or outline_terminals)

    rendered_other = [
        boundary
        for boundary in rendered
        if _component_for_heading(boundary.title) is None
        and not _is_terminal_heading(boundary.title)
    ]
    outline_other = [
        boundary
        for boundary in outlined
        if _component_for_heading(boundary.title) is None
        and not _is_terminal_heading(boundary.title)
    ]
    selected.extend(rendered_other or outline_other)

    return (
        sorted(
            selected,
            key=lambda boundary: (
                boundary.page_index,
                boundary.y is None,
                boundary.y or 0.0,
                boundary.x,
            ),
        ),
        diagnostics,
    )


def _canonical_component_measurements(
    headings: list[SectionBoundary],
    total_pages: int,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    groups: dict[str, list[tuple[int, SectionBoundary]]] = {
        component: [] for component in SECTION_ALIASES
    }
    other_detected_headings: list[str] = []

    for index, heading in enumerate(headings):
        component = _component_for_heading(heading.title)

        if component is None:
            other_detected_headings.append(heading.title)
        else:
            groups[component].append((index, heading))

    sections: list[dict[str, Any]] = []
    diagnostics: list[str] = []

    for component in SECTION_ALIASES:
        matches = groups[component]

        if not matches:
            sections.append(
                {
                    "component": component,
                    "matched_titles": [],
                    "found": False,
                    "start_page": None,
                    "end_page": None,
                    "page_span": None,
                    "fractional_pages": None,
                    "measurement_source": None,
                }
            )
            diagnostics.append(f"section_not_found: {component}")
            continue

        _, first_heading = matches[0]
        last_index, _ = matches[-1]

        next_heading = headings[last_index + 1] if last_index + 1 < len(headings) else None

        sections.append(
            _section_measurement(
                component=component,
                matched_titles=[heading.title for _, heading in matches],
                heading=first_heading,
                next_heading=next_heading,
                total_pages=total_pages,
            )
        )

    return sections, other_detected_headings, diagnostics


def _main_content_measurement(
    boundaries: list[SectionBoundary],
    total_pages: int,
) -> tuple[float, str, list[str]]:
    terminal = next(
        (boundary for boundary in boundaries if _is_terminal_heading(boundary.title)),
        None,
    )

    if terminal is None:
        return float(total_pages), "document_end", []

    if terminal.has_rendered_position:
        assert terminal.y is not None
        page_fraction = _bounded(terminal.y / terminal.page_height)
        return round(terminal.page_index + page_fraction, 2), "rendered_heading", []

    return (
        float(terminal.page_index),
        "outline_page_fallback",
        ["main_content_outline_page_fallback"],
    )


def _missing_component_sections() -> list[dict[str, Any]]:
    return [
        {
            "component": component,
            "matched_titles": [],
            "found": False,
            "start_page": None,
            "end_page": None,
            "page_span": None,
            "fractional_pages": None,
            "measurement_source": None,
        }
        for component in SECTION_ALIASES
    ]


def _section_measurement(
    component: str,
    matched_titles: list[str],
    heading: SectionBoundary,
    next_heading: SectionBoundary | None,
    total_pages: int,
) -> dict[str, Any]:
    start_page = heading.page_index + 1
    end_page = next_heading.page_index + 1 if next_heading else total_pages

    return {
        "component": component,
        "matched_titles": matched_titles,
        "found": True,
        "start_page": start_page,
        "end_page": end_page,
        "page_span": max(end_page - start_page + 1, 1),
        "fractional_pages": _fractional_pages(heading, next_heading, total_pages),
        "measurement_source": heading.source,
    }


def _fractional_pages(
    heading: SectionBoundary,
    next_heading: SectionBoundary | None,
    total_pages: int,
) -> float:
    if not heading.has_rendered_position or (
        next_heading is not None and not next_heading.has_rendered_position
    ):
        end_page_index = next_heading.page_index if next_heading else total_pages - 1
        return float(max(end_page_index - heading.page_index + 1, 1))

    assert heading.y is not None

    if next_heading is None:
        first_page_fraction = _bounded((heading.page_height - heading.y) / heading.page_height)
        full_pages_after = max(total_pages - heading.page_index - 1, 0)
        return round(first_page_fraction + full_pages_after, 2)

    if next_heading.page_index == heading.page_index:
        assert next_heading.y is not None
        return round(_bounded((next_heading.y - heading.y) / heading.page_height), 2)

    assert next_heading.y is not None
    first_page_fraction = _bounded((heading.page_height - heading.y) / heading.page_height)
    full_pages_between = max(next_heading.page_index - heading.page_index - 1, 0)
    next_page_fraction = _bounded(next_heading.y / next_heading.page_height)

    return round(first_page_fraction + full_pages_between + next_page_fraction, 2)


def _heading_candidate_from_line(text: str) -> str | None:
    clean = _clean_text(text)

    if not clean:
        return None

    normalized = _normalize_title(clean)

    if normalized in KNOWN_SECTION_TITLES:
        return clean

    numbered = NUMBERED_HEADING_RE.match(clean)

    if numbered:
        title = numbered.group(1).strip()
        if _looks_like_heading(title):
            return clean

    if APPENDIX_HEADING_RE.match(clean):
        return clean

    return None


def _component_for_heading(title: str) -> str | None:
    normalized = _normalize_title(title)

    for component, aliases in SECTION_ALIASES.items():
        for alias in aliases:
            if normalized == alias or alias in normalized:
                return component

    return None


def _is_terminal_heading(title: str) -> bool:
    normalized = _normalize_title(title)
    return normalized in MAIN_CONTENT_END_TITLES or normalized.startswith("appendix")


def _line_text(line: dict[str, Any]) -> str:
    spans = line.get("spans", [])
    return " ".join(span.get("text", "") for span in spans)


def _max_font_size(line: dict[str, Any]) -> float:
    sizes = [float(span.get("size", 0.0)) for span in line.get("spans", [])]
    return max(sizes) if sizes else 0.0


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize_title(text: str) -> str:
    text = re.sub(r"^\d+(?:\.\d+)*\.?\s+", "", text)
    text = re.sub(r"^[A-ZА-ЯЁ]\.?\s+", "", text)
    return translate_section_title(normalize_section_title(text))


def _looks_like_heading(text: str) -> bool:
    words = text.split()

    if not 1 <= len(words) <= 8:
        return False

    lowercase_words = sum(1 for word in words if word[:1].islower())
    return lowercase_words <= max(1, len(words) // 2)


def _bounded(value: float) -> float:
    return min(max(value, 0.0), 1.0)
