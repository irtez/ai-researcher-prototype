import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "benchmark"))
sys.path.insert(0, str(PROJECT_ROOT / "academia_mcp"))
sys.path.insert(0, str(PROJECT_ROOT / "reviewer_patches"))

from writer_specific_judge import run_grouped_rubric_review
from writer_specific_judge.adapters.reviewer import parse_pdf_text_with_academia


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return int(value)


load_env_file(PROJECT_ROOT / ".env")
load_env_file(PROJECT_ROOT.parent / ".env")


async def main():
    parser = argparse.ArgumentParser(description="Run Grouped Rubric LLM Judge on a PDF paper.")
    parser.add_argument("pdf_path", type=str, help="Path to the scientific paper PDF file")
    parser.add_argument(
        "--paper-type",
        type=str,
        default=None,
        help="Explicit paper type (e.g., 'llm_system', 'empirical')",
    )
    parser.add_argument(
        "--model-name",
        type=str,
        default=os.getenv("REVIEW_MODEL_NAME") or os.getenv("MODEL_NAME"),
        help="OpenRouter model name. Defaults to REVIEW_MODEL_NAME or MODEL_NAME from .env.",
    )
    parser.add_argument(
        "--repeat-count",
        type=int,
        default=1,
        help="Number of repeat calls per group",
    )
    parser.add_argument(
        "--shuffle",
        dest="shuffle_criteria",
        action="store_true",
        default=True,
        help="Shuffle criteria inside prompts to reduce positional bias",
    )
    parser.add_argument(
        "--shuffle-seed",
        type=int,
        default=42,
        help="Seed for criteria shuffling",
    )
    parser.add_argument(
        "--auto-related-work",
        action="store_true",
        help="Enable automatic related work search on arXiv",
    )
    parser.add_argument(
        "--use-llm-keywords",
        action="store_true",
        help="Use LLM to extract keywords for arXiv search",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="review_result.json",
        help="Path to save the JSON output",
    )

    args = parser.parse_args()

    if not os.path.exists(args.pdf_path):
        print(f"Error: PDF file not found at {args.pdf_path}")
        sys.exit(1)

    print(f"1. Extracting text from PDF: {args.pdf_path}...")
    try:
        paper_text = parse_pdf_text_with_academia(args.pdf_path)
        print(f"   Extracted {len(paper_text)} characters.")
    except Exception as e:
        print(f"Error extracting PDF: {e}")
        sys.exit(1)

    print("\n2. Running Grouped Rubric Review...")
    print(
        f"   Settings: model_name={args.model_name}, repeat_count={args.repeat_count}, "
        f"shuffle_criteria={args.shuffle_criteria}, shuffle_seed={args.shuffle_seed}, "
        f"auto_related_work={args.auto_related_work}, use_llm_keywords={args.use_llm_keywords}"
    )

    try:
        review_response = await run_grouped_rubric_review(
            paper_text=paper_text,
            paper_type=args.paper_type,
            auto_related_work=args.auto_related_work,
            use_llm_keywords=args.use_llm_keywords,
            model_name=args.model_name,
            shuffle_criteria=args.shuffle_criteria,
            shuffle_seed=args.shuffle_seed,
            repeat_count=args.repeat_count,
        )

        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(review_response.model_dump(), f, indent=2, ensure_ascii=False)

        print(f"\n[SUCCESS] Review completed! Saved to {args.output}")

        print("\n=== Programmatic Scores (0.0 to 1.0 scale) ===")
        for module_id, score in review_response.module_scores.items():
            score_text = "N/A" if score is None else f"{score:.2f}"
            print(f" - {module_id:<40}: {score_text}")

        print("\n=== Main Problem ===")
        if review_response.main_problem:
            print(f" {review_response.main_problem}")
        else:
            print(" No main problem reported.")

        print("\n=== Top Weaknesses ===")
        if review_response.top_weaknesses:
            for weakness in review_response.top_weaknesses:
                print(f" - {weakness}")
        else:
            print(" No top weaknesses reported.")

    except Exception as e:
        print(f"Error running review: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
