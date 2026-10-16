"""Self-critique loop for review quality improvement (Tier 2, Item 5)."""

import json
import logging
import time
from typing import Any, Dict, Optional

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from settings import settings
from models import ExtendedReviewResponse

logger = logging.getLogger(__name__)


class CritiqueResult(BaseModel):
    """Result of self-critique evaluation."""
    issues_found: list[str] = Field(default_factory=list)
    corrections_made: list[str] = Field(default_factory=list)
    consistency_score: float = 0.0
    completeness_score: float = 0.0
    revised_review: Optional[Dict[str, Any]] = None


SELF_CRITIQUE_PROMPT = """You are reviewing a paper review for consistency and completeness.

# Checklist

1. **Score-Reasoning Alignment**: Do the scores align with the written justifications?
   - A score of 2 with positive text is INCONSISTENT
   - A score of 4 with negative text is INCONSISTENT
   - Check each criterion: originality, importance, claim_support, experimental_soundness, clarity, community_value, contextualization

2. **Section Coverage**: Are all major sections of the paper addressed?
   - Methods: Is the approach clearly explained and evaluated?
   - Experiments: Are datasets, baselines, and metrics discussed?
   - Related Work: Is positioning vs prior work assessed?
   - Limitations: Are limitations acknowledged?

3. **Evidence Citations**: Are there specific citations to the paper text for each major claim?
   - "Section 3.2 claims X but Table 2 shows Y" ✓
   - "The method is weak" ✗ (vague, no citation)

4. **Novelty Assessment**: Is the novelty assessment backed by concrete comparison to prior work?
   - Does the review mention specific related papers?
   - Does it explain how this work differs?

5. **Question Quality**: Are the questions to authors answerable (not rhetorical)?
   - "What is the computational cost compared to Baseline Z?" ✓
   - "Why didn't they do better experiments?" ✗ (rhetorical, not actionable)

# Task
If you find inconsistencies or gaps, correct them in the revised review.

# Output Format
Return JSON:
{
    "issues_found": ["Issue 1", "Issue 2"],
    "corrections_made": ["Correction 1", "Correction 2"],
    "consistency_score": 0.85,
    "completeness_score": 0.90,
    "revised_review": {...}  // Full corrected review or null if no changes
}
"""


async def critique_review(
    review: ExtendedReviewResponse,
    paper_text: Optional[str] = None,
    model_name: Optional[str] = None,
) -> CritiqueResult:
    """
    Perform self-critique on a review.
    """
    model_name = model_name or settings.REVIEW_MODEL_NAME
    key = settings.OPENROUTER_API_KEY
    base_url = settings.BASE_URL
    
    client = AsyncOpenAI(base_url=base_url, api_key=key)
    
    review_dict = review.model_dump()
    review_text = json.dumps(review_dict, indent=2)
    
    context = f"# Review to Critique\n\n{review_text}"
    if paper_text:
        if len(paper_text) > 50000:
            paper_text = paper_text[:50000] + "... [truncated]"
        context += f"\n\n# Paper Text (for reference)\n\n{paper_text}"
    
    messages = [
        {"role": "system", "content": "You are a meticulous quality checker for peer reviews."},
        {"role": "user", "content": f"{SELF_CRITIQUE_PROMPT}\n\n{context}"}
    ]
    
    try:
        response = await client.chat.completions.parse(
            model=model_name,
            messages=messages,
            response_format=CritiqueResult,
            max_completion_tokens=3000,
            temperature=0.0,
        )
        
        result = response.choices[0].message.parsed
        assert result
        
        logger.info(
            f"Self-critique completed: {len(result.issues_found)} issues, "
            f"consistency={result.consistency_score:.2f}, completeness={result.completeness_score:.2f}"
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Self-critique failed: {e}")
        return CritiqueResult(
            issues_found=[f"Self-critique failed: {str(e)}"],
            corrections_made=[],
            consistency_score=0.5,
            completeness_score=0.5,
        )


async def review_with_self_critique(
    review_func,
    *args,
    max_iterations: int = 2,
    min_consistency_threshold: float = 0.7,
    **kwargs,
) -> ExtendedReviewResponse:
    """
    Run review with self-critique loop.
    """
    start_total = time.time()
    logger.info(f"Starting review with self-critique (max_iterations={max_iterations})")
    
    review_start = time.time()
    review = await review_func(*args, **kwargs)
    review_elapsed = time.time() - review_start
    logger.info(f"Initial review completed in {review_elapsed:.2f}s")
    
    iteration_times = []
    total_critique_time = 0.0

    for iteration in range(max_iterations):
        critique_start = time.time()
        critique = await critique_review(review)
        critique_elapsed = time.time() - critique_start
        iteration_times.append((iteration + 1, critique_elapsed))
        total_critique_time += critique_elapsed
        
        logger.info(
            f"Iteration {iteration + 1}/{max_iterations} completed in {critique_elapsed:.2f}s: "
            f"consistency={critique.consistency_score:.2f}, "
            f"completeness={critique.completeness_score:.2f}, "
            f"issues={len(critique.issues_found)}"
        )
        
        if critique.consistency_score >= min_consistency_threshold:
            logger.info(f"Review meets quality threshold (consistency={critique.consistency_score:.2f}), stopping")
            break
        
        if critique.revised_review:
            logger.info(f"Applying {len(critique.corrections_made)} corrections")
            try:
                review = ExtendedReviewResponse(**critique.revised_review)
            except Exception as e:
                logger.warning(f"Failed to parse revised review: {e}")
        else:
            logger.info("No corrections provided by critique")
    
    total_elapsed = time.time() - start_total
    num_iterations = len(iteration_times)

    logger.info(f"  Initial review: {review_elapsed:.2f}s")
    logger.info(f"  Critique iterations: {num_iterations}")
    for iter_num, iter_time in iteration_times:
        pct = (iter_time / total_critique_time * 100) if total_critique_time > 0 else 0
        logger.info(f"    Iteration {iter_num}: {iter_time:.2f}s ({pct:5.1f}%)")
    logger.info(f"  Total critique time: {total_critique_time:.2f}s")
    logger.info(f"  TOTAL: {total_elapsed:.2f}s (review: {review_elapsed:.2f}s, critique: {total_critique_time:.2f}s)")

    return review


