#!/usr/bin/env python3
import asyncio
import sys
import inspect
import traceback
from pathlib import Path
from dotenv import load_dotenv


def check_file_structure():
    """Verify all required files exist."""
    print("\n=== Checking file structure ===\n")

    required_files = [
        "__init__.py",
        "review.py",
        "batch_review.py",
        "faithfulness.py",
        "context_control.py",
        "eval_pipeline.py",
        "prompts.py",
        "models.py",
        "pdf_processor.py",
        "test_reviewer.ipynb",
        "REVIEWER_ARCHITECTURE.md",
        "IMPLEMENTATIONS.md",
    ]

    base_path = Path(__file__).parent
    all_exist = True

    for file in required_files:
        file_path = base_path / file
        if file_path.exists():
            print(f" {file}")
        else:
            print(f" {file} - missing!")
            all_exist = False

    return all_exist


def check_imports():
    """Verify all modules can be imported."""
    print("\n=== Checking imports ===\n")

    imports_to_test = [
        ("review", "review_pdf_paper"),
        ("batch_review", "review_with_repeats"),
        ("faithfulness", "check_faithfulness"),
        ("eval_pipeline", "evaluate_reviews"),
        ("context_control", "prepare_paper_for_review_async"),
        ("review", "ReviewMetrics"),
        ("batch_review", "ConsistencyResult"),
        ("faithfulness", "FaithfulnessCheck"),
        ("eval_pipeline", "EvalMetrics"),
    ]
    all_imported = True

    for module_name, function_name in imports_to_test:
        try:
            module = __import__(module_name, fromlist=[function_name])
            func = getattr(module, function_name)
            print(f" - {module_name}.{function_name}")
        except ImportError as e:
            print(f"ImportError {module_name}.{function_name} - {e}")
            all_imported = False
        except Exception as e:
            print(f"Error: {module_name}.{function_name} - {e}")
            all_imported = False

    return all_imported


async def check_reviewer_tool():
    """Verify the reviewer tool is registered and functional."""
    print("\n=== Checking reviewer tool ===\n")

    try:
        from review import review_pdf_paper

        print(" review_pdf_paper tool is available")

        sig = inspect.signature(review_pdf_paper)
        params = list(sig.parameters.keys())

        expected_params = [
            "pdf_filename",
            "eval_mode",
            "use_images",
            "deterministic",
            "seed",
            "use_cache",
            "use_sections",
            "use_a3pr",
            "use_self_critique",
            "use_arxiv_search",
        ]
        has_all_params = all(p in params for p in expected_params)

        if has_all_params:
            print(f"Function signature includes all parameters: {expected_params}")
        else:
            missing = [p for p in expected_params if p not in params]
            print(f"Some params are missing: {missing}")

        return True

    except ImportError as e:
        print(f"ImportError: review_pdf_paper not available: {e}")
        return False
    except Exception as e:
        print(f"Error checking reviewer tool: {e}")
        return False


def check_docker_compose():
    """Verify docker-compose.yml has all volume mounts."""
    print("\n=== Checking Docker Compose Configuration ===\n")

    all_mounted = True
    compose_file = Path(__file__).parent.parent / "docker-compose.yml"

    with open(compose_file, "r") as f:
        content = f.read()

    required_mounts = [
        "reviewer_patches/__init__.py",
        "reviewer_patches/review.py",
        "reviewer_patches/batch_review.py",
        "reviewer_patches/faithfulness.py",
        "reviewer_patches/context_control.py",
        "reviewer_patches/eval_pipeline.py",
    ]

    for mount in required_mounts:
        if mount in content:
            print(f" -  Volume mount: {mount}")
        else:
            print(f" Some volume mount're missing: {mount}")
            all_mounted = False

    return all_mounted


async def run_review_test():
    """Run a quick review test with a real paper."""
    print("\n=== Running Review Test ===\n")

    try:
        from review import review_pdf_paper_by_url

        load_dotenv(".env")
        test_url = " https://arxiv.org/pdf/1706.03762.pdf"

        print(f"Testing with Attention is All You Need paper: {test_url}")
        print("This may take 60-70 seconds...")

        review = await review_pdf_paper_by_url(
            pdf_url=test_url,
            eval_mode=True,
            deterministic=True,
            use_cache=False,
        )
        
        print("Review was completed successfully!")
        print(f"Overall score: {review.overall_score_raw}/6")
        print(f"Confidence: {review.confidence.score}/5")
        print(f"Originality: {review.originality.score}/4")
        print(f"Importance: {review.importance.score}/4")
        print(f"Experimental Soundness: {review.experimental_soundness.score}/4")
        print(f"Clarity: {review.clarity.score}/4")
        return True

    except Exception as e:
        print(f"Review test failed: {e}")
        traceback.print_exc()
        return False


async def main():
    """Run all verification checks."""
    print("\n BEGINNING VERIFICATION...\n")

    results = {
        "File structure": check_file_structure(),
        "Imports": check_imports(),
        "Docker config": check_docker_compose(),
        "Reviewer tool": await check_reviewer_tool(),
        "Review test": await run_review_test(),
    }
    print("=== Verification summary ===")

    passed = sum(1 for v in results.values() if v is True)
    total = len([v for v in results.values() if v is not None])

    for check, result in results.items():
        if result is True:
            print(f"Was completed: {check}")
        else:
            print(f"Was failed: {check}")

    print(f"\nResult: {passed}/{total} checks passed")

    if passed == total:
        print("\n🎉 All checks passed! Reviewer is ready to use)")
        return 0
    else:
        print("\n⚠️  Some checks failed. Please review the errors above.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
