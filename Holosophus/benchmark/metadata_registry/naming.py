"""Stable naming helpers for portable benchmark metadata."""

from __future__ import annotations

import re
from pathlib import Path


FIELD_SEPARATOR = "__"


def slugify(value: str, fallback: str = "untitled") -> str:
    """Convert a display title into a stable filesystem/metadata slug."""
    value = re.sub(r"[^a-z0-9]+", "_", value.lower())
    value = re.sub(r"_+", "_", value).strip("_")
    return value or fallback


def build_paper_uid(conference: str, year: str | int, title: str) -> str:
    """Build the stable paper identifier shared across machines."""
    return FIELD_SEPARATOR.join(
        [
            slugify(str(conference), "unknown_conference"),
            slugify(str(year), "unknown_year"),
            slugify(title, "untitled"),
        ]
    )


def build_variant_id(paper_uid: str, distillation_level: str) -> str:
    """Build a stable distilled-variant identifier."""
    return FIELD_SEPARATOR.join([paper_uid, slugify(distillation_level, "unknown")])


def parse_variant_id(variant_id: str) -> dict[str, str]:
    """Parse {conference}__{year}__{paper_slug}__{distillation_level}."""
    parts = variant_id.split(FIELD_SEPARATOR)
    if len(parts) != 4:
        raise ValueError(
            "Expected variant id format "
            "{conference}__{year}__{paper_slug}__{distillation_level}; "
            f"got {variant_id!r}"
        )
    conference, year, paper_slug, distillation_level = parts
    return {
        "conference": conference,
        "year": year,
        "paper_slug": paper_slug,
        "paper_uid": FIELD_SEPARATOR.join(parts[:3]),
        "distillation_level": distillation_level,
        "variant_id": variant_id,
    }


def extract_title_from_tex(tex_path: Path) -> str | None:
    """Best-effort extraction of a LaTeX \\title{...} value."""
    try:
        text = tex_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    match = re.search(r"\\title\{(.+?)\}", text, flags=re.DOTALL)
    if not match:
        return None

    title = match.group(1)
    title = re.sub(r"\\includegraphics(?:\[[^\]]*\])?\{[^}]*\}", "", title)
    title = title.replace("\\\\", " ")
    title = re.sub(r"\\[a-zA-Z]+\*?", " ", title)
    title = re.sub(r"\{|\}", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title or None


def find_root_tex(paper_dir: Path) -> Path | None:
    """Find the most likely root TeX source in an original paper directory."""
    candidates = sorted(paper_dir.glob("*.tex"))
    preferred: list[Path] = []
    fallback: list[Path] = []
    for candidate in candidates:
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "\\documentclass" in text and "\\begin{document}" in text:
            preferred.append(candidate)
        else:
            fallback.append(candidate)
    search_order = preferred + fallback
    for candidate in search_order:
        if extract_title_from_tex(candidate):
            return candidate
    return search_order[0] if search_order else None
