"""Thin optional adapter around existing reviewer_patches utilities."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


def parse_sections_with_reviewer(text: str) -> Any:
    from section_parser import parse_sections_from_text
    return parse_sections_from_text(text)


def structured_text_with_reviewer_sections(text: str) -> str:
    parsed = parse_sections_with_reviewer(text)
    return parsed.to_structured_prompt()


def parse_pdf_text_with_academia(pdf_path: str | Path) -> str:
    from academia_mcp.pdf import parse_pdf_file
    return "\n\n\n".join(parse_pdf_file(Path(pdf_path)))
