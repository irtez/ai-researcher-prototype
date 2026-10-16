import re
from pathlib import Path
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from academia_mcp.files import get_workspace_dir
from academia_mcp.s2_client import (
    REFERENCE_BUDGET_SECONDS,
    budget_remaining,
    request_budget,
)
from academia_mcp.tools.s2 import s2_get_info, s2_search

CITE_RE = re.compile(r"\\[a-zA-Z]*cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{([^}]+)\}")
ENTRY_RE = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", re.IGNORECASE)
ARXIV_NEW_RE = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?")
ARXIV_OLD_RE = re.compile(r"([a-z\-]+(?:\.[A-Z]{2})?/\d{7})")
NON_ENTRY_TYPES = {"comment", "string", "preamble"}


class RefIssue(BaseModel):  # type: ignore
    key: str = Field(description="BibTeX key or cite key the issue refers to")
    kind: str = Field(
        description=(
            "Issue type: missing_in_bib, orphan_in_bib, not_found, "
            "title_mismatch, lookup_failed or verification_skipped"
        )
    )
    detail: str = Field(description="Human-readable explanation of the issue")


class VerifyReferencesResponse(BaseModel):  # type: ignore
    tex_file: str = Field(description="Resolved path to the inspected .tex file")
    bib_file: Optional[str] = Field(
        description="Resolved path to the .bib file, if found"
    )
    cite_keys: int = Field(
        description="Number of distinct \\cite keys in the .tex file"
    )
    bib_entries: int = Field(description="Number of entries in the .bib file")
    resolved: int = Field(description="Number of cite keys that resolve to a bib entry")
    missing_in_bib: List[str] = Field(
        description="Cite keys with no matching bib entry"
    )
    orphan_in_bib: List[str] = Field(description="Bib entries that are never cited")
    existence_checked: int = Field(
        description="Number of bib entries attempted against Semantic Scholar, including failed lookups"
    )
    existence_skipped: int = Field(
        description="Number of bib entries not attempted (no id/title, cap or time budget reached)"
    )
    existence_confirmed: int = Field(
        default=0, description="Entries found with matching metadata"
    )
    existence_unavailable: int = Field(
        default=0, description="Entries whose lookup failed or timed out"
    )
    existence_status: Literal["complete", "partial", "not_requested"] = Field(
        default="not_requested",
        description="Coverage of online verification, independent of structural validity",
    )
    issues: List[RefIssue] = Field(description="All detected issues")
    ok: bool = Field(
        description="True if no missing keys, not_found or title_mismatch issues remain"
    )
    summary: str = Field(description="One-line summary of the verification result")


def _strip_tex_comments(text: str) -> str:
    lines: List[str] = []
    for line in text.splitlines():
        cut = re.sub(r"(?<!\\)%.*$", "", line)
        lines.append(cut)
    return "\n".join(lines)


def _extract_cite_keys(tex: str) -> List[str]:
    keys: List[str] = []
    seen: set[str] = set()
    for match in CITE_RE.finditer(_strip_tex_comments(tex)):
        for raw in match.group(1).split(","):
            key = raw.strip()
            if key and key not in seen:
                seen.add(key)
                keys.append(key)
    return keys


def _split_entries(bib: str) -> List[str]:
    positions = [m.start() for m in re.finditer(r"^[ \t]*@", bib, re.MULTILINE)]
    chunks: List[str] = []
    for index, start in enumerate(positions):
        end = positions[index + 1] if index + 1 < len(positions) else len(bib)
        chunks.append(bib[start:end])
    return chunks


def _extract_field(entry: str, field: str) -> Optional[str]:
    match = re.search(rf"\b{field}\s*=\s*", entry, re.IGNORECASE)
    if not match:
        return None
    start = match.end()
    if start >= len(entry):
        return None
    opener = entry[start]
    if opener == "{":
        depth = 0
        out: List[str] = []
        for char in entry[start:]:
            if char == "{":
                depth += 1
                if depth == 1:
                    continue
            elif char == "}":
                depth -= 1
                if depth == 0:
                    break
            out.append(char)
        return re.sub(r"\s+", " ", "".join(out)).strip()
    if opener == '"':
        try:
            closing = entry.index('"', start + 1)
        except ValueError:
            return None
        return re.sub(r"\s+", " ", entry[start + 1 : closing]).strip()
    tail = re.match(r"([^,\n}]+)", entry[start:])
    return tail.group(1).strip() if tail else None


def _extract_arxiv_id(entry: str) -> Optional[str]:
    fields = [
        _extract_field(entry, "eprint"),
        _extract_field(entry, "archiveprefix"),
        _extract_field(entry, "url"),
        _extract_field(entry, "journal"),
        _extract_field(entry, "note"),
    ]
    haystack = " ".join(value for value in fields if value)
    if "arxiv" not in haystack.lower() and not ARXIV_NEW_RE.search(haystack):
        eprint = _extract_field(entry, "eprint")
        if not eprint:
            return None
        haystack = eprint
    new_style = ARXIV_NEW_RE.search(haystack)
    if new_style:
        return new_style.group(1)
    old_style = ARXIV_OLD_RE.search(haystack)
    if old_style:
        return old_style.group(1)
    return None


def _normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def _titles_match(left: str, right: str) -> bool:
    norm_left = _normalize_title(left)
    norm_right = _normalize_title(right)
    if not norm_left or not norm_right:
        return False
    if norm_left == norm_right:
        return True
    if norm_left in norm_right or norm_right in norm_left:
        return True
    tokens_left = set(norm_left.split())
    tokens_right = set(norm_right.split())
    if not tokens_left or not tokens_right:
        return False
    overlap = len(tokens_left & tokens_right) / len(tokens_left | tokens_right)
    return overlap >= 0.8


def _resolve_path(filename: str) -> Path:
    path = Path(filename)
    if not path.exists():
        path = Path(get_workspace_dir()) / filename
    return path


def _is_not_found(error: Exception) -> bool:
    message = str(error).lower()
    return "404" in message or "not found" in message


def _lookup_title(
    arxiv_id: Optional[str], title: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    if arxiv_id:
        try:
            return s2_get_info(arxiv_id).title, None
        except Exception as error:
            if not _is_not_found(error):
                return None, "lookup_failed"
    if title:
        try:
            response = s2_search(title, limit=1)
        except Exception:
            return None, "lookup_failed"
        if response.results:
            return response.results[0].title, None
        return None, "not_found"
    return None, "not_found"


def verify_references(
    tex_filename: str,
    bib_filename: Optional[str] = None,
    verify_existence: bool = True,
    max_checks: int = 80,
) -> VerifyReferencesResponse:
    """
    Verify the bibliography of a LaTeX paper to catch hallucinated or broken citations.

    Performs two kinds of checks:
    1. Structural (always, offline): every \\cite key resolves to a references.bib entry,
       and reports bib entries that are never cited (orphans).
    2. Existence (when verify_existence=True): each bib entry that carries an arXiv id is
       confirmed via Semantic Scholar; entries without an arXiv id are looked up by title.
       A cited paper that cannot be found, or whose title does not match the real record,
       is flagged as a likely hallucination.

    Args:
        tex_filename: Path to the .tex file (absolute or relative to the workspace).
        bib_filename: Optional path to the .bib file. Defaults to references.bib next to the .tex.
        verify_existence: Whether to verify each bib entry against Semantic Scholar. True by default.
        max_checks: Maximum number of bib entries to verify online (bounds runtime). 80 by default.
    """
    tex_path = _resolve_path(tex_filename)
    assert tex_path.exists(), f"TeX file {tex_filename} does not exist"
    tex = tex_path.read_text(encoding="utf-8")

    if bib_filename:
        bib_path: Optional[Path] = _resolve_path(bib_filename)
    else:
        candidate = tex_path.parent / "references.bib"
        bib_path = candidate if candidate.exists() else None

    cite_keys = _extract_cite_keys(tex)

    bib_text = (
        bib_path.read_text(encoding="utf-8") if bib_path and bib_path.exists() else ""
    )
    entries = _split_entries(bib_text)
    entry_by_key: Dict[str, str] = {}
    for entry in entries:
        header = ENTRY_RE.match(entry.lstrip())
        if not header:
            continue
        if header.group(1).lower() in NON_ENTRY_TYPES:
            continue
        entry_by_key[header.group(2)] = entry

    issues: List[RefIssue] = []

    cite_set = set(cite_keys)
    bib_set = set(entry_by_key.keys())

    missing_in_bib = [key for key in cite_keys if key not in bib_set]
    for key in missing_in_bib:
        issues.append(
            RefIssue(
                key=key,
                kind="missing_in_bib",
                detail="Cited key has no references.bib entry",
            )
        )

    orphan_in_bib = sorted(bib_set - cite_set)
    for key in orphan_in_bib:
        issues.append(
            RefIssue(
                key=key,
                kind="orphan_in_bib",
                detail="Bib entry is never cited in the text",
            )
        )

    resolved = len(cite_set & bib_set)

    existence_checked = 0
    existence_skipped = 0
    existence_confirmed = 0
    existence_unavailable = 0
    if verify_existence:
        with request_budget(REFERENCE_BUDGET_SECONDS):
            for key, entry in entry_by_key.items():
                title = _extract_field(entry, "title")
                arxiv_id = _extract_arxiv_id(entry)
                reason = None
                if existence_checked >= max_checks:
                    reason = "Online check limit reached"
                elif not arxiv_id and not title:
                    reason = "No searchable identifier or title"
                elif budget_remaining() <= 0:
                    reason = "Bibliography online-check time budget exhausted"
                if reason:
                    existence_skipped += 1
                    issues.append(
                        RefIssue(key=key, kind="verification_skipped", detail=reason)
                    )
                    continue
                existence_checked += 1
                found_title, error_kind = _lookup_title(arxiv_id, title)
                if error_kind == "lookup_failed":
                    existence_unavailable += 1
                    issues.append(
                        RefIssue(
                            key=key,
                            kind="lookup_failed",
                            detail="Semantic Scholar lookup unavailable or timed out; source existence is unverified",
                        )
                    )
                    continue
                if error_kind == "not_found" or found_title is None:
                    issues.append(
                        RefIssue(
                            key=key,
                            kind="not_found",
                            detail=f"No Semantic Scholar record for {arxiv_id or (title or '')[:120]}",
                        )
                    )
                    continue
                if title and not _titles_match(title, found_title):
                    issues.append(
                        RefIssue(
                            key=key,
                            kind="title_mismatch",
                            detail=f"Cited '{title[:80]}' vs found '{found_title[:80]}'",
                        )
                    )
                    continue
                existence_confirmed += 1

    existence_status = (
        "not_requested"
        if not verify_existence
        else "partial" if existence_unavailable or existence_skipped else "complete"
    )

    hard_kinds = {"missing_in_bib", "not_found", "title_mismatch"}
    hard_issues = [issue for issue in issues if issue.kind in hard_kinds]
    ok = not hard_issues

    if not bib_path:
        summary = "references.bib not found; only cite keys were collected"
    else:
        state = (
            "FAIL" if not ok else "PARTIAL" if existence_status == "partial" else "OK"
        )
        online = (
            "online verification not requested"
            if not verify_existence
            else f"online verification {existence_status}: {existence_confirmed} confirmed, "
            f"{existence_unavailable} unavailable, {existence_skipped} skipped "
            f"({existence_checked} attempted); "
            f"{sum(i.kind == 'not_found' for i in issues)} not found in catalog, "
            f"{sum(i.kind == 'title_mismatch' for i in issues)} title mismatch"
        )
        summary = (
            f"{state}: {resolved}/{len(cite_keys)} cite keys resolved; {online}; "
            f"{len(orphan_in_bib)} orphan(s). Catalog matching does not verify quoted claims."
        )

    return VerifyReferencesResponse(
        tex_file=str(tex_path),
        bib_file=str(bib_path) if bib_path else None,
        cite_keys=len(cite_keys),
        bib_entries=len(entry_by_key),
        resolved=resolved,
        missing_in_bib=missing_in_bib,
        orphan_in_bib=orphan_in_bib,
        existence_checked=existence_checked,
        existence_skipped=existence_skipped,
        existence_confirmed=existence_confirmed,
        existence_unavailable=existence_unavailable,
        existence_status=existence_status,
        issues=issues,
        ok=ok,
        summary=summary,
    )
