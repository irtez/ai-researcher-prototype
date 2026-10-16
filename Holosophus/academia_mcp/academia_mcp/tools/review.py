import base64
import json
import re
import tempfile
import uuid
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from research_paper_registry import PaperRegistry, file_sha256

from academia_mcp.files import get_workspace_dir
from academia_mcp.llm import ChatMessage, llm_acall_structured
from academia_mcp.pdf import download_pdf, parse_pdf_file, parse_pdf_file_to_images
from academia_mcp.settings import settings

PROMPT = """
You are a fair, constructive peer reviewer of computer-science research and analysis.
Assess the work against its stated question, requested scope and actual evidence.
A comparison, reproduction or educational analysis can be complete without a new method.
Apply venue-specific publication standards only when a target venue is explicitly requested;
otherwise judge whether the requested research task has been completed rigorously.
Cite specific sections, figures and tables. Do not invent results or references.

Summarize the question, methods and findings without critique. Assess these dimensions
independently on 1-4 (poor, fair, good, excellent):
- Quality: correctness of methods and calculations, support for claims, appropriate
  evaluation, controls, uncertainty and reproducibility. Request baselines, repeated
  seeds, training details or ablations only when relevant to the methods and claims.
- Clarity: intelligibility, organization and sufficient detail to understand and
  reproduce the reported work. Judge length by completeness for the actual question.
- Significance: usefulness and relevance of the findings within the requested scope;
  distinguish this from broader scientific impact.
- Originality: novelty of methods, analysis or interpretation relative to prior work.
  Report limited novelty honestly and independently. Low originality alone must not
  reject an otherwise correct comparison, reproduction or educational task, or lower
  its quality and clarity scores. Do not invent novelty to raise an assessment.

List actionable questions, explain the consequences of missing evidence and identify
limitations. Reward honest limitations; do not treat unsupported claims as established.
Report formatting issues separately from scientific weaknesses. Specify additional
checks only when needed to support the actual claims or explicitly requested venue.

Set overall on 1-6 according to fulfillment of the requested scope and evidence:
6: Strong accept - fully supported conclusions, excellent execution and reproducibility.
5: Accept - the task is fulfilled with sound methods and evidence; minor limitations.
4: Borderline accept - mostly sound, with bounded gaps that need clarification.
3: Borderline reject - material gaps prevent trusting part of the conclusions.
2: Reject - major methodological errors or unsupported central claims.
1: Strong reject - fundamentally invalid work or serious unresolved ethical issues.
These labels describe the assessment, not an actual venue acceptance decision.
Set confidence on 1-5 (educated guess, low, moderate, high, very high), based on what
was actually checked and available evidence. Do not claim to have run code or verified
references unless those checks were performed.

Return exactly the existing JSON review schema:
summary; quality, clarity, significance, originality each containing strengths,
weaknesses, reasoning and score; questions; limitations; overall and confidence each
containing reasoning and score; format_issues. Use arrays for strengths, weaknesses,
questions, limitations and format_issues. No additional fields.
"""


CHEMISTRY_PROMPT = """
You are a fair, constructive peer reviewer of chemistry research and laboratory-data analysis.
Assess the work against its stated question and actual evidence. Cite specific sections,
tables or figures; separate formatting issues from scientific concerns. A concise report
can be complete. Judge length by what the question and evidence require.
Check chemical identifiers and representations, units, source data, transformations,
calculation methods, reproducibility, uncertainty and applicability limits. Distinguish
measured values, calculated descriptors, heuristic estimates and model predictions.
Accuracy claims for predictions require relevant validation. Training, multiple random
seeds, baselines or ablations are needed only when justified by the method and claims.
Do not invent missing results or references; identify gaps and explain their consequences.
Summarize the question and findings, then assess quality, clarity, significance and
originality independently on 1-4 (poor, fair, good, excellent). Useful new analyses or
interpretations can contribute without a new algorithm. Give actionable questions and
limitations. Set overall on 1-6 (strong reject, reject, borderline reject, borderline
accept, accept, strong accept), and confidence on 1-5 (educated guess through certain).
Return exactly the existing JSON review schema:
summary; quality, clarity, significance, originality each containing strengths,
weaknesses, reasoning and score; questions; limitations; overall and confidence each
containing reasoning and score; format_issues. Use arrays for strengths, weaknesses,
questions, limitations and format_issues. No additional fields.
"""


class AspectItem(BaseModel):  # type: ignore
    strengths: list[str] = Field(
        description="Strengths of the paper in a specific aspect"
    )
    weaknesses: list[str] = Field(
        description="Weaknesses of the paper in a specific aspect"
    )
    reasoning: str = Field(description="Reasoning about this aspect")
    score: int = Field(ge=1, le=4, description="Score of this aspect (1-4)")


class ReasoningItem(BaseModel):  # type: ignore
    reasoning: str = Field(description="Reasoning about this aspect")
    score: int = Field(ge=1, le=6, description="Overall score (1-6)")


class ConfidenceItem(ReasoningItem):
    score: int = Field(ge=1, le=5, description="Confidence score (1-5)")


class ReviewResponse(BaseModel):  # type: ignore
    summary: str = Field(description="Summary of the paper")
    quality: AspectItem = Field(description="Quality of the paper")
    clarity: AspectItem = Field(description="Clarity of the paper")
    significance: AspectItem = Field(description="Significance of the paper")
    originality: AspectItem = Field(description="Originality of the paper")
    questions: list[str] = Field(
        description="Questions and suggestions for the authors"
    )
    limitations: list[str] = Field(description="Limitations of the paper")
    overall: ReasoningItem = Field(description="Overall score and reasoning")
    confidence: ConfidenceItem = Field(description="Confidence score and reasoning")
    format_issues: list[str] = Field(description="Format issues")


def _validate_review_outputs(directory: Path) -> None:
    for name in ("review.json", "review.md"):
        target = directory / name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError(f"Invalid review artifact path: {target}")


@dataclass(frozen=True)
class _ReviewTarget:
    directory: Path
    pdf_path: str
    pdf_sha256: str
    check_run_id: str | None


def _review_target(pdf_path: Path) -> _ReviewTarget | None:
    """Resolve ownership before following aliases, so other sessions cannot claim it."""
    if settings.WORKSPACE_DIR is None:
        return None
    workspace = Path(settings.WORKSPACE_DIR).resolve()
    try:
        relative = pdf_path.relative_to(workspace)
    except ValueError:
        return None
    if len(relative.parts) < 2 or ".." in relative.parts:
        return None
    session_id = relative.parts[0]
    directory = workspace / session_id
    if (
        directory.is_symlink()
        or session_id in {"examples", "inputs", "figures"}
        or not re.fullmatch(r"[^\W_][\w.-]{0,199}", session_id)
    ):
        return None
    registry = PaperRegistry(workspace)
    try:
        resolved_pdf = registry.resolve_file(
            session_id, Path(*relative.parts[1:]).as_posix(), suffix=".pdf"
        )
    except ValueError:
        # A source outside this session may be reviewed, but never owns its output.
        return None
    record = registry.read(session_id)
    if record is None:
        if (
            relative.parts[1:] != ("paper.pdf",)
            or pdf_path.is_symlink()
            or pdf_path.resolve().parent != directory
        ):
            return None
        check_run_id = None
        registered_pdf = "paper.pdf"
    else:
        registered_pdf = record["pdf_path"]
        if resolved_pdf != registry.resolve_file(session_id, registered_pdf, suffix=".pdf"):
            return None
        check_run_id = record["check_run_id"]
    _validate_review_outputs(directory)
    return _ReviewTarget(
        directory=directory,
        pdf_path=registered_pdf,
        pdf_sha256=file_sha256(resolved_pdf),
        check_run_id=check_run_id,
    )


def _review_markdown(result: ReviewResponse, response_language: str) -> str:
    labels = (
        (
            "Рецензия",
            "Краткое содержание",
            "Качество",
            "Ясность",
            "Значимость",
            "Оригинальность",
            "Сильные стороны",
            "Недостатки",
            "Вопросы и рекомендации",
            "Ограничения",
            "Итоговая оценка",
            "Уверенность",
            "Замечания к оформлению",
        )
        if response_language == "ru"
        else (
            "Review",
            "Summary",
            "Quality",
            "Clarity",
            "Significance",
            "Originality",
            "Strengths",
            "Weaknesses",
            "Questions and recommendations",
            "Limitations",
            "Overall assessment",
            "Confidence",
            "Formatting issues",
        )
    )
    lines = [f"# {labels[0]}", "", f"## {labels[1]}", "", result.summary, ""]
    for name, label in zip(
        ("quality", "clarity", "significance", "originality"), labels[2:6]
    ):
        aspect = getattr(result, name)
        lines.extend([f"## {label}: {aspect.score}/4", "", aspect.reasoning, ""])
        for heading, items in (
            (labels[6], aspect.strengths),
            (labels[7], aspect.weaknesses),
        ):
            if items:
                lines.extend(
                    [f"### {heading}", "", *(f"- {item}" for item in items), ""]
                )
    for label, items in (
        (labels[8], result.questions),
        (labels[9], result.limitations),
    ):
        lines.extend([f"## {label}", "", *(f"- {item}" for item in items), ""])
    for label, item, maximum in (
        (labels[10], result.overall, 6),
        (labels[11], result.confidence, 5),
    ):
        lines.extend([f"## {label}: {item.score}/{maximum}", "", item.reasoning, ""])
    lines.extend(
        [f"## {labels[12]}", "", *(f"- {item}" for item in result.format_issues), ""]
    )
    return "\n".join(lines)


def _save_review(
    directory: Path, result: ReviewResponse, response_language: str
) -> None:
    artifacts = {
        "review.json": json.dumps(
            result.model_dump(mode="json"), ensure_ascii=False, indent=2
        )
        + "\n",
        "review.md": _review_markdown(result, response_language),
    }
    # Replace whole files so readers never see partial JSON and existing hard links
    # cannot redirect writes to unrelated files. The review scores are never changed.
    pending: list[tuple[Path, Path]] = []
    try:
        for name, content in artifacts.items():
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=directory,
                prefix=f".{name}.",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                pending.append((temporary, directory / name))
                handle.write(content)
        for temporary, target in pending:
            # Academia and the host runner may use different Unix users.
            temporary.chmod(0o644)
            temporary.replace(target)
    finally:
        for temporary, _ in pending:
            temporary.unlink(missing_ok=True)


def _create_pdf_filename(pdf_url: str) -> str:
    if "arxiv.org/pdf" in pdf_url:
        pdf_filename = pdf_url.split("/")[-1]
    else:
        pdf_filename = str(uuid.uuid4())
    if not pdf_filename.endswith(".pdf"):
        pdf_filename += ".pdf"
    return pdf_filename


def download_pdf_paper(pdf_url: str, session_id: str | None = None) -> str:
    """
    Download a pdf file from a url to the workspace directory.

    Returns the path to the downloaded pdf file.

    Args:
        pdf_url: The url of the pdf file.
        session_id: Optional session directory; returned path is workspace-relative.
    """
    pdf_filename = _create_pdf_filename(pdf_url)
    workspace = Path(get_workspace_dir()).resolve()
    directory = workspace
    if session_id is not None:
        if (
            not re.fullmatch(r"[^\W_][\w.-]{0,199}", session_id)
            or session_id == "figures"
        ):
            raise ValueError("Invalid session id")
        directory = workspace / session_id
        if directory.is_symlink() or directory.resolve().parent != workspace:
            raise ValueError("Invalid session directory")
        directory.mkdir(parents=True, exist_ok=True)
    pdf_path = directory / pdf_filename
    if session_id is not None and pdf_path.is_symlink():
        raise ValueError("Invalid PDF path")
    download_pdf(pdf_url, pdf_path)
    return str(pdf_path.relative_to(workspace))


async def review_pdf_paper(
    pdf_filename: str,
    research_profile: Literal["general", "chemistry"] = "general",
    response_language: Literal["ru", "en"] = "ru",
    task_context: str | None = None,
    target_venue: str | None = None,
) -> ReviewResponse:
    """
    Review a pdf file with a paper.
    It sends PDF text and page images to the configured external review model.
    It can detect different issues with the paper formatting.
    Returns a structured review with the selected profile and unchanged score scales.
    For the registered session article, also saves review.json and review.md in the
    session root and associates them with that article. Without a registration,
    WORKSPACE_DIR/<session>/paper.pdf retains this behavior. Other source PDFs are
    reviewed without replacing the session's review artifacts.

    Args:
        pdf_filename: The path to the pdf file.
        research_profile: Computer-science or chemistry research review.
        response_language: Language of review text (ru by default, en if explicitly requested).
        task_context: Concise original assignment and scope, when available.
        target_venue: Venue criteria only if the user explicitly requested that venue.
    """
    pdf_filename_path = Path(pdf_filename)
    if not pdf_filename_path.exists():
        pdf_filename_path = Path(get_workspace_dir()) / pdf_filename
    pdf_filename_path = pdf_filename_path.absolute()

    target = _review_target(pdf_filename_path)

    images = parse_pdf_file_to_images(pdf_filename_path)
    text = "\n\n\n".join(parse_pdf_file(pdf_filename_path))
    content_parts: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": "Paper text:\n\n" + text,
        }
    ]
    for image in images:
        buffer_io = BytesIO()
        image.save(buffer_io, format="PNG")
        img_bytes = buffer_io.getvalue()
        image_base64 = base64.b64encode(img_bytes).decode("utf-8")
        image_content = {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{image_base64}"},
        }
        content_parts.append(image_content)

    language = "Russian" if response_language == "ru" else "English"
    instructions = (
        f"Write all review text values in {language}, regardless of the PDF language. "
        "Preserve JSON keys, source titles, identifiers and quotations.\n"
    )
    if task_context:
        instructions += "Requested task and scope:\n" + task_context + "\n"
    if target_venue:
        instructions += "Explicitly requested target venue:\n" + target_venue + "\n"
    else:
        instructions += "No target venue was requested; apply no venue-specific acceptance threshold.\n"
    content_parts.append(
        {
            "type": "text",
            "text": "####\n\nInstructions:\n\n"
            + instructions
            + (CHEMISTRY_PROMPT if research_profile == "chemistry" else PROMPT),
        }
    )
    model_name = settings.REVIEW_MODEL_NAME
    messages = [
        ChatMessage(role="user", content=content_parts),
    ]
    result: ReviewResponse = await llm_acall_structured(
        model_name=model_name,
        messages=messages,
        response_format=ReviewResponse,
        max_completion_tokens=settings.REVIEW_MAX_COMPLETION_TOKENS,
        temperature=0.1,
    )
    if target is not None:
        try:
            current_target = _review_target(pdf_filename_path)
        except (OSError, ValueError) as exc:
            raise ValueError(
                "Paper or registration changed during review; review again"
            ) from exc
        if current_target != target:
            raise ValueError("Paper or registration changed during review; review again")
        if target.check_run_id is not None:
            PaperRegistry(target.directory.parent).record_review(
                target.directory.name,
                target.pdf_path,
                "review.json",
                "review.md",
                expected_check_run_id=target.check_run_id,
                pdf_sha256=target.pdf_sha256,
                save=lambda: _save_review(target.directory, result, response_language),
            )
        else:
            _save_review(target.directory, result, response_language)
    return result


async def review_pdf_paper_by_url(
    pdf_url: str,
    research_profile: Literal["general", "chemistry"] = "general",
    response_language: Literal["ru", "en"] = "ru",
    task_context: str | None = None,
    target_venue: str | None = None,
) -> ReviewResponse:
    """
    Review a pdf file with a paper by url.
    It downloads the pdf file and then reviews it.
    It parses the pdf file into images and then sends the images to the LLM for review.
    It can detect different issues with the paper formatting.
    Returns a structured review in Russian unless English is explicitly requested.
    This temporary source PDF does not produce canonical session review artifacts.

    Args:
        pdf_url: The url of the pdf file.
        research_profile: Computer-science or chemistry research review.
        response_language: Review text language (ru or en), independent of PDF language.
        task_context: Concise original assignment and scope, when available.
        target_venue: Venue criteria only if explicitly requested by the user.
    """
    pdf_filename = _create_pdf_filename(pdf_url)
    with tempfile.TemporaryDirectory(prefix="temp_pdf_") as temp_dir:
        pdf_path = Path(temp_dir) / pdf_filename
        download_pdf(pdf_url, pdf_path)
        return await review_pdf_paper(
            str(pdf_path),
            research_profile,
            response_language,
            task_context,
            target_venue,
        )
