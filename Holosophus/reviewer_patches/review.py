import base64
import hashlib
import logging
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from dotenv import load_dotenv
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from academia_mcp.files import get_workspace_dir
from academia_mcp.pdf import download_pdf, parse_pdf_file, parse_pdf_file_to_images
from settings import settings

from prompts import REVIEW_PROMPT
from models import (
    ReviewResponse,
    ExtendedReviewResponse,
    REVIEW_MODE_GENERAL,
    REVIEW_MODE_WRITER_SPECIFIC_JUDGE,
    ReviewMode,
    compute_overall_score,
    map_to_overall_score,
    validate_review_mode,
)
from section_parser import parse_sections_from_text
from a3pr_pipeline import run_a3pr_review
from self_critique import review_with_self_critique
from writer_specific_judge import GroupedRubricReviewResponse, run_grouped_rubric_review

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

load_dotenv()
@dataclass
class ReviewMetrics:
    """Metrics for a single review operation."""

    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    pdf_path: str = ""
    pdf_size_bytes: int = 0
    num_pages: int = 0
    text_length: int = 0
    num_images: int = 0
    latency_pdf_parse: float = 0.0
    latency_images: float = 0.0
    latency_llm: float = 0.0
    latency_total: float = 0.0
    tokens_input: int = 0
    tokens_output: int = 0
    tokens_total: int = 0
    retry_count: int = 0
    model_name: str = ""
    temperature: float = 0.0
    cost_usd: float = 0.0
    eval_mode: bool = False
    raw_response: Optional[str] = None
    validation_errors: List[str] = field(default_factory=list)
    scores: Dict[str, int] = field(default_factory=dict)


_review_cache: Dict[str, tuple[ReviewResponse, ReviewMetrics]] = {}


def _compute_pdf_hash(pdf_path: Path) -> str:
    """Compute SHA256 hash of PDF file for caching."""
    with open(pdf_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD based on OpenRouter pricing (approximate)."""
    pricing = {
        "openai/gpt-5": (2.5, 10.0),  # (input, output) per 1M tokens
        "openai/gpt-4o": (2.5, 10.0),
        "anthropic/claude-3.5-sonnet": (3.0, 15.0),
        "default": (1.0, 3.0),
    }
    input_price, output_price = pricing.get(model, pricing["default"])
    cost = (input_tokens / 1_000_000) * input_price + (
        output_tokens / 1_000_000
    ) * output_price
    return round(cost, 6)


async def llm_acall_structured(
    model_name: str,
    messages: List[Dict[str, Any]],
    response_format: type[T],
    num_parsing_retries: int = 3,
    temperature: float = 0.0,
    seed: Optional[int] = None,
    metrics: Optional[ReviewMetrics] = None,
    **kwargs: Any,
) -> T:
    """Structured LLM call with JSON parsing, retry logic, and metrics."""
    key = os.getenv("OPENROUTER_API_KEY") 
    assert key, "Please set OPENROUTER_API_KEY in the environment variables"
    base_url = settings.BASE_URL

    client = AsyncOpenAI(base_url=base_url, api_key=key)

    if temperature == 0:
        kwargs["top_p"] = 1.0
        if seed is not None:
            kwargs["seed"] = seed

    last_error = None
    for retry_index in range(num_parsing_retries):
        try:
            start_time = time.time()
            response = await client.chat.completions.parse(
                model=model_name,
                messages=messages,
                response_format=response_format,
                temperature=temperature,
                **kwargs,
            )
            latency = time.time() - start_time

            if metrics:
                metrics.latency_llm = latency
                metrics.retry_count = retry_index + 1

                usage = response.usage
                if usage:
                    metrics.tokens_input = usage.prompt_tokens
                    metrics.tokens_output = usage.completion_tokens
                    metrics.tokens_total = usage.total_tokens
                    metrics.cost_usd = _estimate_cost(
                        model_name, metrics.tokens_input, metrics.tokens_output
                    )

            structured_response = response.choices[0].message.parsed
            assert structured_response

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    f"Raw LLM response: {response.choices[0].message.content[:500]}..."
                )

            return structured_response
        except Exception as e:
            last_error = e
            if metrics:
                metrics.retry_count = retry_index + 1
                metrics.validation_errors.append(str(e))
            logger.warning(
                f"LLM parse retry {retry_index + 1}/{num_parsing_retries}: {e}"
            )
            if retry_index == num_parsing_retries - 1:
                logger.error(
                    f"LLM parse failed after {num_parsing_retries} retries: {e}"
                )
                raise
            continue

    raise RuntimeError(f"LLM parse failed: {last_error}")


def _create_pdf_filename(pdf_url: str) -> str:
    if "arxiv.org/pdf" in pdf_url:
        pdf_filename = pdf_url.split("/")[-1]
    else:
        pdf_filename = str(uuid.uuid4())
    if not pdf_filename.endswith(".pdf"):
        pdf_filename += ".pdf"
    return pdf_filename


def download_pdf_paper(pdf_url: str) -> str:
    pdf_filename = _create_pdf_filename(pdf_url)
    pdf_path = Path(get_workspace_dir()) / pdf_filename
    download_pdf(pdf_url, pdf_path)
    return pdf_filename


async def review_pdf_paper(
    pdf_filename: str,
    eval_mode: bool = False,
    use_images: bool = True,
    deterministic: bool = True,
    seed: Optional[int] = 42,
    use_cache: bool = True,
    use_sections: bool = True,
    use_a3pr: bool = False,
    use_self_critique: bool = False,
    use_arxiv_search: bool = True,
    review_mode: ReviewMode = REVIEW_MODE_GENERAL,
    # writer_specific_judge kwargs forwarded to run_grouped_rubric_review
    paper_type: Optional[str] = None,
    paper_id: Optional[str] = None,
    rubric_path: Optional[str] = None,
) -> "ExtendedReviewResponse | GroupedRubricReviewResponse":
    """Review a PDF paper using the requested *review_mode*.

    Parameters
    ----------
    review_mode:
        ``"general"`` (default) — existing 7-criterion extended review.
        ``"writer_specific_judge"`` — grouped-rubric LLM-as-a-judge review
        powered by the writer benchmark rubric.  Returns a
        :class:`GroupedRubricReviewResponse` in this case.

    All other parameters are forwarded to the relevant pipeline.
    ``paper_type``, ``paper_id``, and ``rubric_path`` are only used when
    *review_mode* is ``"writer_specific_judge"``.

    Raises
    ------
    ValueError
        If *review_mode* is not a recognised mode constant.
    """
    validate_review_mode(review_mode)

    # ------------------------------------------------------------------
    # Writer-specific judge path — extract text then delegate to llm_judge
    # ------------------------------------------------------------------
    if review_mode == REVIEW_MODE_WRITER_SPECIFIC_JUDGE:
        return await _run_writer_specific_judge(
            pdf_filename=pdf_filename,
            paper_id=paper_id,
            paper_type=paper_type,
            rubric_path=rubric_path,
            use_arxiv_search=use_arxiv_search,
            model_name=None,
        )

    # ------------------------------------------------------------------
    # General review path (default) — existing 7-criterion extended review
    # ------------------------------------------------------------------
    start_total = time.time()
    metrics = ReviewMetrics(eval_mode=eval_mode) if eval_mode else None

    pdf_filename_path = Path(pdf_filename)
    if not pdf_filename_path.exists():
        pdf_filename_path = Path(get_workspace_dir()) / pdf_filename

    metrics.pdf_path = str(pdf_filename_path) if metrics else ""
    if pdf_filename_path.exists():
        metrics.pdf_size_bytes = pdf_filename_path.stat().st_size if metrics else 0

    if use_cache and pdf_filename_path.exists():
        pdf_hash = _compute_pdf_hash(pdf_filename_path)
        if pdf_hash in _review_cache:
            elapsed_cached = time.time() - start_total
            cached_response, cached_metrics = _review_cache[pdf_hash]
            if metrics:
                metrics.request_id = cached_metrics.request_id
                metrics.cost_usd = cached_metrics.cost_usd  # Already paid
                metrics.latency_total = elapsed_cached
            speedup = cached_metrics.latency_total / elapsed_cached if elapsed_cached > 0 and cached_metrics.latency_total > 0 else float('inf')
            logger.info(
                f"Cache hit for PDF hash {pdf_hash[:16]}...: "
                f"{elapsed_cached:.2f}s vs {cached_metrics.latency_total:.2f}s original "
                f"(speedup: {speedup:.1f}x, saved ${cached_metrics.cost_usd:.6f})"
            )
            return cached_response

    start_parse = time.time()
    text_pages = parse_pdf_file(pdf_filename_path)
    full_text = "\n\n\n".join(text_pages)

    if use_sections:
        logger.info("Parsing paper into sections...")
        parsed_paper = parse_sections_from_text(full_text)
        paper_text_for_prompt = parsed_paper.to_structured_prompt()
        if metrics:
            metrics.text_length = len(parsed_paper.get_main_text(include_references=False))
    else:
        parsed_paper = None
        paper_text_for_prompt = full_text
        if metrics:
            metrics.text_length = len(full_text)
    if metrics:
        metrics.latency_pdf_parse = time.time() - start_parse
        metrics.num_pages = len(text_pages)

    images = []
    if use_images:
        start_images = time.time()
        images = parse_pdf_file_to_images(pdf_filename_path)
        if metrics:
            metrics.latency_images = time.time() - start_images
            metrics.num_images = len(images)

    if use_a3pr and parsed_paper:
        logger.info("Using A3PR 4-stage pipeline...")

        async def run_pipeline():
            return await run_a3pr_review(
                parsed_paper=parsed_paper,
                use_arxiv_search=use_arxiv_search,
                model_name=settings.REVIEW_MODEL_NAME,
            )

        if use_self_critique:
            result = await review_with_self_critique(
                run_pipeline,
                max_iterations=2,
                min_consistency_threshold=0.8,
            )
        else:
            result = await run_pipeline()

    else:
        logger.info("Using legacy single-call approach...")

        content_parts: List[Dict[str, Any]] = [
            {"type": "text", "text": "Paper text:\n\n" + paper_text_for_prompt}
        ]

        for image in images:
            buffer_io = BytesIO()
            image.save(buffer_io, format="PNG")
            img_bytes = buffer_io.getvalue()
            image_base64 = base64.b64encode(img_bytes).decode("utf-8")
            content_parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_base64}"},
                }
            )

        content_parts.append(
            {
                "type": "text",
                "text": "####\n\nInstructions:\n\n" + REVIEW_PROMPT,
            }
        )

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Prompt length: {len(content_parts[-1]['text'])} chars")

        model_name = settings.REVIEW_MODEL_NAME
        messages = [{"role": "user", "content": content_parts}]

        if metrics:
            metrics.model_name = model_name
            metrics.temperature = 0.0 if deterministic else 0.1

        temperature = 0.0 if deterministic else 0.1
        result = await llm_acall_structured(
            model_name=model_name,
            messages=messages,
            response_format=ExtendedReviewResponse,
            max_completion_tokens=settings.REVIEW_MAX_COMPLETION_TOKENS,
            temperature=temperature,
            seed=seed if deterministic else None,
            metrics=metrics,
        )

    scores_7_criteria = {
        "originality": result.originality.score,
        "importance": result.importance.score,
        "claim_support": result.claim_support.score,
        "experimental_soundness": result.experimental_soundness.score,
        "clarity": result.clarity.score,
        "community_value": result.community_value.score,
        "contextualization": result.contextualization.score,
    }
    overall_score_raw = compute_overall_score(scores_7_criteria)
    overall_score_weighted = map_to_overall_score(overall_score_raw)

    # Store weighted score; LLM's direct overall.score is preserved as independent signal
    result.overall_score_raw = overall_score_raw

    logger.info(
        f"Overall — LLM direct: {result.overall.score}/10, "
        f"weighted (7 criteria): {overall_score_weighted}/10 (raw {overall_score_raw:.2f}/4)"
    )

    if metrics:
        metrics.num_images = len(images)
    metrics.latency_total = time.time() - start_total if metrics else 0.0

    # Extract scores for metrics
    if metrics:
        metrics.scores = {
            "originality": result.originality.score,
            "importance": result.importance.score,
            "claim_support": result.claim_support.score,
            "experimental_soundness": result.experimental_soundness.score,
            "clarity": result.clarity.score,
            "community_value": result.community_value.score,
            "contextualization": result.contextualization.score,
            "overall": result.overall.score,
            "confidence": result.confidence.score,
            "overall_raw": int(result.overall_score_raw),
        }
        logger.info(
            f"Review completed: scores={metrics.scores}, "
            f"tokens={metrics.tokens_total}, cost=${metrics.cost_usd:.6f}, "
            f"latency={metrics.latency_total:.2f}s"
        )

    # Cache result
    if use_cache and pdf_filename_path.exists():
        pdf_hash = _compute_pdf_hash(pdf_filename_path)
        _review_cache[pdf_hash] = (
            (result, metrics) if metrics else (result, ReviewMetrics())
        )

    return result


async def _run_writer_specific_judge(
    pdf_filename: str,
    *,
    paper_id: Optional[str] = None,
    paper_type: Optional[str] = None,
    rubric_path: Optional[str] = None,
    use_arxiv_search: bool = True,
    model_name: Optional[str] = None,
) -> GroupedRubricReviewResponse:
    """Extract paper text from *pdf_filename* and run the grouped-rubric judge."""
    pdf_filename_path = Path(pdf_filename)
    if not pdf_filename_path.exists():
        pdf_filename_path = Path(get_workspace_dir()) / pdf_filename

    from academia_mcp.pdf import parse_pdf_file as _parse_pdf_file

    text_pages = _parse_pdf_file(pdf_filename_path)
    paper_text = "\n\n\n".join(text_pages)

    return await run_grouped_rubric_review(
        paper_text,
        paper_id=paper_id,
        paper_type=paper_type,
        rubric_path=rubric_path,
        auto_related_work=use_arxiv_search,
        model_name=model_name,
    )


async def review_pdf_paper_by_url(
    pdf_url: str,
    eval_mode: bool = False,
    use_images: bool = True,
    deterministic: bool = True,
    seed: Optional[int] = 42,
    use_cache: bool = True,
    review_mode: ReviewMode = REVIEW_MODE_GENERAL,
    paper_type: Optional[str] = None,
    paper_id: Optional[str] = None,
    rubric_path: Optional[str] = None,
) -> "ExtendedReviewResponse | GroupedRubricReviewResponse":
    """Review a PDF paper by URL using the requested *review_mode*.

    Downloads the PDF to a temporary directory and calls :func:`review_pdf_paper`.
    """
    pdf_filename = _create_pdf_filename(pdf_url)
    with tempfile.TemporaryDirectory(prefix="temp_pdf_") as temp_dir:
        pdf_path = Path(temp_dir) / pdf_filename
        download_pdf(pdf_url, pdf_path)
        return await review_pdf_paper(
            str(pdf_path),
            eval_mode=eval_mode,
            use_images=use_images,
            deterministic=deterministic,
            seed=seed,
            use_cache=use_cache,
            review_mode=review_mode,
            paper_type=paper_type,
            paper_id=paper_id,
            rubric_path=rubric_path,
        )


__all__ = [
    "ReviewMetrics",
    "review_pdf_paper",
    "review_pdf_paper_by_url",
    "download_pdf_paper",
    "_compute_pdf_hash",
]

