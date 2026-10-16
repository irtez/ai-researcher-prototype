"""Deterministically check placement of scientific artifacts in LaTeX source."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from holosophos.tools.writer.section_titles import (
    normalize_section_title,
    translate_section_title,
)

POLICY_PATH = Path(__file__).with_name("content_placement_policy.json")
CANONICAL_SECTIONS = {
    "abstract",
    "introduction",
    "background",
    "related_work",
    "method",
    "experiments",
    "results",
    "analysis",
    "discussion",
    "limitations",
    "conclusion",
    "appendix",
    "references",
    "bibliography",
}
INCLUDE_RE = re.compile(r"\\(?:input|include)\s*\{([^{}]+)\}")
SECTION_RE = re.compile(
    r"\\(?P<level>section|subsection|subsubsection|paragraph|subparagraph)\*?"
    r"(?:\[[^]]*])?\s*\{(?P<title>[^{}]*)\}"
)
SECTION_LEVELS = {name: level for level, name in enumerate(
    ("section", "subsection", "subsubsection", "paragraph", "subparagraph")
)}
ENV_RE = re.compile(r"\\(begin|end)\s*\{([^{}]+)\}")
MACRO_RE = re.compile(r"\\(?:newcommand|renewcommand|providecommand|def)\b")


@dataclass(frozen=True)
class SourceLine:
    path: Path
    line_number: int
    text: str


def analyze_content_placement(
    tex_path: str,
    policy: Mapping[str, Any] | None = None,
    source_root: str | None = None,
) -> dict[str, Any]:
    """Classify source-level scientific artifacts by their LaTeX section."""
    active_policy = dict(policy) if policy is not None else _load_default_policy()
    root = Path(tex_path)
    boundary = Path(source_root).resolve() if source_root is not None else None
    if not root.is_file():
        return _empty_report(active_policy, ["tex_missing"])
    diagnostics: list[str] = []
    source_files: list[str] = []
    try:
        lines = _expand_source(root, set(), diagnostics, source_files, boundary)
    except (OSError, UnicodeError):
        return _empty_report(active_policy, ["tex_read_failed"])
    findings = _scan(lines, active_policy, diagnostics)
    report = _empty_report(active_policy, diagnostics)
    report["source_files"] = source_files
    report["findings"] = findings
    report["summary"] = _summary(findings)
    return report


def _load_default_policy() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(POLICY_PATH.read_text(encoding="utf-8")))


def _empty_report(policy: Mapping[str, Any], diagnostics: list[str]) -> dict[str, Any]:
    return {
        "tool_name": "content_placement",
        "policy_name": policy["policy_name"],
        "policy_version": policy["policy_version"],
        "source_files": [],
        "summary": {
            "artifacts_detected": 0,
            "allowed": 0,
            "discouraged": 0,
            "forbidden": 0,
            "unknown_section": 0,
        },
        "findings": [],
        "diagnostics": diagnostics,
    }


def _expand_source(
    path: Path,
    active: set[Path],
    diagnostics: list[str],
    source_files: list[str],
    source_root: Path | None,
) -> list[SourceLine]:
    resolved = path.resolve(strict=False)
    if source_root is not None and not resolved.is_relative_to(source_root):
        diagnostics.append(f"include_outside_workspace: {path.name}")
        return []
    if resolved in active:
        diagnostics.append(f"include_cycle: {path}")
        return []
    if not path.is_file():
        diagnostics.append(f"include_missing: {path.name}")
        return []
    source_files.append(str(path))
    result: list[SourceLine] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        text = _strip_comment(raw)
        include = INCLUDE_RE.search(text)
        if include is None:
            result.append(SourceLine(path, line_number, text))
            continue
        child = path.parent / include.group(1).strip()
        if child.suffix != ".tex":
            child = child.with_suffix(".tex")
        result.extend(
            _expand_source(child, active | {resolved}, diagnostics, source_files, source_root)
        )
    return result


def _strip_comment(line: str) -> str:
    for index, character in enumerate(line):
        if character == "%" and (index == 0 or line[index - 1] != "\\"):
            return line[:index]
    return line


def _brace_delta(text: str) -> int:
    return sum(
        1 if character == "{" else -1
        for index, character in enumerate(text)
        if character in "{}" and (index == 0 or text[index - 1] != "\\")
    )


def _scan(
    lines: list[SourceLine], policy: Mapping[str, Any], diagnostics: list[str]
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    section: tuple[str, str | None] | None = None
    parents: dict[int, tuple[str, str | None]] = {}
    stack: list[tuple[str, str | None]] = []
    listing: str | None = None
    display_open = False
    macro_brace_depth: int | None = None
    for line in lines:
        if macro_brace_depth is not None:
            macro_brace_depth += _brace_delta(line.text)
            if macro_brace_depth <= 0:
                macro_brace_depth = None
            continue
        if listing:
            if re.search(rf"\\end\s*\{{{re.escape(listing)}\}}", line.text):
                _pop(stack, listing)
                listing = None
            continue
        macro = MACRO_RE.search(line.text)
        if macro:
            depth = _brace_delta(line.text[macro.start() :])
            macro_brace_depth = depth if depth > 0 else None
            continue
        section_spans = [(0, section)]

        def section_at(offset: int) -> tuple[str, str | None] | None:
            return next(value for start, value in reversed(section_spans) if start <= offset)

        tokens = sorted(
            [*SECTION_RE.finditer(line.text), *ENV_RE.finditer(line.text)],
            key=lambda match: match.start(),
        )
        for token in tokens:
            if token.re is SECTION_RE:
                raw = token.group("title").strip()
                level = SECTION_LEVELS[token.group("level")]
                parents = {depth: parent for depth, parent in parents.items() if depth < level}
                canonical = _canonical_section(raw, policy)
                if canonical is None and parents:
                    canonical = parents[max(parents)][1]
                section = (raw, canonical)
                parents[level] = section
                section_spans.append((token.end(), section))
                continue
            command, environment = token.groups()
            if environment == "abstract":
                parents.clear()
                section = ("Abstract", "abstract") if command == "begin" else None
                section_spans.append((token.end(), section))
            artifact = _artifact_for_environment(environment, policy)
            if command == "begin":
                outer = not any(item[1] for item in stack)
                recorded = artifact if outer else None
                stack.append((environment, recorded))
                if recorded:
                    findings.append(_finding(recorded, section, line, token.group(0), policy))
                if artifact == "code_listing" and not re.search(
                    rf"\\end\s*\{{{re.escape(environment)}\}}", line.text[token.end() :]
                ):
                    listing = environment
            else:
                _pop(stack, environment)
        if not any(item[1] for item in stack):
            for pattern, artifact in (
                (r"\\captionof\s*\{figure\}", "figure"),
                (r"\\captionof\s*\{table\}", "table"),
                (r"\\lstinputlisting\b", "code_listing"),
                (r"\\inputminted\b", "code_listing"),
                (r"\\includegraphics\b", "figure"),
            ):
                match = re.search(pattern, line.text)
                if match:
                    findings.append(_finding(artifact, section_at(match.start()), line, match.group(0), policy))
        if r"\[" in line.text and not display_open and not any(item[1] for item in stack):
            findings.append(_finding("equation", section_at(line.text.index(r"\[")), line, r"\[", policy))
            display_open = True
        if r"\]" in line.text:
            display_open = False
        if line.text.count("$$") % 2:
            if not display_open and not any(item[1] for item in stack):
                findings.append(_finding("equation", section_at(line.text.index("$$")), line, "$$", policy))
            display_open = not display_open
    if display_open:
        diagnostics.append("unmatched_display_math")
    for environment, artifact in stack:
        if artifact:
            diagnostics.append(f"unclosed_environment: {environment}")
    return findings


def _pop(stack: list[tuple[str, str | None]], environment: str) -> None:
    for index in range(len(stack) - 1, -1, -1):
        if stack[index][0] == environment:
            del stack[index:]
            return


def _artifact_for_environment(environment: str, policy: Mapping[str, Any]) -> str | None:
    if environment in {"tikzpicture", "axis"}:
        return "figure"
    aliases = policy["artifact_aliases"]["environments"]
    return cast(str | None, aliases.get(environment))


def _canonical_section(title: str, policy: Mapping[str, Any]) -> str | None:
    plain = re.sub(r"\\[A-Za-z]+(?:\[[^]]*])?\{([^{}]*)\}", r"\1", title)
    normalized = normalize_section_title(plain)
    if normalized in policy["section_aliases"]:
        return cast(str, policy["section_aliases"][normalized])
    normalized = translate_section_title(normalized)
    if normalized.startswith("appendix "):
        normalized = "appendix"
    if normalized in policy["section_aliases"]:
        return cast(str, policy["section_aliases"][normalized])
    candidate = normalized.replace(" ", "_")
    return candidate if candidate in CANONICAL_SECTIONS else None


def _classification(
    artifact: str, section: str | None, policy: Mapping[str, Any]
) -> tuple[str, str]:
    if section is None:
        return "unknown_section", "warning"
    rules = policy["artifacts"][artifact]
    if section in rules["forbidden_sections"]:
        return "forbidden", "error"
    if section in rules["discouraged_sections"]:
        return "discouraged", "warning"
    if section in rules["allowed_sections"]:
        return "allowed", "info"
    return "unknown_section", "warning"


def _finding(
    artifact: str,
    section: tuple[str, str | None] | None,
    line: SourceLine,
    evidence: str,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    raw, canonical = section or (None, None)
    classification, severity = _classification(artifact, canonical, policy)
    allowed = ", ".join(policy["artifacts"][artifact]["allowed_sections"])
    if classification == "forbidden":
        message = f"{artifact} detected in forbidden section '{raw}'. Allowed sections: {allowed}."
    elif classification == "discouraged":
        message = f"{artifact} detected in discouraged section '{raw}'. Review its placement."
    elif classification == "unknown_section":
        message = f"{artifact} detected, but no section could be assigned reliably."
    else:
        message = f"{artifact} placement is allowed in section '{raw}'."
    return {
        "artifact_type": artifact,
        "classification": classification,
        "severity": severity,
        "section": {"raw": raw, "canonical": canonical},
        "location": {"file": str(line.path), "line": line.line_number},
        "evidence": evidence,
        "message": message,
    }


def _summary(findings: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "artifacts_detected": len(findings),
        "allowed": sum(item["classification"] == "allowed" for item in findings),
        "discouraged": sum(item["classification"] == "discouraged" for item in findings),
        "forbidden": sum(item["classification"] == "forbidden" for item in findings),
        "unknown_section": sum(item["classification"] == "unknown_section" for item in findings),
    }
