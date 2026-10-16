"""ICLR OpenReview dataset loader and normalizer."""

import json
import logging
import os
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import requests
from tqdm import tqdm

logger = logging.getLogger(__name__)


@dataclass
class ICLRReview:
    """Normalized ICLR review in our schema."""
    paper_id: str
    title: str
    pdf_path: str
    decision: Literal["oral", "spotlight", "poster", "reject"]
    category: str
    
    overall: int
    quality: int
    clarity: int
    significance: int
    originality: int
    confidence: int
    
    summary: str = ""
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    questions: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    
    forum_id: str = ""
    openreview_url: str = ""
    num_reviews: int = 0
    avg_rating_raw: float = 0.0
    avg_rating_normalized: float = 0.0
    
    raw_reviews: List[Dict[str, Any]] = field(default_factory=list)
    metareview: Optional[str] = None


def normalize_score_10_to_6(score_10: float) -> int:
    if score_10 <= 2:
        return 1
    elif score_10 <= 4:
        return 2
    elif score_10 == 5:
        return 3
    elif score_10 == 6:
        return 4
    elif score_10 <= 8:
        return 5
    else:  # 9-10
        return 6


def normalize_decision(decision_str: str) -> Literal["oral", "spotlight", "poster", "reject"]:
    decision_lower = decision_str.lower()
    
    if "oral" in decision_lower:
        return "oral"
    elif "spotlight" in decision_lower:
        return "spotlight"
    elif "accept" in decision_lower and "poster" in decision_lower:
        return "poster"
    elif "accept" in decision_lower:
        return "poster"
    elif "reject" in decision_lower or "withdraw" in decision_lower:
        return "reject"
    else:
        logger.warning(f"Unknown decision: {decision_str}, defaulting to 'poster'")
        return "poster"


def extract_text_from_review(review: Dict[str, Any]) -> Dict[str, Any]:
    text_fields = {
        "summary": "",
        "strengths": [],
        "weaknesses": [],
        "questions": [],
    }
    
    if "summary" in review:
        text_fields["summary"] = review["summary"]
    
    if "strengths" in review:
        strengths_text = review["strengths"]
        if isinstance(strengths_text, str):
            text_fields["strengths"] = [
                s.strip().lstrip("-*•").strip()
                for s in strengths_text.split("\n")
                if s.strip()
            ]
        elif isinstance(strengths_text, list):
            text_fields["strengths"] = strengths_text
    
    if "weaknesses" in review:
        weaknesses_text = review["weaknesses"]
        if isinstance(weaknesses_text, str):
            text_fields["weaknesses"] = [
                w.strip().lstrip("-*•").strip()
                for w in weaknesses_text.split("\n")
                if w.strip()
            ]
        elif isinstance(weaknesses_text, list):
            text_fields["weaknesses"] = weaknesses_text
    
    if "questions" in review:
        questions_text = review["questions"]
        if isinstance(questions_text, str):
            text_fields["questions"] = [
                q.strip()
                for q in questions_text.split("\n")
                if q.strip() and "?" in q
            ]
        elif isinstance(questions_text, list):
            text_fields["questions"] = questions_text
    
    return text_fields


def parse_iclr_json(json_path: Path) -> ICLRReview:
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    title = data.get("title", "Unknown Title")
    forum_id = data.get("forum", "")
    pdf_url = data.get("pdf_url", "")
    
    decision_data = data.get("decision", {})
    decision_str = decision_data.get("decision", "Accept (Poster)")
    decision = normalize_decision(decision_str)
    
    category = json_path.parent.parent.name
    if category not in ["MLLM", "RL", "3D"]:
        category = "Other"
    
    peer_reviews = data.get("peer_discussion", [])
    ratings = []
    all_strengths = []
    all_weaknesses = []
    all_questions = []
    summaries = []
    
    soundness_scores: List[float] = []
    presentation_scores: List[float] = []
    contribution_scores: List[float] = []
    confidence_scores: List[float] = []

    for item in peer_reviews:
        review_data = item.get("review", {})

        rating = review_data.get("rating")
        if rating:
            ratings.append(float(rating))

        if review_data.get("soundness"):
            soundness_scores.append(float(review_data["soundness"]))
        if review_data.get("presentation"):
            presentation_scores.append(float(review_data["presentation"]))
        if review_data.get("contribution"):
            contribution_scores.append(float(review_data["contribution"]))
        if review_data.get("confidence"):
            confidence_scores.append(float(review_data["confidence"]))

        text_fields = extract_text_from_review(review_data)
        summaries.append(text_fields["summary"])
        all_strengths.extend(text_fields["strengths"])
        all_weaknesses.extend(text_fields["weaknesses"])
        all_questions.extend(text_fields["questions"])

    avg_rating_raw = sum(ratings) / len(ratings) if ratings else 5.0

    def _avg(lst: List[float]) -> float:
        return sum(lst) / len(lst) if lst else 0.0

    avg_soundness = _avg(soundness_scores)
    avg_presentation = _avg(presentation_scores)
    avg_contribution = _avg(contribution_scores)
    avg_confidence = _avg(confidence_scores)

    # overall rating is 1-10 in ICLR; criteria are 1-4
    overall = max(1, min(10, round(avg_rating_raw)))

    quality = max(1, min(4, round(avg_soundness))) if avg_soundness else 2
    clarity = max(1, min(4, round(avg_presentation))) if avg_presentation else 2
    significance = max(1, min(4, round(avg_contribution))) if avg_contribution else 2
    originality = significance
    confidence = max(1, min(5, round(avg_confidence))) if avg_confidence else 4

    avg_rating_normalized = overall
    
    metareview_data = data.get("metareview", {})
    metareview = metareview_data.get("metareview", "")
    
    paper_id = forum_id if forum_id else json_path.stem
    
    pdf_path = json_path.with_suffix(".pdf")
    if not pdf_path.exists():
        logger.warning(f"PDF not found for {json_path}: {pdf_path}")
        pdf_path = ""
    
    review = ICLRReview(
        paper_id=paper_id,
        title=title,
        pdf_path=str(pdf_path),
        decision=decision,
        category=category,
        overall=overall,
        quality=quality,
        clarity=clarity,
        significance=significance,
        originality=originality,
        confidence=confidence,
        summary=summaries[0] if summaries else "",
        strengths=list(set(all_strengths))[:10],  # Deduplicate, limit to 10
        weaknesses=list(set(all_weaknesses))[:10],
        questions=list(set(all_questions))[:5],
        limitations=[],  # Not explicitly in ICLR format
        forum_id=forum_id,
        openreview_url=pdf_url,
        num_reviews=len(peer_reviews),
        avg_rating_raw=avg_rating_raw,
        avg_rating_normalized=avg_rating_normalized,
        raw_reviews=peer_reviews,
        metareview=metareview if metareview else None,
    )
    
    logger.debug(
        f"Parsed {paper_id}: decision={decision}, "
        f"avg_rating={avg_rating_raw:.1f}→{avg_rating_normalized}, "
        f"num_reviews={len(peer_reviews)}"
    )
    
    return review


def download_iclr_dataset(
    output_dir: Path,
) -> Path:

    dataset_url = "https://huggingface.co/datasets/simon123905/ICLR/resolve/main/Miniset-1K/Miniset-1K.zip"
    
    zip_path = output_dir / "Miniset-1K.zip"
    extracted_dir = output_dir / "Miniset-1K"
    
    if extracted_dir.exists():
        logger.info(f"Dataset already exists: {extracted_dir}")
        return extracted_dir
    
    output_dir.mkdir(parents=True, exist_ok=True)
    
    if not zip_path.exists():
        logger.info(f"Downloading ICLR dataset from {dataset_url}")
        print(" Downloading ICLR Miniset-1K dataset...")
        
        try:
            response = requests.get(dataset_url, stream=True)
            response.raise_for_status()
            
            total_size = int(response.headers.get("content-length", 0))
            block_size = 8192
            
            with open(zip_path, "wb") as f, tqdm(
                total=total_size, unit="B", unit_scale=True
            ) as pbar:
                for chunk in response.iter_content(block_size):
                    f.write(chunk)
                    pbar.update(len(chunk))
            
            print(f" Downloaded: {zip_path} ({zip_path.stat().st_size / 1024 / 1024:.1f} MB)")
            
        except Exception as e:
            logger.error(f"Download failed: {e}")
            print(f" Download failed: {e}")
            if zip_path.exists():
                zip_path.unlink()
            raise
    
    if not extracted_dir.exists():
        print(f" Extracting dataset...")
        try:
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(output_dir)
            print(f" Extracted to: {extracted_dir}")
        except Exception as e:
            logger.error(f"Extraction failed: {e}")
            print(f" Extraction failed: {e}")
            raise
    
    print(f"✅ Dataset ready: {extracted_dir}")
    return extracted_dir


def load_iclr_reviews(
    dataset_dir,
    limit: Optional[int] = None,
) -> List[ICLRReview]:

    if isinstance(dataset_dir, str):
        dataset_dir = Path(dataset_dir)
    elif not isinstance(dataset_dir, Path):
        raise TypeError(f"dataset_dir must be str or Path, got {type(dataset_dir)}")

    if not dataset_dir.exists():
        raise FileNotFoundError(
            f"Dataset directory not found: {dataset_dir}\n"
            f"Please download the dataset first:\n"
            f"  python iclr_dataset.py\n"
            f"Or check the path: {dataset_dir.absolute()}"
        )

    if not dataset_dir.is_dir():
        raise NotADirectoryError(
            f"Path is not a directory: {dataset_dir}\n"
            f"Expected a directory containing ICLR Miniset-1K data."
        )

    json_files = list(dataset_dir.rglob("*.json"))

    logger.info(f"Found {len(json_files)} JSON files in {dataset_dir}")
    
    reviews = []
    failed_files = []

    for i, json_file in enumerate(tqdm(json_files, desc="Loading reviews")):
        if limit and i >= limit:
            break
        
        try:
            review = parse_iclr_json(json_file)
            reviews.append(review)
        except Exception as e:
            failed_files.append((json_file, str(e)))
            logger.warning(f"Failed to parse {json_file}: {e}")

    success_rate = len(reviews) / len(json_files) * 100 if json_files else 0
    logger.info(
        f"Loaded {len(reviews)} reviews from {len(json_files)} files "
        f"({success_rate:.1f}% success rate)"
    )

    if failed_files:
        logger.warning(f"Failed to parse {len(failed_files)} files:")
        for json_file, error in failed_files[:5]:  # Show first 5 failures
            logger.warning(f"  - {json_file.name}: {error}")

    if not reviews:
        raise ValueError(
            f"Successfully loaded 0 reviews from {len(json_files)} JSON files.\n"
            f"Check the error messages above for details."
        )

    return reviews

def save_iclr_as_golden(
    review: ICLRReview,
    output_dir: Path,
) -> None:
    """
    Save ICLR review in golden format for benchmark comparison.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    golden_data = {
        "paper_id": review.paper_id,
        "title": review.title,
        "decision": review.decision,
        "category": review.category,
        "scores": {
            "overall": review.overall,
            "quality": review.quality,
            "clarity": review.clarity,
            "significance": review.significance,
            "originality": review.originality,
            "confidence": review.confidence,
        },
        "text": {
            "summary": review.summary,
            "strengths": review.strengths,
            "weaknesses": review.weaknesses,
            "questions": review.questions,
            "limitations": review.limitations,
        },
        "metadata": {
            "forum_id": review.forum_id,
            "openreview_url": review.openreview_url,
            "num_reviews": review.num_reviews,
            "avg_rating_raw": review.avg_rating_raw,
            "avg_rating_normalized": review.avg_rating_normalized,
        },
    }
    
    output_path = output_dir / f"{review.paper_id}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(golden_data, f, indent=2, ensure_ascii=False)
    
    logger.info(f"Saved golden review: {output_path}")
    
    if review.pdf_path and Path(review.pdf_path).exists():
        import shutil
        pdf_dest = output_dir / f"{review.paper_id}.pdf"
        shutil.copy2(review.pdf_path, pdf_dest)
        logger.info(f"Copied PDF: {pdf_dest}")


def create_benchmark_golden_set(
    dataset_dir: Path,
    output_dir: Path,
    sample_size: int = 10,
    balance_decisions: bool = True,
) -> List[ICLRReview]:
    """
    Create a balanced golden set for benchmark evaluation.
    """
    all_reviews = load_iclr_reviews(dataset_dir)
    
    if not all_reviews:
        raise ValueError("No reviews found in dataset")
    
    if balance_decisions:
        from collections import defaultdict
        
        by_decision = defaultdict(list)
        for review in all_reviews:
            by_decision[review.decision].append(review)
        
        selected = []
        per_category = sample_size // len(by_decision)
        
        for decision, reviews in by_decision.items():
            import random
            random.seed(42)
            sampled = random.sample(reviews, min(per_category, len(reviews)))
            selected.extend(sampled)  # Исправлена опечатка: samplemed → sampled
            logger.info(f"Selected {len(sampled)} {decision} papers")
        
        if len(selected) < sample_size:
            remaining = sample_size - len(selected)
            available = [r for r in all_reviews if r not in selected]
            if available:
                import random
                selected.extend(random.sample(available, min(remaining, len(available))))
    else:
        import random
        random.seed(42)
        selected = random.sample(all_reviews, min(sample_size, len(all_reviews)))
    
    output_dir.mkdir(parents=True, exist_ok=True)
    for review in selected:
        save_iclr_as_golden(review, output_dir)
    
    index_data = {
        "total_papers": len(selected),
        "by_decision": {},
        "by_category": {},
        "papers": [
            {
                "paper_id": r.paper_id,
                "title": r.title,
                "decision": r.decision,
                "category": r.category,
                "overall_score": r.overall,
            }
            for r in selected
        ],
    }
    
    from collections import Counter
    decision_counts = Counter(r.decision for r in selected)
    category_counts = Counter(r.category for r in selected)
    index_data["by_decision"] = dict(decision_counts)
    index_data["by_category"] = dict(category_counts)
    
    index_path = output_dir / "golden_index.json"
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index_data, f, indent=2, ensure_ascii=False)
    
    logger.info(f"Created golden set with {len(selected)} papers: {output_dir}")
    print(f"✅ Created golden set: {len(selected)} papers in {output_dir}")
    
    return selected


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="ICLR Dataset Loader",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).parent / "data",
        help="Directory to store dataset (default: ./data)",
    )
    parser.add_argument(
        "--create-golden-set",
        action="store_true",
        help="Create balanced golden set for benchmark",
    )
    parser.add_argument(
        "--golden-sample-size",
        type=int,
        default=10,

        help="Number of papers in golden set (default: 10)",
    )

    args = parser.parse_args()
    
    print(f" Downloading ICLR dataset to {args.output_dir}...")
    dataset_dir = download_iclr_dataset(
        args.output_dir,
    )
    
    if args.create_golden_set:
        golden_dir = args.output_dir / "golden_set"

        print(f"\n📋 Creating golden set with {args.golden_sample_size} papers...")
        selected = create_benchmark_golden_set(
            dataset_dir,
            golden_dir,
            sample_size=args.golden_sample_size,
            balance_decisions=True,
        )
        print(f"✅ Golden set created: {golden_dir}")
    else:
        print(f"\n📊 Loading reviews from {dataset_dir}...")
        reviews = load_iclr_reviews(dataset_dir, limit=100)
        
        from collections import Counter
        decision_counts = Counter(r.decision for r in reviews)
        category_counts = Counter(r.category for r in reviews)
        
        print(f"\n📊 Dataset Statistics (first {len(reviews)} papers):")
        print(f"  By decision: {dict(decision_counts)}")
        print(f"  By category: {dict(category_counts)}")
        print(f"  Avg rating (raw): {sum(r.avg_rating_raw for r in reviews) / len(reviews):.2f}")
        print(f"  Avg rating (normalized): {sum(r.avg_rating_normalized for r in reviews) / len(reviews):.2f}")


        print(f"\n✅ Dataset loaded successfully!")
        print(f"   Location: {dataset_dir.absolute()}")

