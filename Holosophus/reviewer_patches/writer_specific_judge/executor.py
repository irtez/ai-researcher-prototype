"""Grouped rubric executor: one LLM call per metric group."""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Optional

from openai import AsyncOpenAI

from .aggregation import compute_module_score_details, compute_module_scores
from .definitions import GroupDefinition, PaperType
from .loader import load_grouped_rubric, select_groups_for_paper_type
from .models import GroupedRubricReviewResponse, GroupedRubricSummary, RubricGroupResult
from .paper_type import resolve_paper_type
from .prompts import build_group_prompt
from .related_work import enrich_with_related_work


logger = logging.getLogger(__name__)

GroupCaller = Callable[[GroupDefinition, str], Awaitable[RubricGroupResult]]
RelatedWorkProvider = Callable[[str], Awaitable[str]]


def validate_group_result(
    group: GroupDefinition,
    result: RubricGroupResult,
    *,
    allowed_scores: list[str] | None = None,
) -> RubricGroupResult:
    expected_ids = set(group.criterion_ids)
    returned_ids = {criterion.criterion_id for criterion in result.criterion_results}
    missing = expected_ids - returned_ids
    extra = returned_ids - expected_ids
    if missing or extra:
        raise ValueError(
            f"Group {group.group_id} returned invalid criteria: "
            f"missing={sorted(missing)}, extra={sorted(extra)}"
        )
    if allowed_scores is not None:
        invalid_scores = [
            (criterion.criterion_id, str(criterion.score))
            for criterion in result.criterion_results
            if str(criterion.score) not in allowed_scores
        ]
        if invalid_scores:
            raise ValueError(
                f"Group {group.group_id} returned scores outside rubric scale: {invalid_scores}. "
                f"Allowed scores: {allowed_scores}"
            )
    result.group_id = group.group_id
    result.group_name = group.group_name
    return result


async def openrouter_group_caller(
    group: GroupDefinition,
    prompt: str,
    *,
    model_name: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> RubricGroupResult:
    key = api_key or os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is required for grouped rubric review")

    client = AsyncOpenAI(
        base_url=base_url or os.getenv("BASE_URL", "https://openrouter.ai/api/v1"),
        api_key=key,
    )
    resolved_model_name = (
        model_name
        or os.getenv("REVIEW_MODEL_NAME")
        or os.getenv("MODEL_NAME")
        or "google/gemini-3.5-flash"
    )
    max_completion_tokens = int(os.getenv("REVIEW_MAX_COMPLETION_TOKENS", "16384"))

    response = await client.chat.completions.parse(
        model=resolved_model_name,
        messages=[
            {
                "role": "system",
                "content": "You score only one rubric group and return strict JSON.",
            },
            {"role": "user", "content": prompt},
        ],
        response_format=RubricGroupResult,
        max_completion_tokens=max_completion_tokens,
        temperature=0.0,
    )
    parsed = response.choices[0].message.parsed
    if not parsed:
        raise RuntimeError(f"Empty grouped rubric result for {group.group_id}")
    return parsed


def summarize_grouped_results(group_results: list[RubricGroupResult]) -> GroupedRubricSummary:
    strengths: list[str] = []
    weaknesses: list[str] = []
    severe: list[str] = []

    for group_result in group_results:
        for criterion in group_result.criterion_results:
            item = f"{criterion.criterion_id}: {criterion.comment}"
            if criterion.score == 2 and criterion.comment:
                strengths.append(item)
            elif criterion.score in (0, 1) and criterion.comment:
                weaknesses.append(item)
            if criterion.severity in ("major", "critical") and criterion.comment:
                severe.append(item)

    main_problem = (
        severe[0]
        if severe
        else (weaknesses[0] if weaknesses else "No dominant problem identified by grouped rubric.")
    )
    return GroupedRubricSummary(
        main_problem=main_problem,
        top_strengths=strengths[:5],
        top_weaknesses=weaknesses[:5],
    )


async def run_grouped_rubric_review(
    paper_text: str,
    *,
    paper_id: str | None = None,
    paper_type: str | PaperType | None = None,
    rubric_path: str | Path | None = None,
    related_work: str | None = None,
    auto_related_work: bool = False,
    use_llm_keywords: bool = False,
    use_llm_paper_type: bool = False,
    model_name: Optional[str] = None,
    shuffle_criteria: bool = False,
    shuffle_seed: int | None = 42,
    repeat_count: int = 1,
    group_caller: GroupCaller | None = None,
    related_work_provider: RelatedWorkProvider | None = None,
) -> GroupedRubricReviewResponse:
    if repeat_count < 1:
        raise ValueError("repeat_count must be >= 1")

    rubric = load_grouped_rubric(rubric_path)
    resolved_paper_type = await resolve_paper_type(
        paper_text,
        paper_type=str(paper_type) if paper_type else None,
        use_llm_classifier=use_llm_paper_type,
        model_name=model_name,
    )
    groups = select_groups_for_paper_type(resolved_paper_type, rubric=rubric)
    caller = group_caller

    if related_work is None and auto_related_work:
        if related_work_provider is not None:
            related_work = await related_work_provider(paper_text)
        else:
            related_work = await enrich_with_related_work(
                paper_text,
                use_llm_keywords=use_llm_keywords,
                model_name=model_name,
            )

    logger.info(
        "Running grouped rubric review: paper_type=%s groups=%s repeat_count=%s",
        resolved_paper_type,
        [group.group_id for group in groups],
        repeat_count,
    )

    group_results: list[RubricGroupResult] = []
    for group_index, group in enumerate(groups):
        for repeat_index in range(repeat_count):
            criterion_seed = None
            if shuffle_criteria:
                seed_base = shuffle_seed if shuffle_seed is not None else 0
                criterion_seed = seed_base + group_index * 1000 + repeat_index

            prompt = build_group_prompt(
                rubric,
                group,
                paper_text,
                related_work=related_work,
                shuffle_criteria=shuffle_criteria,
                shuffle_seed=criterion_seed,
            )
            if caller is None:
                result = await openrouter_group_caller(group, prompt, model_name=model_name)
            else:
                result = await caller(group, prompt)

            result = validate_group_result(group, result, allowed_scores=rubric.allowed_scores)
            if repeat_count > 1:
                result.group_summary = (
                    f"Repeat {repeat_index + 1}/{repeat_count}. {result.group_summary}"
                )
            group_results.append(result)

    module_scores = compute_module_scores(group_results, groups)
    module_score_details = compute_module_score_details(group_results, groups)
    summary = summarize_grouped_results(group_results)

    return GroupedRubricReviewResponse(
        paper_id=paper_id,
        paper_type=resolved_paper_type,
        applicable_groups=[group.group_id for group in groups],
        group_results=group_results,
        module_scores=module_scores,
        module_score_details=module_score_details,
        main_problem=summary.main_problem,
        top_strengths=summary.top_strengths,
        top_weaknesses=summary.top_weaknesses,
        overall_discussion_persuasiveness=summary.overall_discussion_persuasiveness,
        overall_recommendation=summary.overall_recommendation,
    )
