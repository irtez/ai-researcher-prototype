"""ACL-specific formatting and submission compliance metrics."""

from __future__ import annotations

import re
import subprocess
import zlib
from pathlib import Path

from writer.acl.profile import (
    ACL_ABSTRACT_WORD_LIMIT,
    ACL_PAGE_LIMIT,
    ACL_REVIEW_TEMPLATE_RE,
    ACL_TEMPLATE_RE,
)
from writer.acl.base import BaseMetric, FailType, MetricResult, PaperPackage
from writer.base.text_quality import extract_plain_text

_AUTHOR_RE = re.compile(r"\\author\{([^}]*)\}", re.IGNORECASE | re.DOTALL)
_EMAIL_RE = re.compile(r"\b[\w.\-+]+@[\w.\-]+\.[A-Za-z]{2,}\b")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_ACK_RE = re.compile(r"\\section\*?\{acknowledg(?:e)?ments?\}", re.IGNORECASE)
_REVIEW_RE = re.compile(ACL_REVIEW_TEMPLATE_RE, re.IGNORECASE)
_TEMPLATE_RE = re.compile(ACL_TEMPLATE_RE, re.IGNORECASE)
_PDF_PAGE_RE = re.compile(rb"/Type\s*/Page\b")
_PDF_FLATE_STREAM_RE = re.compile(rb"stream\r?\n(.*?)\r?\nendstream", re.DOTALL)


def estimate_acl_pages(pkg: PaperPackage) -> float:
    """Approximate ACL content pages without relying on a compiled PDF."""

    text_words = len(extract_plain_text(pkg.raw_tex).split())
    explicit_page_breaks = len(
        re.findall(r"\\(?:newpage|clearpage|pagebreak)\b", pkg.raw_tex, re.IGNORECASE)
    )
    float_bonus = 0.35 * len(pkg.floats)
    estimated_from_words = text_words / 900.0
    return max(estimated_from_words + float_bonus, explicit_page_breaks + 1.0)


def _count_pdf_pages_with_pypdf(pdf_path: Path) -> int | None:
    """Count PDF pages with pypdf when the optional dependency is installed."""
    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]
    except ImportError:
        return None

    try:
        reader = PdfReader(pdf_path)
        return len(reader.pages)
    except Exception:
        return None


def _count_pdf_pages_with_pdfinfo(pdf_path: Path) -> int | None:
    """Count PDF pages with poppler's pdfinfo when available."""
    try:
        result = subprocess.run(
            ["pdfinfo", str(pdf_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None
    match = re.search(r"^Pages:\s*(\d+)\s*$", result.stdout, re.MULTILINE)
    return int(match.group(1)) if match else None


def _count_pdf_pages_with_regex(pdf_path: Path) -> int | None:
    """Best-effort fallback for raw or Flate-compressed PDF page objects."""
    data = pdf_path.read_bytes()
    page_count = len(_PDF_PAGE_RE.findall(data))
    for match in _PDF_FLATE_STREAM_RE.finditer(data):
        stream = match.group(1)
        for candidate in (stream, stream.strip()):
            try:
                decompressed = zlib.decompress(candidate)
            except zlib.error:
                continue
            page_count += len(_PDF_PAGE_RE.findall(decompressed))
            break
    return page_count or None


def count_pdf_pages(pkg: PaperPackage) -> tuple[int, str] | None:
    """Count pages from a generated PDF when available.

    Prefer pypdf because generated PDFs may compress page objects, making raw
    byte-marker counting unreliable. Use `pdfinfo` or raw markers only as
    fallbacks so the metric still works in minimal environments.
    """
    candidates = list(pkg.root.glob("paper.pdf")) or sorted(pkg.root.glob("*.pdf"))
    if not candidates:
        return None

    pdf_path = candidates[0]
    for source, counter in (
        ("pypdf", _count_pdf_pages_with_pypdf),
        ("pdfinfo", _count_pdf_pages_with_pdfinfo),
        ("regex", _count_pdf_pages_with_regex),
    ):
        page_count = counter(pdf_path)
        if page_count is not None:
            return page_count, source
    return None


class ACLPageLimitCompliance(BaseMetric):
    """ACL page-limit compliance check for long papers."""

    def evaluate(self, pkg: PaperPackage) -> list[MetricResult]:
        estimated_pages = estimate_acl_pages(pkg)
        measured = count_pdf_pages(pkg)
        measured_pages = measured[0] if measured else None
        measurement_source = measured[1] if measured else None
        pages_for_limit = measured_pages or estimated_pages
        return [
            MetricResult(
                name="acl_page_limit_compliance",
                value=1.0 if pages_for_limit <= ACL_PAGE_LIMIT else 0.0,
                fail_type=FailType.HARD,
                details={
                    "page_count_source": measurement_source or "heuristic",
                    "measured_pdf_pages": measured_pages,
                    "estimated_pages": round(estimated_pages, 2),
                    "pages_for_limit": pages_for_limit,
                    "page_limit": ACL_PAGE_LIMIT,
                },
            )
        ]


class ACLAbstractLengthCompliance(BaseMetric):
    """Check that the abstract stays within the ACL limit."""

    def evaluate(self, pkg: PaperPackage) -> list[MetricResult]:
        abstract_words = len(pkg.abstract_text.split())
        return [
            MetricResult(
                name="acl_abstract_length_compliance",
                value=1.0 if abstract_words <= ACL_ABSTRACT_WORD_LIMIT else 0.0,
                fail_type=FailType.HARD,
                details={
                    "abstract_word_count": abstract_words,
                    "word_limit": ACL_ABSTRACT_WORD_LIMIT,
                },
            )
        ]


class ACLFormatCompliance(BaseMetric):
    """Check for core ACL template markers in the LaTeX source."""

    def evaluate(self, pkg: PaperPackage) -> list[MetricResult]:
        has_acl_package = bool(_TEMPLATE_RE.search(pkg.raw_tex))
        has_article_class = bool(
            re.search(
                r"\\documentclass(?:\[[^\]]*11pt[^\]]*\])?\{article\}",
                pkg.raw_tex,
                re.IGNORECASE,
            )
        )
        details = {
            "has_acl_package": has_acl_package,
            "has_article_class": has_article_class,
        }
        return [
            MetricResult(
                name="acl_format_compliance",
                value=1.0 if has_acl_package and has_article_class else 0.0,
                fail_type=FailType.HARD,
                details=details,
            )
        ]


class ACLReviewVersionCompliance(BaseMetric):
    """Check that the source is configured as an ACL review submission."""

    def evaluate(self, pkg: PaperPackage) -> list[MetricResult]:
        has_review_option = bool(_REVIEW_RE.search(pkg.raw_tex))
        has_finalcopy = "\\aclfinalcopy" in pkg.raw_tex
        return [
            MetricResult(
                name="acl_review_version_compliance",
                value=1.0 if has_review_option and not has_finalcopy else 0.0,
                fail_type=FailType.HARD,
                details={
                    "has_review_option": has_review_option,
                    "has_finalcopy": has_finalcopy,
                },
            )
        ]


class ACLAnonymizationCompliance(BaseMetric):
    """Check for obvious deanonymizing signals forbidden in ACL review submissions."""

    def evaluate(self, pkg: PaperPackage) -> list[MetricResult]:
        violations: list[str] = []

        author_match = _AUTHOR_RE.search(pkg.raw_tex)
        if author_match:
            author_block = author_match.group(1).strip().lower()
            if author_block and "anonymous" not in author_block and "anon" not in author_block:
                violations.append("non-anonymous author block")

        if _EMAIL_RE.search(pkg.raw_tex):
            violations.append("email address present")

        if _ACK_RE.search(pkg.raw_tex):
            violations.append("acknowledgements section present")

        for match in _URL_RE.finditer(pkg.raw_tex):
            url = match.group(0).lower()
            if any(domain in url for domain in ("github.com", "gitlab.com", "drive.google.com")):
                violations.append(f"self-identifying link: {url}")

        return [
            MetricResult(
                name="acl_anonymization_compliance",
                value=1.0 if not violations else 0.0,
                fail_type=FailType.HARD,
                details={"violations": violations},
            )
        ]


class ACLResponsibleNLPChecklistCompliance(BaseMetric):
    """Check that the package includes a Responsible NLP checklist artifact."""

    def evaluate(self, pkg: PaperPackage) -> list[MetricResult]:
        checklist_files = [
            path.name
            for path in pkg.root.rglob("*")
            if path.is_file() and "checklist" in path.name.lower()
        ]
        inline_checklist = "responsible nlp research checklist" in pkg.raw_tex.lower()
        passed = bool(checklist_files or inline_checklist)
        return [
            MetricResult(
                name="acl_responsible_nlp_checklist_compliance",
                value=1.0 if passed else 0.0,
                fail_type=FailType.HARD,
                details={
                    "checklist_files": checklist_files,
                    "inline_checklist": inline_checklist,
                },
            )
        ]

