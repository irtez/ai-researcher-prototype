"""Adapter to convert ICLR reviews to our ExtendedReviewResponse format."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from models import ExtendedReviewResponse, AspectItem, ReasoningItem, WEIGHTS, compute_overall_score, map_to_overall_score

logger = logging.getLogger(__name__)


class ICLRRawReview(BaseModel):
    """Raw ICLR review from JSON."""
    summary: str = ""
    strengths: List[str] = []
    weaknesses: List[str] = []
    questions: List[str] = []
    rating: int = 1  # 1-10 scale
    confidence: int = 1  # 1-5 scale
    soundness: Optional[int] = None  # 1-4 scale
    presentation: Optional[int] = None  # 1-4 scale
    contribution: Optional[int] = None  # 1-4 scale


def normalize_iclr_to_extended(
    iclr_review: ICLRRawReview,
    paper_title: str = "",
    paper_id: str = "",
) -> ExtendedReviewResponse:
    """
    Convert ICLR review to ExtendedReviewResponse format.
    
    Args:
        iclr_review: Raw ICLR review.
        paper_title: Optional paper title.
        paper_id: Optional paper ID.
    
    Returns:
        ExtendedReviewResponse with normalized scores and mapped fields.
    """
    # Map ICLR fields to our 7 criteria (1-4 scale, matching ICLR native scale)
    # ICLR: soundness (1-4), presentation (1-4), contribution (1-4), rating (1-10)
    # Ours: originality, importance, claim_support, experimental_soundness, clarity,
    #        community_value, contextualization

    soundness_4 = max(1, min(4, iclr_review.soundness)) if iclr_review.soundness else 2
    claim_support_score = soundness_4
    experimental_soundness_score = soundness_4

    clarity_score = max(1, min(4, iclr_review.presentation)) if iclr_review.presentation else 2

    contribution_4 = max(1, min(4, iclr_review.contribution)) if iclr_review.contribution else 2
    originality_score = contribution_4
    importance_score = contribution_4
    community_value_score = contribution_4

    # Contextualization has no ICLR equivalent — use neutral midpoint for 1-4 scale
    contextualization_score = 2

    # Overall rating is already 1-10 in ICLR
    overall_10 = max(1, min(10, iclr_review.rating))

    # Confidence (ICLR: 1-5, ours: 1-5)
    confidence_score = min(5, max(1, iclr_review.confidence))
    
    # Create reasoning from strengths/weaknesses
    strengths_text = "\n".join(f"- {s}" for s in iclr_review.strengths[:5]) if iclr_review.strengths else "No major strengths identified."
    weaknesses_text = "\n".join(f"- {w}" for w in iclr_review.weaknesses[:5]) if iclr_review.weaknesses else "No major weaknesses identified."
    
    quality_reasoning = f"Strengths:\n{strengths_text}\n\nWeaknesses:\n{weaknesses_text}"
    
    # Create AspectItem objects
    quality = AspectItem(
        score=experimental_soundness_score,
        reasoning=quality_reasoning,
        strengths=iclr_review.strengths[:5],
        weaknesses=iclr_review.weaknesses[:5],
    )
    
    clarity = AspectItem(
        score=clarity_score,
        reasoning=f"Presentation quality based on ICLR reviewer assessment. {strengths_text if clarity_score >= 3 else weaknesses_text}",
        strengths=iclr_review.strengths[:2] if clarity_score >= 3 else [],
        weaknesses=iclr_review.weaknesses[:2] if clarity_score < 3 else [],
    )
    
    originality = AspectItem(
        score=originality_score,
        reasoning=f"Originality assessment: {originality_score}/4",
        strengths=[s for s in iclr_review.strengths if "novel" in s.lower() or "new" in s.lower()][:3],
        weaknesses=[w for w in iclr_review.weaknesses if "novel" in w.lower() or "limited" in w.lower()][:3],
    )

    importance = AspectItem(
        score=importance_score,
        reasoning=f"Importance of research question: {importance_score}/4",
        strengths=[],
        weaknesses=[],
    )

    claim_support = AspectItem(
        score=claim_support_score,
        reasoning=f"Claim support quality: {claim_support_score}/4",
        strengths=[],
        weaknesses=[],
    )

    experimental_soundness = AspectItem(
        score=experimental_soundness_score,
        reasoning=f"Experimental soundness: {experimental_soundness_score}/4",
        strengths=[s for s in iclr_review.strengths if "experiment" in s.lower() or "result" in s.lower()][:3],
        weaknesses=[w for w in iclr_review.weaknesses if "experiment" in w.lower() or "evaluation" in w.lower()][:3],
    )

    community_value = AspectItem(
        score=community_value_score,
        reasoning=f"Value to research community: {community_value_score}/4",
        strengths=[],
        weaknesses=[],
    )

    contextualization = AspectItem(
        score=contextualization_score,
        reasoning="Contextualization vs prior work: neutral default (not explicitly rated in ICLR)",
        strengths=[],
        weaknesses=[],
    )

    # Compute weighted overall score from 7 criteria (1-10 scale)
    scores_7 = {
        "originality": originality_score,
        "importance": importance_score,
        "claim_support": claim_support_score,
        "experimental_soundness": experimental_soundness_score,
        "clarity": clarity_score,
        "community_value": community_value_score,
        "contextualization": contextualization_score,
    }

    overall_score_raw = compute_overall_score(scores_7)
    overall_score_mapped = map_to_overall_score(overall_score_raw)

    # Use ICLR direct rating when it differs substantially from weighted computation
    iclr_rating_10 = max(1, min(10, iclr_review.rating))
    if abs(overall_score_mapped - iclr_rating_10) > 2:
        logger.warning(
            f"Weighted overall ({overall_score_mapped}) differs from ICLR rating ({iclr_rating_10}) "
            f"by more than 2 points. Using ICLR rating as override."
        )
        overall_score_mapped = iclr_rating_10

    review = ExtendedReviewResponse(
        summary=iclr_review.summary or f"Review of paper: {paper_title}",
        originality=originality,
        importance=importance,
        claim_support=claim_support,
        experimental_soundness=experimental_soundness,
        clarity=clarity,
        community_value=community_value,
        contextualization=contextualization,
        questions=iclr_review.questions[:5] if iclr_review.questions else [],
        limitations=[],
        overall_score_raw=overall_score_raw,
        overall=ReasoningItem(
            score=overall_score_mapped,
            reasoning=f"Weighted score {overall_score_raw:.2f}/4 → {overall_score_mapped}/10. ICLR direct rating: {iclr_rating_10}/10",
        ),
        confidence=ReasoningItem(
            score=confidence_score,
            reasoning=f"Confidence level: {confidence_score}/5",
        ),
        format_issues=[],
    )

    logger.debug(
        f"Converted ICLR review: rating={iclr_review.rating} → overall={overall_score_mapped}/10, "
        f"soundness={soundness_4}/4, presentation={clarity_score}/4, contribution={contribution_4}/4"
    )

    return review


def load_golden_review(golden_json_path: Path) -> ExtendedReviewResponse:
    """
    Load golden review from JSON file and convert to ExtendedReviewResponse.
    
    Args:
        golden_json_path: Path to golden JSON file.
    
    Returns:
        ExtendedReviewResponse object.
    """
    with open(golden_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    logger.info(f"Loaded golden review from {golden_json_path}")
    

    scores = data.get("scores", {})
    overall = scores.get("overall", 3)
    quality = scores.get("quality", 3)
    clarity = scores.get("clarity", 3)
    significance = scores.get("significance", 3)
    originality = scores.get("originality", 3)
    confidence = scores.get("confidence", 4)
    
    # Extract text
    text = data.get("text", {})
    summary = text.get("summary", "")
    strengths = text.get("strengths", [])
    weaknesses = text.get("weaknesses", [])
    questions = text.get("questions", [])
    
    paper_title = data.get("title", golden_json_path.stem)

    def _c4(v: int) -> int:
        return max(1, min(4, int(v)))

    def _c10(v: int) -> int:
        return max(1, min(10, int(v)))

    def _c5(v: int) -> int:
        return max(1, min(5, int(v)))

    quality_4 = _c4(quality)
    clarity_4 = _c4(clarity)
    significance_4 = _c4(significance)
    originality_4 = _c4(originality)
    overall_10 = _c10(overall)
    confidence_5 = _c5(confidence)

    strengths_text = "\n".join(f"- {s}" for s in strengths) if strengths else ""
    weaknesses_text = "\n".join(f"- {w}" for w in weaknesses) if weaknesses else ""
    base_reasoning = f"Strengths:\n{strengths_text}\n\nWeaknesses:\n{weaknesses_text}".strip()

    return ExtendedReviewResponse(
        summary=summary or f"Golden review of paper: {paper_title}",
        originality=AspectItem(
            score=originality_4,
            reasoning=base_reasoning,
            strengths=strengths[:3],
            weaknesses=weaknesses[:3],
        ),
        importance=AspectItem(score=significance_4, reasoning=base_reasoning),
        claim_support=AspectItem(score=quality_4, reasoning=base_reasoning),
        experimental_soundness=AspectItem(
            score=quality_4,
            reasoning=base_reasoning,
            strengths=strengths[:5],
            weaknesses=weaknesses[:5],
        ),
        clarity=AspectItem(score=clarity_4, reasoning=base_reasoning),
        community_value=AspectItem(score=significance_4, reasoning=base_reasoning),
        contextualization=AspectItem(score=2, reasoning="Not explicitly rated in golden set"),
        questions=questions,
        limitations=[],
        overall_score_raw=float(overall_10),
        overall=ReasoningItem(
            score=overall_10,
            reasoning=f"Golden reviewer overall score: {overall_10}/10",
        ),
        confidence=ReasoningItem(
            score=confidence_5,
            reasoning=f"Golden reviewer confidence: {confidence_5}/5",
        ),
        format_issues=[],
    )


def compare_reviews(
    predicted: ExtendedReviewResponse,
    golden: ExtendedReviewResponse,
) -> Dict[str, Any]:
    """
    Compare predicted review with golden review.
    
    Args:
        predicted: Predicted ExtendedReviewResponse.
        golden: Golden ExtendedReviewResponse.
    
    Returns:
        Dict with comparison metrics.
    """
    # Compare overall scores
    overall_diff = abs(predicted.overall.score - golden.overall.score)
    
    # Compare individual criteria
    criteria_comparison = {}
    
    for criterion in ["originality", "importance", "claim_support", "experimental_soundness", "clarity", "community_value", "contextualization"]:
        pred_score = getattr(predicted, criterion).score
        gold_score = getattr(golden, criterion).score
        diff = abs(pred_score - gold_score)
        criteria_comparison[criterion] = {
            "predicted": pred_score,
            "golden": gold_score,
            "diff": diff,
        }
    
    # Calculate MAE
    mae = sum(c["diff"] for c in criteria_comparison.values()) / len(criteria_comparison)
    
    # Compare confidence
    confidence_diff = abs(predicted.confidence.score - golden.confidence.score)
    
    return {
        "overall_match": predicted.overall.score == golden.overall.score,
        "overall_diff": overall_diff,
        "criteria_comparison": criteria_comparison,
        "mae": mae,
        "confidence_diff": confidence_diff,
        "exact_match": overall_diff == 0 and confidence_diff == 0,
    }


if __name__ == "__main__":
    test_iclr = ICLRRawReview(
        summary="This paper presents a novel approach to...",
        strengths=[
            "Novel application of VLMs to procedural materials",
            "Large-scale dataset creation",
            "Solid experimental results",
        ],
        weaknesses=[
            "Limited novelty in augmentation techniques",
            "Some failure cases not well explained",
        ],
        questions=[
            "How does the model handle highly intricate textures?",
            "What is the computational cost compared to baselines?",
        ],
        rating=8,
        confidence=4,
        soundness=3,
        presentation=3,
        contribution=3,
    )

    converted = normalize_iclr_to_extended(test_iclr, "Test Paper", "test_001")

    print(f"\nConverted Review:")
    print(f"  Overall: {converted.overall.score}/10")
    print(f"  Originality: {converted.originality.score}/4")
    print(f"  Clarity: {converted.clarity.score}/4")
    print(f"  Experimental Soundness: {converted.experimental_soundness.score}/4")
    print(f"  Confidence: {converted.confidence.score}/5")
    print(f"\n  Summary: {converted.summary[:100]}...")
    print(f"  Questions: {len(converted.questions)} questions")
