"""Faithfulness checking: verify review claims against paper text."""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI
from pydantic import BaseModel

from academia_mcp.settings import settings

logger = logging.getLogger(__name__)


class FaithfulnessClaim(BaseModel):
    """A single claim extracted from review."""
    claim_text: str
    category: str
    evidence_required: str


class FaithfulnessCheck(BaseModel):
    """Result of faithfulness verification."""
    claims: List[FaithfulnessClaim]
    verified_claims: List[int]
    hallucinated_claims: List[int]
    ambiguous_claims: List[int]
    faithfulness_score: float
    reasoning: str
    recommendations: List[str]


FAITHFULNESS_PROMPT = """You are verifying the faithfulness of a peer review against the original paper.

# Task
1. Extract key factual claims from the review (methods used, experiments conducted, results reported, comparisons made)
2. For each claim, determine if it is:
   - VERIFIED: Clearly supported by evidence in the paper text
   - HALLUCINATED: Contradicted by or absent from the paper
   - AMBIGUOUS: Unclear or partially supported

# Guidelines
- Be strict: claims without clear textual evidence should be marked as hallucinated or ambiguous
- Focus on factual claims about methods, experiments, and results (not opinions)
- Quote specific sections/pages from the paper when verifying or refuting claims
- Flag claims about missing experiments or baselines as "ambiguous" if the paper doesn't explicitly state scope

# Output Format
Return a JSON object with:
- claims: list of {claim_text, category, evidence_required}
- verified_claims: list of indices (0-based) of verified claims
- hallucinated_claims: list of indices of hallucinated claims
- ambiguous_claims: list of indices of ambiguous claims
- faithfulness_score: float 0.0-1.0 (fraction verified)
- reasoning: brief explanation
- recommendations: list of suggestions for improving review accuracy

Example:
{
    "claims": [
        {"claim_text": "The model uses 5-layer transformer", "category": "method", "evidence_required": "Architecture diagram or methods section"},
        {"claim_text": "Experiments on 3 datasets", "category": "experiment", "evidence_required": "Experimental setup section"}
    ],
    "verified_claims": [0],
    "hallucinated_claims": [1],
    "ambiguous_claims": [],
    "faithfulness_score": 0.5,
    "reasoning": "Claim 1 is correct (Section 3.1), but paper only reports 2 datasets not 3",
    "recommendations": ["Verify dataset count in Table 2", "Check architecture details in Appendix A"]
}
"""


async def check_faithfulness(
    paper_text: str,
    review_text: str,
    model_name: Optional[str] = None,
    max_completion_tokens: int = 10000,
) -> FaithfulnessCheck:
    """
    Check if review claims are faithful to the paper content
    """
    model_name = model_name or settings.REVIEW_MODEL_NAME
    key = settings.OPENROUTER_API_KEY
    assert key, "Please set OPENROUTER_API_KEY in environment"
    base_url = settings.BASE_URL
    
    client = AsyncOpenAI(base_url=base_url, api_key=key)
    
    max_paper_tokens = 80000
    if len(paper_text) > max_paper_tokens:
        logger.warning(f"Truncating paper text from {len(paper_text)} to {max_paper_tokens} chars")
        paper_text = paper_text[:max_paper_tokens] + "... [truncated]"
    
    messages = [
        {
            "role": "system",
            "content": "You are a meticulous fact-checker verifying peer reviews against original papers."
        },
        {
            "role": "user",
            "content": f"""# Paper Text
{paper_text}

# Review to Verify
{review_text}

# Instructions
{FAITHFULNESS_PROMPT}

Analyze the review above against the paper text. Return JSON only."""
        }
    ]
    
    try:
        response = await client.chat.completions.parse(
            model=model_name,
            messages=messages,
            response_format=FaithfulnessCheck,
            max_completion_tokens=max_completion_tokens,
            temperature=0.0,
        )
        
        result = response.choices[0].message.parsed
        assert result
        
        logger.info(
            f"Faithfulness check completed: score={result.faithfulness_score:.2f}, "
            f"verified={len(result.verified_claims)}, hallucinated={len(result.hallucinated_claims)}"
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Faithfulness check failed: {e}")
        return FaithfulnessCheck(
            claims=[],
            verified_claims=[],
            hallucinated_claims=[],
            ambiguous_claims=[],
            faithfulness_score=0.0,
            reasoning=f"Faithfulness check failed: {str(e)}",
            recommendations=["Retry faithfulness check"],
        )


async def check_review_faithfulness(
    paper_text: str,
    review_response: Any,  # ReviewResponse type
    model_name: Optional[str] = None,
) -> FaithfulnessCheck:
    """
    Convenience function to check faithfulness of a ReviewResponse object.
    """
    review_parts = [
        f"Summary: {review_response.summary}",
        f"Originality: {review_response.originality.reasoning}",
        f"Importance: {review_response.importance.reasoning}",
        f"Claim Support: {review_response.claim_support.reasoning}",
        f"Experimental Soundness: {review_response.experimental_soundness.reasoning}",
        f"Clarity: {review_response.clarity.reasoning}",
        f"Community Value: {review_response.community_value.reasoning}",
        f"Contextualization: {review_response.contextualization.reasoning}",
        f"Questions: {'; '.join(review_response.questions)}",
        f"Limitations: {'; '.join(review_response.limitations)}",
    ]
    review_text = "\n\n".join(review_parts)
    
    return await check_faithfulness(paper_text, review_text, model_name)
