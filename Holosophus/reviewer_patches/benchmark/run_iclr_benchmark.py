#!/usr/bin/env python3

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent))

from iclr_dataset import (
    create_benchmark_golden_set,
    download_iclr_dataset,
    load_iclr_reviews,
)
from iclr_adapter import (
    compare_reviews,
    load_golden_review,
)

try:
    from batch_review import review_dataset
    from eval_pipeline import save_eval_metrics, EvalMetrics
    IMPORT_SUCCESS = True
except ImportError as e:
    print(f"⚠️  Import warning: {e}")
    print("Make sure academia container is running and reviewer_patches is mounted")
    IMPORT_SUCCESS = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def setup_benchmark_run(output_dir: Path) -> Path:
    """Create timestamped benchmark run directory."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = output_dir / f"iclr_run_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    
    (run_dir / "logs").mkdir(exist_ok=True)
    (run_dir / "reviews").mkdir(exist_ok=True)
    (run_dir / "eval").mkdir(exist_ok=True)
    (run_dir / "golden").mkdir(exist_ok=True)
    
    return run_dir

def calculate_spearman_correlation(predictions: List[float], ground_truth: List[float]):
    """
    Calculate Spearman rank correlation coefficient.
    """
    try:
        from scipy.stats import spearmanr
        rho, p_value = spearmanr(predictions, ground_truth)
        return rho, p_value
    except ImportError:
        logger.warning("scipy not available, using fallback correlation")
        if len(predictions) < 2:
            return 0.0, 1.0
        n = len(predictions)
        mean_pred = sum(predictions) / n
        mean_gold = sum(ground_truth) / n
        
        numerator = sum((p - mean_pred) * (g - mean_gold) for p, g in zip(predictions, ground_truth))
        denom_pred = sum((p - mean_pred) ** 2 for p in predictions) ** 0.5
        denom_gold = sum((g - mean_gold) ** 2 for g in ground_truth) ** 0.5
        
        if denom_pred * denom_gold == 0:
            return 0.0, 1.0
        
        rho = numerator / (denom_pred * denom_gold)
        return rho, 0.0


def calculate_acc_top2(predictions: List[str], ground_truth: List[str]) -> float:
    """
    Calculate accuracy of predicting correct decision within top-2 categories.
    """
    decision_order = {'oral': 0, 'spotlight': 1, 'poster': 2, 'reject': 3}
    
    correct = 0
    for pred, gold in zip(predictions, ground_truth):
        pred_rank = decision_order.get(pred, 2)
        gold_rank = decision_order.get(gold, 2)
        
        if abs(pred_rank - gold_rank) <= 1:
            correct += 1
    
    return correct / len(predictions) if predictions else 0.0


def calculate_per_criterion_metrics(comparison_results: List[Dict]) -> Dict[str, Dict]:
    """
    Calculate MAE and Spearman correlation for each criterion separately.
    """
    criteria = [
        'experimental_soundness', # maps to soundness in paper
        'clarity', # maps to presentation in paper
        'originality',
        'importance',
        'claim_support',
        'community_value',
        'contextualization',
    ]
    
    metrics_by_criterion = {}
    
    for criterion in criteria:
        preds = []
        golds = []
        
        for comp in comparison_results:
            if 'criteria_comparison' in comp and criterion in comp['criteria_comparison']:
                preds.append(comp['criteria_comparison'][criterion]['predicted'])
                golds.append(comp['criteria_comparison'][criterion]['golden'])
        
        if len(preds) == 0:
            logger.warning(f"No predictions for criterion: {criterion}")
            continue
        
        mae = sum(abs(p - g) for p, g in zip(preds, golds)) / len(preds)
        
        rho, p_value = calculate_spearman_correlation(preds, golds)
        
        metrics_by_criterion[criterion] = {
            'mae': round(mae, 3),
            'spearman_rho': round(rho, 3),
            'p_value': round(p_value, 4) if p_value else None,
            'n': len(preds),
        }
    
    return metrics_by_criterion


def analyze_domain_metrics(
    comparison_results: List[Dict],
    paper_metadata: List[Dict],
) -> Dict[str, Dict]:
    """
    Calculate metrics broken down by domain (CV/MLLM/RL).
    """
    from collections import defaultdict
    
    by_domain = defaultdict(list)
    
    for comp, meta in zip(comparison_results, paper_metadata):
        domain = meta.get('category', 'Unknown')
        comp['domain'] = domain
        comp['golden_decision'] = meta.get('decision', 'poster')
        by_domain[domain].append(comp)
    
    domain_metrics = {}
    
    for domain, comps in by_domain.items():
        mae_overall = sum(c['overall_diff'] for c in comps) / len(comps)
        
        preds = [c.get('predicted_overall', 3) for c in comps]
        golds = [c.get('golden_overall', 3) for c in comps]
        rho, p_value = calculate_spearman_correlation(preds, golds)
        
        pred_decisions = [c.get('decision', 'poster') for c in comps]
        gold_decisions = [c.get('golden_decision', 'poster') for c in comps]
        acc_top2 = calculate_acc_top2(pred_decisions, gold_decisions)
        
        domain_metrics[domain] = {
            'mae_overall': round(mae_overall, 3),
            'spearman_rho': round(rho, 3),
            'p_value': round(p_value, 4) if p_value else None,
            'acc_top2': round(acc_top2, 3),
            'n': len(comps),
        }
    
    return domain_metrics


def analyze_decision_bias(
    comparison_results: List[Dict],
    paper_metadata: List[Dict],
) -> Dict[str, Dict]:
    """
    Analyze systematic bias in predictions for different decision categories.
    """
    from collections import defaultdict
    import numpy as np
    
    try:
        from scipy import stats
        has_scipy = True
    except ImportError:
        has_scipy = False
        logger.warning("scipy not available, skipping t-test in bias analysis")
    
    by_decision = defaultdict(lambda: {'predicted': [], 'golden': []})
    
    for comp, meta in zip(comparison_results, paper_metadata):
        decision = meta.get('decision', 'unknown')
        
        pred_score = comp.get('predicted_overall', 3)
        gold_score = meta.get('golden_overall', 3)
        
        by_decision[decision]['predicted'].append(pred_score)
        by_decision[decision]['golden'].append(gold_score)
    
    bias_analysis = {}
    
    for decision, scores in by_decision.items():
        pred_scores = scores['predicted']
        gold_scores = scores['golden']
        
        if len(pred_scores) == 0:
            continue
        
        pred_mean = float(np.mean(pred_scores))
        gold_mean = float(np.mean(gold_scores))
        bias = pred_mean - gold_mean
        
        if has_scipy and len(pred_scores) > 1:
            t_stat, p_value = stats.ttest_ind(pred_scores, gold_scores)
            t_stat = float(t_stat)
            p_value = float(p_value)
        else:
            t_stat = None
            p_value = None
        
        bias_analysis[decision] = {
            'n': len(pred_scores),
            'avg_predicted': round(pred_mean, 3),
            'avg_golden': round(gold_mean, 3),
            'bias': round(bias, 3),
            't_statistic': t_stat,
            'p_value': round(p_value, 4) if p_value else None,
        }
    
    return bias_analysis


def compare_experiment_runs(
    results_single: Dict[str, Any],
    results_a3pr: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Compare two experiment runs (single-pass vs A3PR pipeline).
    """
    comparison = {}
    
    criteria_single = results_single.get('by_criterion', {})
    criteria_a3pr = results_a3pr.get('by_criterion', {})
    
    all_criteria = set(criteria_single.keys()) | set(criteria_a3pr.keys())
    
    for criterion in all_criteria:
        m_single = criteria_single.get(criterion, {})
        m_a3pr = criteria_a3pr.get(criterion, {})
        
        comparison[criterion] = {
            'mae_single': m_single.get('mae', 0.0),
            'mae_a3pr': m_a3pr.get('mae', 0.0),
            'spearman_single': m_single.get('spearman_rho', 0.0),
            'spearman_a3pr': m_a3pr.get('spearman_rho', 0.0),
            'mae_improvement': m_single.get('mae', 0.0) - m_a3pr.get('mae', 0.0),
            'spearman_improvement': m_a3pr.get('spearman_rho', 0.0) - m_single.get('spearman_rho', 0.0),
        }
    
    comparison['overall'] = {
        'avg_mae_single': results_single.get('summary', {}).get('avg_mae', 0.0),
        'avg_mae_a3pr': results_a3pr.get('summary', {}).get('avg_mae', 0.0),
        'exact_match_single': results_single.get('summary', {}).get('exact_match_rate', 0.0),
        'exact_match_a3pr': results_a3pr.get('summary', {}).get('exact_match_rate', 0.0),
    }
    
    if 'domain_analysis' in results_single and 'domain_analysis' in results_a3pr:
        acc_single = []
        acc_a3pr = []
        
        for domain_data in results_single['domain_analysis'].values():
            if 'acc_top2' in domain_data:
                acc_single.append(domain_data['acc_top2'])
        
        for domain_data in results_a3pr['domain_analysis'].values():
            if 'acc_top2' in domain_data:
                acc_a3pr.append(domain_data['acc_top2'])
        
        if acc_single and acc_a3pr:
            comparison['acc_top2'] = {
                'single': sum(acc_single) / len(acc_single),
                'a3pr': sum(acc_a3pr) / len(acc_a3pr),
                'improvement': sum(acc_a3pr) / len(acc_a3pr) - sum(acc_single) / len(acc_single),
            }
    
    return comparison


async def run_iclr_benchmark(
    dataset_dir: Path,
    output_dir: Path,
    sample_size: int = 1,
    balance_decisions: bool = True,
    use_images: bool = False,  # Disable by default for speed
    deterministic: bool = True,
    use_sections: bool = True,
    use_a3pr: bool = False,
    use_self_critique: bool = False,
    use_arxiv_search: bool = False,
    skip_golden_creation: bool = False,
    run_experiments: bool = False,
) -> Dict[str, Any]:
    """
    Run end-to-end ICLR benchmark.
    """
    if not IMPORT_SUCCESS:
        logger.error("Cannot run benchmark: imports failed")
        return {"error": "Imports failed"}
    
    run_dir = setup_benchmark_run(output_dir)
    logger.info(f"Benchmark run directory: {run_dir}")
    
    golden_dir = run_dir / "golden"
    
    if not skip_golden_creation:
        logger.info(f"Creating balanced golden set with {sample_size} papers...")
        golden_reviews = create_benchmark_golden_set(
            dataset_dir,
            golden_dir,
            sample_size=sample_size,
            balance_decisions=balance_decisions,
        )
        logger.info(f"Created golden set: {len(golden_reviews)} papers")
    else:
        golden_reviews = []
        golden_index_path = golden_dir / "golden_index.json"
        if golden_index_path.exists():
            with open(golden_index_path, "r") as f:
                index_data = json.load(f)
            logger.info(f"Loaded golden index: {index_data['total_papers']} papers")
        else:
            logger.warning(f"Golden index not found: {golden_index_path}")
    
    pdf_paths = []
    paper_metadata = []
    
    for json_file in golden_dir.glob("*.json"):
        if json_file.name == "golden_index.json":
            continue
        
        with open(json_file, "r") as f:
            data = json.load(f)
        
        pdf_path = json_file.with_suffix(".pdf")
        if pdf_path.exists():
            pdf_paths.append(str(pdf_path))
            paper_metadata.append({
                "paper_id": data.get("paper_id", json_file.stem),
                "title": data.get("title", ""),
                "decision": data.get("decision", "unknown"),
                "category": data.get("category", "unknown"),
                "golden_overall": data.get("scores", {}).get("overall", 3),
            })
        else:
            logger.warning(f"PDF not found: {pdf_path}")
    
    if not pdf_paths:
        logger.error("No PDF files found in golden set")
        return {"error": "No PDFs found"}
    
    logger.info(f"Found {len(pdf_paths)} PDFs in golden set")
    
    reviews_file = run_dir / "reviews" / "predicted_reviews.jsonl"
    metrics_file = run_dir / "eval" / "batch_metrics.json"
    
    logger.info("Running reviewer on golden set papers...")
    start_time = time.time()
    
    predicted_reviews = await review_dataset(
        pdf_paths=pdf_paths,
        output_jsonl=str(reviews_file),
        metrics_file=str(metrics_file),
        use_images=use_images,
        deterministic=deterministic,
        use_cache=True,
        use_sections=use_sections,
        use_a3pr=use_a3pr,
        use_self_critique=use_self_critique,
        use_arxiv_search=use_arxiv_search,
    )
    
    elapsed = time.time() - start_time
    logger.info(f"Batch review completed in {elapsed:.1f}s")
    
    logger.info("Comparing predicted reviews with golden reviews...")
    
    comparison_results = []
    mae_scores = []
    exact_matches = 0
    
    for i, (pdf_path, predicted) in enumerate(zip(pdf_paths, predicted_reviews)):
        if not predicted:
            logger.warning(f"Skipping {pdf_path}: no predicted review")
            continue
        
        golden_json = Path(pdf_path).with_suffix(".json")
        if not golden_json.exists():
            logger.warning(f"Golden JSON not found: {golden_json}")
            continue
        
        try:
            golden = load_golden_review(golden_json)
        except Exception as e:
            logger.error(f"Failed to load golden review from {golden_json}: {e}")
            continue
        
        comparison = compare_reviews(predicted, golden)
        comparison["paper_id"] = paper_metadata[i]["paper_id"]
        comparison["title"] = paper_metadata[i]["title"]
        comparison["decision"] = paper_metadata[i]["decision"]
        comparison["golden_overall"] = paper_metadata[i]["golden_overall"]
        comparison["predicted_overall"] = predicted.overall.score
        
        comparison_results.append(comparison)
        mae_scores.append(comparison["mae"])
        
        if comparison["exact_match"]:
            exact_matches += 1
        
        logger.debug(
            f"Paper {i+1}/{len(pdf_paths)}: "
            f"MAE={comparison['mae']:.2f}, "
            f"overall: {comparison['predicted_overall']} vs {comparison['golden_overall']}"
        )
    
    avg_mae = sum(mae_scores) / len(mae_scores) if mae_scores else 0.0
    exact_match_rate = exact_matches / len(comparison_results) if comparison_results else 0.0
    
    by_decision = {}
    for comp in comparison_results:
        decision = comp["decision"]
        if decision not in by_decision:
            by_decision[decision] = {"count": 0, "mae_sum": 0.0}
        by_decision[decision]["count"] += 1
        by_decision[decision]["mae_sum"] += comp["mae"]
    
    for decision in by_decision:
        by_decision[decision]["avg_mae"] = (
            by_decision[decision]["mae_sum"] / by_decision[decision]["count"]
            if by_decision[decision]["count"] > 0 else 0.0
        )

    
    extended_metrics = {}
    
    if run_experiments:
        logger.info("Calculating extended metrics for experiments...")
        
        logger.info("  - Calculating per-criterion metrics...")
        extended_metrics["by_criterion"] = calculate_per_criterion_metrics(comparison_results)
        
        logger.info("  - Analyzing domain-specific metrics...")
        extended_metrics["domain_analysis"] = analyze_domain_metrics(
            comparison_results, paper_metadata
        )
        
        logger.info("  - Analyzing decision bias...")
        extended_metrics["decision_bias"] = analyze_decision_bias(
            comparison_results, paper_metadata
        )
        
        logger.info("Extended metrics calculation complete.")
    
    benchmark_results = {
        "timestamp": datetime.now().isoformat(),
        "config": {
            "sample_size": sample_size,
            "balance_decisions": balance_decisions,
            "use_images": use_images,
            "deterministic": deterministic,
            "use_sections": use_sections,
            "use_a3pr": use_a3pr,
            "use_self_critique": use_self_critique,
            "use_arxiv_search": use_arxiv_search,
            "run_experiments": run_experiments,
        },
        "summary": {
            "num_papers": len(pdf_paths),
            "num_comparisons": len(comparison_results),
            "avg_mae": avg_mae,
            "exact_match_rate": exact_match_rate,
            "exact_matches": exact_matches,
            "total_time_seconds": elapsed,
            "avg_time_per_paper": elapsed / len(pdf_paths) if pdf_paths else 0.0,
        },
        "by_decision": by_decision,
        "per_paper_results": comparison_results,
        "paper_metadata": paper_metadata,
    }
    
    if extended_metrics:
        benchmark_results["extended_metrics"] = extended_metrics
    
    results_file = run_dir / "eval" / "benchmark_results.json"
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(benchmark_results, f, indent=2, ensure_ascii=False)
    
    quick_stats = {
        "avg_mae": round(avg_mae, 3),
        "exact_match_rate": round(exact_match_rate, 3),
        "total_time_seconds": round(elapsed, 1),
        "papers_evaluated": len(comparison_results),
    }
    
    if extended_metrics:
        quick_stats["has_extended_metrics"] = True
    
    stats_file = run_dir / "logs" / "quick_stats.json"
    with open(stats_file, "w") as f:
        json.dump(quick_stats, f, indent=2)
    
    print("\n" + "=" * 60)
    print("ICLR BENCHMARK SUMMARY")
    print("=" * 60)
    print(f"Papers evaluated: {len(comparison_results)}/{len(pdf_paths)}")
    print(f"Average MAE: {avg_mae:.3f}")
    print(f"Exact match rate: {exact_match_rate:.1%}")
    print(f"Total time: {elapsed:.1f}s ({elapsed / len(pdf_paths):.1f}s per paper)")
    
    if by_decision:
        print("\nBy decision:")
        for decision, stats in sorted(by_decision.items()):
            print(f"  {decision:12}: {stats['count']:2d} papers, MAE={stats['avg_mae']:.3f}")
    
    if run_experiments and extended_metrics:
        print("\n" + "-" * 60)
        print("EXTENDED METRICS (EXPERIMENTS)")
        print("-" * 60)
        
        if "by_criterion" in extended_metrics:
            print("\nPer-criterion metrics:")
            for crit, metrics in extended_metrics["by_criterion"].items():
                print(f"  {crit:25}: MAE={metrics['mae']:.3f}, ρ={metrics['spearman_rho']:.3f}")
        
        if "domain_analysis" in extended_metrics:
            print("\nDomain analysis:")
            for domain, metrics in extended_metrics["domain_analysis"].items():
                print(f"  {domain:6}: MAE={metrics['mae_overall']:.3f}, "
                      f"ρ={metrics['spearman_rho']:.3f}, "
                      f"Acc@Top2={metrics['acc_top2']:.3f} (n={metrics['n']})")
        
        if "decision_bias" in extended_metrics:
            print("\nDecision bias analysis:")
            for decision, metrics in extended_metrics["decision_bias"].items():
                bias_sign = "+" if metrics['bias'] > 0 else ""
                print(f"  {decision:12}: predicted={metrics['avg_predicted']:.2f}, "
                      f"golden={metrics['avg_golden']:.2f}, "
                      f"bias={bias_sign}{metrics['bias']:.2f}")
    
    print(f"\n📁 Results saved to: {run_dir}")
    print(f"📊 Detailed results: {results_file}")
    print(f"📝 Quick stats: {stats_file}")
    print("=" * 60)
    
    return benchmark_results


def main():
    parser = argparse.ArgumentParser(
        description="Run ICLR benchmark end-to-end",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_iclr_benchmark.py --sample-size 5 --quick
  
  # Full benchmark with Tier 1 + Tier 2 features
  python run_iclr_benchmark.py --sample-size 20 --use-a3pr --use-self-critique
  
  # Run with extended metrics for experiments
  python run_iclr_benchmark.py --sample-size 100 --run-experiments
  
  # A3PR ablation study (run twice and compare)
  python run_iclr_benchmark.py --sample-size 50 --use-a3pr --run-experiments --output-dir output_a3pr
  python run_iclr_benchmark.py --sample-size 50 --run-experiments --output-dir output_single
  
  # Domain analysis
  python run_iclr_benchmark.py --sample-size 100 --run-experiments --balance-decisions
        """
    )
    
    parser.add_argument(
        "--dataset-dir",
        type=Path,
        default=Path(__file__).parent / "data",
        help="Path to ICLR Miniset-1K dataset",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).parent / "output",
        help="Directory for benchmark outputs",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=1,
        help="Number of papers to evaluate (default: 10)",
    )
    parser.add_argument(
        "--balance-decisions",
        action="store_true",
        default=True,
        help="Balance golden set across decision categories (default: True)",
    )
    parser.add_argument(
        "--no-balance-decisions",
        action="store_false",
        dest="balance_decisions",
        help="Don't balance decisions",
    )
    parser.add_argument(
        "--use-images",
        action="store_true",
        help="Include page images in reviews (slower)",
    )
    parser.add_argument(
        "--stochastic",
        action="store_true",
        help="Use stochastic mode (temperature=0.1)",
    )
    parser.add_argument(
        "--use-sections",
        action="store_true",
        default=True,
        help="Parse papers into sections (Tier 1, default: True)",
    )
    parser.add_argument(
        "--no-sections",
        action="store_false",
        dest="use_sections",
        help="Don't parse sections",
    )
    parser.add_argument(
        "--use-a3pr",
        action="store_true",
        help="Use A3PR 4-stage pipeline (Tier 2)",
    )
    parser.add_argument(
        "--use-self-critique",
        action="store_true",
        help="Run self-critique loop (Tier 2)",
    )
    parser.add_argument(
        "--use-arxiv-search",
        action="store_true",
        help="Search arXiv for related work (Tier 1)",
    )
    parser.add_argument(
        "--skip-golden-creation",
        action="store_true",
        help="Skip golden set creation (use existing)",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Quick mode: sample-size=5, no images, no arXiv search",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Re-download dataset",
    )
    parser.add_argument(
        "--run-experiments",
        action="store_true",
        help="Calculate extended metrics: per-criterion, domain analysis, decision bias",
    )
    
    args = parser.parse_args()
    
    if args.quick:
        args.sample_size = 5
        args.use_images = False
        args.use_arxiv_search = False
        print("🚀 Quick mode: 5 papers, no images, no arXiv search")
    
    if not args.dataset_dir.exists():
        print(f"❌ Dataset directory not found: {args.dataset_dir}")
        
        print("📥 Downloading ICLR dataset...")
        try:
            download_iclr_dataset(
                args.dataset_dir,
            )
            print(f"✅ Dataset downloaded to: {args.dataset_dir}")
        except Exception as e:
            print(f"❌ Download failed: {e}")
            return 1
    
    print("   Starting ICLR benchmark...")
    print(f"   Dataset: {args.dataset_dir}")
    print(f"   Sample size: {args.sample_size}")
    print(f"   Features: sections={args.use_sections}, a3pr={args.use_a3pr}, self_critique={args.use_self_critique}, arxiv={args.use_arxiv_search}")
    print(f"   Extended metrics: {args.run_experiments}")
    
    run_dir = asyncio.run(run_iclr_benchmark(
        dataset_dir=args.dataset_dir,
        output_dir=args.output_dir,
        sample_size=args.sample_size,
        balance_decisions=args.balance_decisions,
        use_images=args.use_images,
        deterministic=not args.stochastic,
        use_sections=args.use_sections,
        use_a3pr=args.use_a3pr,
        use_self_critique=args.use_self_critique,
        use_arxiv_search=args.use_arxiv_search,
        skip_golden_creation=args.skip_golden_creation,
        run_experiments=args.run_experiments,
    ))
    
    if "error" in run_dir:
        print(f"\n❌ Benchmark failed: {run_dir['error']}")
        return 1
    
    print(f"\n✅ Benchmark completed successfully!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
