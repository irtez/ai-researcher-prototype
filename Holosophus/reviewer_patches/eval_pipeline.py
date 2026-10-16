"""Evaluation pipeline for reviewer agent: calculate MAE, Spearman correlation, variance."""

import asyncio
import json
import logging
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.stats import spearmanr

from review import ReviewResponse

logger = logging.getLogger(__name__)


@dataclass
class EvalMetrics:
    """Aggregated evaluation metrics for reviewer performance."""
    mae_overall: float = 0.0
    mae_by_aspect: Dict[str, float] = field(default_factory=dict)
    rmse_overall: float = 0.0 
    spearman_overall: float = 0.0
    spearman_p_value: float = 0.0
    pearson_overall: float = 0.0
    
    mean_variance: float = 0.0 
    mean_std: float = 0.0
    
    score_distribution: Dict[str, Dict[int, int]] = field(default_factory=dict)  # aspect -> {score: count}
    
    num_papers: int = 0
    num_reviews: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "accuracy": {
                "mae_overall": self.mae_overall,
                "mae_by_aspect": self.mae_by_aspect,
                "rmse_overall": self.rmse_overall,
            },
            "correlation": {
                "spearman_overall": self.spearman_overall,
                "spearman_p_value": self.spearman_p_value,
                "pearson_overall": self.pearson_overall,
            },
            "consistency": {
                "mean_variance": self.mean_variance,
                "mean_std": self.mean_std,
            },
            "distribution": self.score_distribution,
            "dataset_info": {
                "num_papers": self.num_papers,
                "num_reviews": self.num_reviews,
            }
        }


def calculate_mae(
    predicted_scores: List[int],
    ground_truth_scores: List[int],
) -> float:
    """Calculate Mean Absolute Error."""
    if len(predicted_scores) != len(ground_truth_scores):
        raise ValueError("Score lists must have same length")
    if not predicted_scores:
        return 0.0
    
    errors = [abs(p - g) for p, g in zip(predicted_scores, ground_truth_scores)]
    return statistics.mean(errors)


def calculate_rmse(
    predicted_scores: List[int],
    ground_truth_scores: List[int],
) -> float:
    """Calculate Root Mean Square Error."""
    if len(predicted_scores) != len(ground_truth_scores):
        raise ValueError("Score lists must have same length")
    if not predicted_scores:
        return 0.0
    
    squared_errors = [(p - g) ** 2 for p, g in zip(predicted_scores, ground_truth_scores)]
    return np.sqrt(statistics.mean(squared_errors))


def calculate_spearman(
    predicted_scores: List[int],
    ground_truth_scores: List[int],
) -> Tuple[float, float]:
    """
    Calculate Spearman rank correlation.
    """
    if len(predicted_scores) < 2 or len(ground_truth_scores) < 2:
        logger.warning("Need at least 2 samples for Spearman correlation")
        return 0.0, 1.0
    
    try:
        correlation, p_value = spearmanr(predicted_scores, ground_truth_scores)
        return float(correlation), float(p_value)
    except Exception as e:
        logger.warning(f"Spearman calculation failed: {e}")
        return 0.0, 1.0


def calculate_pearson(
    predicted_scores: List[int],
    ground_truth_scores: List[int],
) -> float:
    """Calculate Pearson correlation coefficient."""
    if len(predicted_scores) < 2 or len(ground_truth_scores) < 2:
        return 0.0
    
    try:
        return float(np.corrcoef(predicted_scores, ground_truth_scores)[0, 1])
    except Exception:
        return 0.0


def calculate_variance(
    scores_list: List[List[int]],
) -> Tuple[float, float]:
    """
    Calculate mean variance and std across multiple review sets.
    """
    if not scores_list:
        return 0.0, 0.0
    
    variances = []
    stds = []
    
    for scores in scores_list:
        if len(scores) > 1:
            variances.append(statistics.variance(scores))
            stds.append(statistics.stdev(scores))
        else:
            variances.append(0.0)
            stds.append(0.0)
    
    return statistics.mean(variances), statistics.mean(stds)


def get_score_distribution(
    reviews: List[ReviewResponse],
    aspect: str = "overall",
) -> Dict[int, int]:
    """Get distribution of scores for a given aspect."""
    distribution: Dict[int, int] = {}
    
    for review in reviews:
        if aspect == "overall":
            score = review.overall.score
        elif aspect == "clarity":
            score = review.clarity.score
        elif aspect == "importance":
            score = review.importance.score
        elif aspect == "originality":
            score = review.originality.score
        elif aspect == "confidence":
            score = review.confidence.score
        else:
            raise ValueError(f"Unknown aspect: {aspect}")
        
        distribution[score] = distribution.get(score, 0) + 1
    
    return distribution


def evaluate_reviews(
    reviews: List[ReviewResponse],
    ground_truth: Optional[Dict[str, Dict[str, int]]] = None,
    ground_truth_file: Optional[str] = None,
) -> EvalMetrics:
    """
    Evaluate reviewer performance against ground truth scores.
    """
    metrics = EvalMetrics(num_papers=len(reviews), num_reviews=len(reviews))
    
    if ground_truth_file and not ground_truth:
        with open(ground_truth_file, "r") as f:
            ground_truth = json.load(f)
    
    aspects = ["clarity", "importance", "originality", "overall", "confidence"]
    for aspect in aspects:
        metrics.score_distribution[aspect] = get_score_distribution(reviews, aspect)
    
    if not ground_truth:
        logger.warning("No ground truth provided, skipping accuracy/correlation metrics")
        return metrics
    
    pred_overall = [r.overall.score for r in reviews]
    pred_clarity = [r.clarity.score for r in reviews]
    pred_importance = [r.importance.score for r in reviews]
    pred_originality = [r.originality.score for r in reviews]
    pred_confidence = [r.confidence.score for r in reviews]
    
    gt_overall = []
    gt_clarity = []
    gt_importance = []
    gt_originality = []
    gt_confidence = []

    for i in range(len(reviews)):
        paper_id = f"paper_{i}"  # Assumes ordered correspondence
        if paper_id in ground_truth:
            gt = ground_truth[paper_id]
            gt_overall.append(gt.get("overall", 0))
            gt_clarity.append(gt.get("clarity", 0))
            gt_importance.append(gt.get("importance", 0))
            gt_originality.append(gt.get("originality", 0))
            gt_confidence.append(gt.get("confidence", 0))
    
    if gt_overall:
        metrics.mae_overall = calculate_mae(pred_overall, gt_overall)
        metrics.rmse_overall = calculate_rmse(pred_overall, gt_overall)
    if gt_clarity:
        metrics.mae_by_aspect["clarity"] = calculate_mae(pred_clarity, gt_clarity)
    if gt_importance:
        metrics.mae_by_aspect["importance"] = calculate_mae(pred_importance, gt_importance)
    if gt_originality:
        metrics.mae_by_aspect["originality"] = calculate_mae(pred_originality, gt_originality)
    if gt_confidence:
        metrics.mae_by_aspect["confidence"] = calculate_mae(pred_confidence, gt_confidence)

    if gt_overall and len(gt_overall) > 1:
        metrics.spearman_overall, metrics.spearman_p_value = calculate_spearman(pred_overall, gt_overall)
        metrics.pearson_overall = calculate_pearson(pred_overall, gt_overall)
    
    logger.info(
        f"Evaluation completed: MAE={metrics.mae_overall:.3f}, "
        f"Spearman={metrics.spearman_overall:.3f} (p={metrics.spearman_p_value:.3f})"
    )
    
    return metrics


async def evaluate_consistency(
    consistency_results: List[Any],  # List of ConsistencyResult
) -> Dict[str, float]:
    """
    Evaluate consistency across multiple reviews of same papers.
    """
    all_variances = []
    all_stds = []
    
    for result in consistency_results:
        for _, scores in result.scores.items():
            if len(scores) > 1:
                all_variances.append(statistics.variance(scores))
                all_stds.append(statistics.stdev(scores))
    
    return {
        "mean_variance": statistics.mean(all_variances) if all_variances else 0.0,
        "mean_std": statistics.mean(all_stds) if all_stds else 0.0,
        "max_variance": max(all_variances) if all_variances else 0.0,
        "max_std": max(all_stds) if all_stds else 0.0,
    }


def save_eval_metrics(
    metrics: EvalMetrics,
    output_file: str,
    reviews: Optional[List[ReviewResponse]] = None,
) -> None:
    output = {
        "metrics": metrics.to_dict(),
    }
    
    if reviews:
        output["reviews"] = [r.model_dump() for r in reviews]
    
    with open(output_file, "w") as f:
        json.dump(output, f, indent=2)
    
    logger.info(f"Saved evaluation metrics to {output_file}")


def load_reviews_from_jsonl(jsonl_file: str) -> List[ReviewResponse]:
    """Load reviews from JSONL file."""
    reviews = []
    with open(jsonl_file, "r") as f:
        for line in f:
            data = json.loads(line)
            if "error" not in data:
                reviews.append(ReviewResponse(**data))
    return reviews
