import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from academia_mcp.files import get_workspace_dir

logger = logging.getLogger(__name__)

HALLUCINATION_VERDICTS = {"LIKELY", "HIGHLY_LIKELY"}


class FlaggedRef(BaseModel):  # type: ignore
    title: str = Field(description="Title of the flagged reference")
    authors: str = Field(description="Authors of the flagged reference, as cited")
    error_type: str = Field(description="Type of citation error, if any")
    error_details: str = Field(description="Short explanation of the error")
    hallucination_verdict: str = Field(
        description="Hallucination verdict: LIKELY, HIGHLY_LIKELY or empty"
    )
    hallucination_explanation: str = Field(
        description="Short explanation of the hallucination verdict"
    )


class RefCheckDeepResponse(BaseModel):  # type: ignore
    total_refs: int = Field(description="Total number of references processed")
    errors: int = Field(description="Number of citation errors found")
    warnings: int = Field(description="Number of warnings found")
    unverified: int = Field(description="Number of references that could not be verified")
    hallucinated: int = Field(description="Number of references judged likely hallucinated")
    flagged_refs: List[FlaggedRef] = Field(description="Per-reference details for flagged entries")
    ok: bool = Field(description="True if no hallucinations were found and the run succeeded")
    summary: str = Field(description="One-line summary of the verification result")
    error_message: Optional[str] = Field(description="Error string if the checker failed to run")


def _parse_report(report_path: str) -> RefCheckDeepResponse:
    report_file = Path(report_path)
    if not report_file.is_file() or report_file.stat().st_size == 0:
        return _failed("RefChecker produced no report")

    try:
        data = json.loads(report_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return _failed(f"Failed to parse report: {error}")

    summary = data.get("summary", {})
    total_refs = int(summary.get("total_references_processed", 0) or 0)
    errors = int(summary.get("total_errors_found", 0) or 0)
    warnings = int(summary.get("total_warnings_found", 0) or 0)
    unverified = int(summary.get("total_unverified_refs", 0) or 0)

    hallucinated = 0
    flagged: List[FlaggedRef] = []
    for record in data.get("records", []):
        assessment = record.get("hallucination_assessment", {}) or {}
        verdict = (assessment.get("verdict") or "").upper()
        is_hallucinated = verdict in HALLUCINATION_VERDICTS
        if is_hallucinated:
            hallucinated += 1
        if record.get("error_type") or is_hallucinated:
            flagged.append(
                FlaggedRef(
                    title=record.get("ref_title", ""),
                    authors=record.get("ref_authors_cited", ""),
                    error_type=record.get("error_type", "") or "",
                    error_details=(record.get("error_details") or "")[:200],
                    hallucination_verdict=verdict,
                    hallucination_explanation=(assessment.get("explanation") or "")[:200],
                )
            )

    ok = hallucinated == 0
    if ok:
        summary_text = (
            f"OK: {total_refs} refs, {errors} error(s), " f"{unverified} unverified, 0 hallucinated"
        )
    else:
        summary_text = f"FAIL: {hallucinated} likely hallucinated reference(s) out of {total_refs}"

    return RefCheckDeepResponse(
        total_refs=total_refs,
        errors=errors,
        warnings=warnings,
        unverified=unverified,
        hallucinated=hallucinated,
        flagged_refs=flagged[:20],
        ok=ok,
        summary=summary_text,
        error_message=None,
    )


def _failed(message: str) -> RefCheckDeepResponse:
    return RefCheckDeepResponse(
        total_refs=0,
        errors=0,
        warnings=0,
        unverified=0,
        hallucinated=0,
        flagged_refs=[],
        ok=False,
        summary=f"RefChecker did not complete: {message}",
        error_message=message,
    )


def verify_references_deep(
    pdf_filename: str,
    llm_provider: Optional[str] = None,
) -> RefCheckDeepResponse:
    """
    Deep reference verification of a paper PDF via the academic-refchecker engine.

    Extracts every reference from the PDF and verifies it against external bibliographic
    databases (Semantic Scholar, OpenAlex, CrossRef, DBLP, ACL Anthology). For each
    reference it returns an error type, an unverified flag, and a hallucination verdict
    (LIKELY / HIGHLY_LIKELY) with an explanation. Use this as a hard gate before accepting
    a generated paper: a reference judged hallucinated should block the result.

    This is slower than verify_references (network-bound, roughly 1-5 minutes per paper)
    and requires the optional `academic-refchecker` package to be installed.

    Args:
        pdf_filename: Path to the paper PDF (absolute or relative to the workspace).
        llm_provider: Optional LLM provider for web-search-based hallucination assessment.
    """
    try:
        from refchecker.core.refchecker import ArxivReferenceChecker
    except ImportError:
        return _failed(
            "academic-refchecker is not installed (pip install 'academic-refchecker[llm]')"
        )

    pdf_path = Path(pdf_filename)
    if not pdf_path.exists():
        pdf_path = Path(get_workspace_dir()) / pdf_filename
    if not pdf_path.exists():
        return _failed(f"PDF file {pdf_filename} does not exist")

    report_path: Optional[str] = None
    try:
        descriptor, report_path = tempfile.mkstemp(suffix=".json", prefix="refchecker_")
        os.close(descriptor)

        kwargs: Dict[str, Any] = {"report_file": report_path, "report_format": "json"}
        if llm_provider:
            kwargs["llm_provider"] = llm_provider

        logger.info("Running RefChecker on %s", pdf_path)
        checker = ArxivReferenceChecker(**kwargs)
        checker.check_paper(str(pdf_path))
        return _parse_report(report_path)
    except Exception as error:
        logger.warning("RefChecker failed: %s", error, exc_info=True)
        return _failed(str(error))
    finally:
        if report_path:
            try:
                os.unlink(report_path)
            except OSError:
                pass
