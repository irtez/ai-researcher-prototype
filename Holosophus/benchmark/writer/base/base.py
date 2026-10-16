"""Base classes and schemas for the generic writer benchmark package."""

from __future__ import annotations

import enum
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class FailType(str, enum.Enum):
    HARD = "hard"
    SOFT = "soft"


@dataclass
class MetricResult:
    name: str
    value: int | float
    fail_type: FailType
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        if isinstance(self.value, float):
            return self.value >= 1.0 - 1e-9
        return self.value == 0


class BaseMetric(ABC):
    """Every metric receives a parsed PaperPackage and returns MetricResult(s)."""

    @abstractmethod
    def evaluate(self, pkg: "PaperPackage") -> list[MetricResult]:
        ...


_CITE_RE = re.compile(r"\\cite[tp]?\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}", re.MULTILINE)
_LABEL_RE = re.compile(r"\\label\{([^}]+)\}", re.MULTILINE)
_REF_RE = re.compile(r"\\(?:ref|autoref|cref|Cref)\{([^}]+)\}", re.MULTILINE)
_BEGIN_FIGURE_RE = re.compile(r"\\begin\{(figure|table)\*?\}", re.MULTILINE)
_END_FIGURE_RE = re.compile(r"\\end\{(figure|table)\*?\}", re.MULTILINE)
_CAPTION_RE = re.compile(r"\\caption\s*(?:\[[^\]]*\])?\s*\{", re.MULTILINE)
_BIB_KEY_RE = re.compile(r"@\w+\s*\{([^,\s]+)", re.MULTILINE)
_ABSTRACT_BEGIN = re.compile(r"\\begin\{abstract\}", re.MULTILINE)
_ABSTRACT_END = re.compile(r"\\end\{abstract\}", re.MULTILINE)
_SECTION_LEVEL_RE = re.compile(
    r"\\(section|subsection|subsubsection)\s*(\*?)\s*\{([^}]*)\}",
    re.MULTILINE,
)


@dataclass
class FloatEnv:
    kind: str
    labels: list[str]
    has_caption: bool
    raw: str


@dataclass
class SectionInfo:
    name: str
    normalized: str
    level: int
    body: str
    start_pos: int


@dataclass
class PaperPackage:
    """Parsed representation of a paper directory."""

    root: Path
    tex_path: Path
    bib_path: Path | None
    raw_tex: str
    raw_bib: str
    sections: list[SectionInfo]
    cite_keys_used: list[str]
    bib_keys_defined: list[str]
    labels_defined: list[str]
    refs_used: list[str]
    floats: list[FloatEnv]
    abstract_text: str

    @classmethod
    def from_directory(cls, path: str | Path) -> "PaperPackage":
        root = Path(path)
        tex_path = find_main_tex(root)
        raw_tex = tex_path.read_text(errors="replace")

        bib_path = find_bib(root)
        raw_bib = bib_path.read_text(errors="replace") if bib_path else ""

        return cls(
            root=root,
            tex_path=tex_path,
            bib_path=bib_path,
            raw_tex=raw_tex,
            raw_bib=raw_bib,
            sections=parse_sections(raw_tex),
            cite_keys_used=parse_cite_keys(raw_tex),
            bib_keys_defined=parse_bib_keys(raw_bib),
            labels_defined=[match.group(1) for match in _LABEL_RE.finditer(raw_tex)],
            refs_used=[match.group(1) for match in _REF_RE.finditer(raw_tex)],
            floats=parse_floats(raw_tex),
            abstract_text=parse_abstract(raw_tex),
        )


def find_main_tex(root: Path) -> Path:
    candidates = list(root.glob("main.tex"))
    if not candidates:
        candidates = list(root.glob("*.tex"))
        candidates = [
            candidate
            for candidate in candidates
            if not candidate.name.startswith("_") and "snippet" not in candidate.name.lower()
        ]
    if not candidates:
        raise FileNotFoundError(f"No .tex file found in {root}")

    for preferred in ("main.tex", "paper.tex"):
        for candidate in candidates:
            if candidate.name == preferred:
                return candidate
    return sorted(candidates, key=lambda path: path.name)[0]


def find_bib(root: Path) -> Path | None:
    candidates = list(root.glob("*.bib"))
    if not candidates:
        return None

    for preferred in ("references.bib", "ref.bib", "main.bib"):
        for candidate in candidates:
            if candidate.name == preferred:
                return candidate
    return sorted(candidates, key=lambda path: path.name)[0]


def strip_comments(tex: str) -> str:
    cleaned: list[str] = []
    for line in tex.split("\n"):
        idx = 0
        while True:
            idx = line.find("%", idx)
            if idx < 0:
                break
            if idx == 0 or line[idx - 1] != "\\":
                line = line[:idx]
                break
            idx += 1
        cleaned.append(line)
    return "\n".join(cleaned)


def normalize_section_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).lower().strip()


def parse_sections(tex: str) -> list[SectionInfo]:
    tex_clean = strip_comments(tex)
    doc_start = tex_clean.find("\\begin{document}")
    if doc_start >= 0:
        tex_clean = tex_clean[doc_start:]

    sections: list[SectionInfo] = []
    level_map = {"section": 0, "subsection": 1, "subsubsection": 2}
    matches = list(_SECTION_LEVEL_RE.finditer(tex_clean))
    for index, match in enumerate(matches):
        name = match.group(3).strip()
        level = level_map[match.group(1)]
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(tex_clean)
        body = tex_clean[start:end].strip()
        sections.append(
            SectionInfo(
                name=name,
                normalized=normalize_section_name(name),
                level=level,
                body=body,
                start_pos=match.start(),
            )
        )
    return sections


def parse_cite_keys(tex: str) -> list[str]:
    tex_clean = strip_comments(tex)
    keys: list[str] = []
    for match in _CITE_RE.finditer(tex_clean):
        for key in match.group(1).split(","):
            key = key.strip()
            if key:
                keys.append(key)
    return keys


def parse_bib_keys(bib: str) -> list[str]:
    return [match.group(1) for match in _BIB_KEY_RE.finditer(bib)]


def parse_floats(tex: str) -> list[FloatEnv]:
    tex_clean = strip_comments(tex)
    floats: list[FloatEnv] = []

    begins = list(_BEGIN_FIGURE_RE.finditer(tex_clean))
    ends = list(_END_FIGURE_RE.finditer(tex_clean))
    for begin_match in begins:
        kind = begin_match.group(1)
        start = begin_match.start()
        end_match = None
        for candidate in ends:
            if candidate.start() > start and candidate.group(1) == kind:
                end_match = candidate
                break
        if end_match is None:
            continue

        raw = tex_clean[start:end_match.end()]
        floats.append(
            FloatEnv(
                kind=kind,
                labels=[label.group(1) for label in _LABEL_RE.finditer(raw)],
                has_caption=bool(_CAPTION_RE.search(raw)),
                raw=raw,
            )
        )
    return floats


def parse_abstract(tex: str) -> str:
    tex_clean = strip_comments(tex)
    begin_match = _ABSTRACT_BEGIN.search(tex_clean)
    end_match = _ABSTRACT_END.search(tex_clean)
    if begin_match and end_match and end_match.start() > begin_match.end():
        return tex_clean[begin_match.end() : end_match.start()].strip()
    return ""

