"""Batch processing and consistency checks for reviewer agent (enhanced with Tier 1 & Tier 2)."""

import json
import logging
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from models import ExtendedReviewResponse, REVIEW_MODE_GENERAL, ReviewMode
from review import (
    ReviewMetrics,
    review_pdf_paper,
)

logger = logging.getLogger(__name__)

@dataclass
class ConsistencyResult:
    """Result of consistency check across multiple reviews."""
    pdf_path: str
    num_reviews: int
    scores: Dict[str, List[int]] = field(default_factory=dict)
    mean_scores: Dict[str, float] = field(default_factory=dict)
    std_scores: Dict[str, float] = field(default_factory=dict)
    aggregated_review: Optional[ExtendedReviewResponse] = None
    individual_reviews: List[ExtendedReviewResponse] = field(default_factory=list)
    metrics: List[ReviewMetrics] = field(default_factory=list)


async def review_with_repeats(
    pdf_path: str,
    n: int = 3,
    use_images: bool = True,
    deterministic: bool = False,
    use_cache: bool = False,
    use_sections: bool = True,
    use_a3pr: bool = False,
    use_self_critique: bool = False,
    use_arxiv_search: bool = True,
    review_mode: ReviewMode = REVIEW_MODE_GENERAL,
) -> ConsistencyResult:
    """
    Run review n times with different seeds to estimate consistency.
    """
    logger.info(f"Running {n} consistency checks for {pdf_path}")

    reviews: List[ExtendedReviewResponse] = []
    metrics_list: List[ReviewMetrics] = []

    for i in range(n):
        seed = 42 + i if deterministic else None
        try:
            review = await review_pdf_paper(
                pdf_path,
                eval_mode=True,
                use_images=use_images,
                deterministic=deterministic,
                seed=seed,
                use_cache=use_cache,
                use_sections=use_sections,
                use_a3pr=use_a3pr,
                use_self_critique=use_self_critique,
                use_arxiv_search=use_arxiv_search,
                review_mode=review_mode,
            )
            reviews.append(review)
            logger.info(f"Review {i + 1}/{n} completed")
        except Exception as e:
            logger.error(f"Review {i + 1}/{n} failed: {e}")
            if i == 0:
                raise
            break

    if not reviews:
        raise RuntimeError("All reviews failed")

    score_keys = [
        "overall",
        "clarity",
        "originality",
        "confidence",
        "importance",
        "claim_support",
        "experimental_soundness",
        "community_value",
        "contextualization",
    ]

    scores_dict: Dict[str, List[int]] = {k: [] for k in score_keys}

    for r in reviews:
        if hasattr(r, "clarity"):
            scores_dict["clarity"].append(r.clarity.score)
        if hasattr(r, "originality"):
            scores_dict["originality"].append(r.originality.score)
        if hasattr(r, "overall"):
            scores_dict["overall"].append(r.overall.score)
        if hasattr(r, "confidence"):
            scores_dict["confidence"].append(r.confidence.score)

        if hasattr(r, "importance"):
            scores_dict["importance"].append(r.importance.score)
        if hasattr(r, "claim_support"):
            scores_dict["claim_support"].append(r.claim_support.score)
        if hasattr(r, "experimental_soundness"):
            scores_dict["experimental_soundness"].append(r.experimental_soundness.score)
        if hasattr(r, "community_value"):
            scores_dict["community_value"].append(r.community_value.score)
        if hasattr(r, "contextualization"):
            scores_dict["contextualization"].append(r.contextualization.score)

    scores_dict = {k: v for k, v in scores_dict.items() if v}

    mean_scores = {k: statistics.mean(v) for k, v in scores_dict.items() if v}
    std_scores = {
        k: statistics.stdev(v) if len(v) > 1 else 0.0 for k, v in scores_dict.items()
    }

    all_weaknesses = []
    all_questions = []
    
    for r in reviews:
        for aspect in [
            "overall",
            "clarity",
            "importance",
            "originality",
            "claim_support",
            "experimental_soundness",
            "community_value",
            "contextualization",
        ]:
            if hasattr(r, aspect):
                aspect_item = getattr(r, aspect)
                if hasattr(aspect_item, "weaknesses"):
                    all_weaknesses.extend(aspect_item.weaknesses)
        if hasattr(r, "questions"):
            all_questions.extend(r.questions)

    first_review = reviews[0]

    for aspect in [
        "overall",
        "clarity",
        "importance",
        "originality",
        "claim_support",
        "experimental_soundness",
        "community_value",
        "contextualization",
    ]:
        if hasattr(first_review, aspect):
            aspect_item = getattr(first_review, aspect)
            if hasattr(aspect_item, "weaknesses"):
                aspect_item.weaknesses = list(set(all_weaknesses))[:10]

    result = ConsistencyResult(
        pdf_path=pdf_path,
        num_reviews=len(reviews),
        scores=scores_dict,
        mean_scores=mean_scores,
        std_scores=std_scores,
        aggregated_review=first_review,
        individual_reviews=reviews,
        metrics=metrics_list,
    )

    logger.info(
        f"Consistency check completed: mean_scores={mean_scores}, "
        f"std_scores={std_scores}"
    )

    return result


async def review_dataset(
    pdf_paths: List[str],
    output_jsonl: str,
    metrics_file: Optional[str] = None,
    use_images: bool = True,
    deterministic: bool = True,
    use_cache: bool = True,
    use_sections: bool = True,
    use_a3pr: bool = False,
    use_self_critique: bool = False,
    use_arxiv_search: bool = True,
    review_mode: ReviewMode = REVIEW_MODE_GENERAL,
) -> List[ExtendedReviewResponse]:
    """
    Review a dataset of PDFs and save results to JSONL.
    """
    logger.info(f"Processing {len(pdf_paths)} PDFs")

    results: List[ExtendedReviewResponse] = []
    metrics_list: List[Dict[str, Any]] = []

    async def process_one(path: str, idx: int) -> None:
        try:
            review = await review_pdf_paper(
                path,
                eval_mode=True,
                use_images=use_images,
                deterministic=deterministic,
                use_cache=use_cache,
                use_sections=use_sections,
                use_a3pr=use_a3pr,
                use_self_critique=use_self_critique,
                use_arxiv_search=use_arxiv_search,
                review_mode=review_mode,
            )
            results.append(review)
            logger.info(f"[{idx + 1}/{len(pdf_paths)}] Completed: {Path(path).name}")
        except Exception as e:
            logger.error(f"[{idx + 1}/{len(pdf_paths)}] Failed {Path(path).name}: {e}")
            results.append(None)

    for i, path in enumerate(pdf_paths):
        await process_one(path, i)

    with open(output_jsonl, "w") as f:
        for i, review in enumerate(results):
            if review:
                f.write(json.dumps(review.model_dump()) + "\n")
            else:
                f.write(
                    json.dumps({"error": f"Failed to process {pdf_paths[i]}"}) + "\n"
                )

    logger.info(f"Saved {len(results)} reviews to {output_jsonl}")

    if metrics_file:
        with open(metrics_file, "w") as f:
            json.dump(
                {
                    "num_papers": len(pdf_paths),
                    "completed": sum(1 for r in results if r),
                    "tier1_features": {
                        "use_sections": use_sections,
                        "use_arxiv_search": use_arxiv_search,
                    },
                    "tier2_features": {
                        "use_a3pr": use_a3pr,
                        "use_self_critique": use_self_critique,
                    },
                },
                f,
                indent=2,
            )
        logger.info(f"Saved metrics to {metrics_file}")

    return results


async def ensemble_review(
    pdf_path: str,
    k: int = 3,
    use_images: bool = True,
    use_sections: bool = True,
    use_a3pr: bool = False,
    use_self_critique: bool = False,
    use_arxiv_search: bool = True,
) -> ConsistencyResult:
    """
    Run k independent reviews and aggregate results.
    """
    return await review_with_repeats(
        pdf_path,
        n=k,
        use_images=use_images,
        deterministic=False,
        use_cache=False,
        use_sections=use_sections,
        use_a3pr=use_a3pr,
        use_self_critique=use_self_critique,
        use_arxiv_search=use_arxiv_search,
    )


async def review_with_faithfulness_check(
    pdf_path: str,
    faithfulness_threshold: float = 0.8,
    use_images: bool = True,
    deterministic: bool = True,
    use_sections: bool = True,
    use_a3pr: bool = False,
    use_self_critique: bool = False,
    use_arxiv_search: bool = True,
) -> tuple[ExtendedReviewResponse, Any]:
    """
    Review a paper and automatically check faithfulness.
    If faithfulness < threshold, run self-critique to improve.
    """
    from faithfulness import check_review_faithfulness
    from review import parse_pdf_file

    logger.info(f"Reviewing {Path(pdf_path).name} with faithfulness check")

    review = await review_pdf_paper(
        pdf_path,
        eval_mode=True,
        use_images=use_images,
        deterministic=deterministic,
        use_cache=False,
        use_sections=use_sections,
        use_a3pr=use_a3pr,
        use_self_critique=use_self_critique,
        use_arxiv_search=use_arxiv_search,
    )

    text_pages = parse_pdf_file(pdf_path)
    paper_text = "\n\n".join(text_pages)

    faithfulness = await check_review_faithfulness(
        paper_text=paper_text,
        review_response=review,
    )

    logger.info(f"Faithfulness score: {faithfulness.faithfulness_score:.2f}")

    if faithfulness.faithfulness_score < faithfulness_threshold:
        logger.warning(
            f"Faithfulness {faithfulness.faithfulness_score:.2f} < {faithfulness_threshold}, "
            f"running self-critique..."
        )

        from self_critique import critique_review

        critique = await critique_review(
            review=review,
            paper_text=paper_text,
        )

        if critique.revised_review:
            logger.info(
                f"Applying {len(critique.corrections_made)} corrections from self-critique"
            )
            review = ExtendedReviewResponse(**critique.revised_review)

    return review, faithfulness
