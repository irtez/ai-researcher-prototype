"""
Unified benchmark runner for Holosophus librarian.

Calls /agents/librarian DIRECTLY (bypasses manager for speed).
Runs all 3 benchmark samples with parallel workers.
Outputs results progressively as each query completes.

Usage:
    # Smoke: 1 query from each benchmark (3 total)
    uv run python reports/run_eval_via_api.py --nrows 1

    # Full run: all 95 queries, 4 workers
    uv run python reports/run_eval_via_api.py --experiment_name baseline_v2

    # Single benchmark
    uv run python reports/run_eval_via_api.py --benchmarks librarian

    # Custom parallelism
    uv run python reports/run_eval_via_api.py --max_workers 8
"""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import fire  # type: ignore
import requests


APP_URL = "http://localhost:5055"
AGENT_ENDPOINT = "/agents/librarian"  # Direct librarian, skip manager
DATA_DIR = Path(__file__).parent / "data"
RESULTS_DIR = Path(__file__).parent / "results"

BENCHMARK_FILES = {
    "librarian": "librarian_test_sample.jsonl",
    "litsearch": "litsearch_sample.jsonl",
    "infodeepseek": "infodeepseek_sample.jsonl",
}


def query_librarian(query: str, session_id: str, timeout: int = 600) -> str:
    """Send a query directly to the librarian agent."""
    resp = requests.post(
        f"{APP_URL}{AGENT_ENDPOINT}",
        json={
            "messages": [{"role": "user", "content": query}],
            "session_id": session_id,
            "stream": False,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.text


def evaluate_result(record: dict, result: str) -> bool:
    """Evaluate correctness based on benchmark type."""
    # librarian_test: ArXiv ID substring match (original eval protocol)
    if "target_arxiv_ids" in record:
        return any(aid in result for aid in record["target_arxiv_ids"])

    # litsearch: corpus ID match (need to check if numeric IDs appear)
    if "target_corpus_ids" in record:
        return any(str(cid) in result for cid in record["target_corpus_ids"])

    # infodeepseek: save raw result for later LLM-judge eval
    # Return None — will be evaluated by original eval/eval.py
    return False  # placeholder — real eval via InfoDeepSeek pipeline


def run_one(args: tuple) -> dict:
    """Run single query. For ThreadPoolExecutor."""
    idx, bench_name, record, experiment, timeout = args
    query = record.get("query", "")
    session_id = f"{experiment}__{bench_name}__q{idx:03d}"

    t0 = time.time()
    try:
        result = query_librarian(query, session_id, timeout=timeout)
    except Exception as e:
        result = f"ERROR: {e}"
    elapsed = time.time() - t0

    correct = evaluate_result(record, result)
    tag = "PASS" if correct else "FAIL"
    print(f"  [{tag}] {bench_name} Q{idx+1} ({elapsed:.0f}s): {query[:65]}...",
          flush=True)

    return {
        "benchmark": bench_name,
        "index": idx,
        "query": query,
        "correct": correct,
        "time_s": round(elapsed),
        "result": result[:2000],
        "record": record,
    }


def run_eval(
    benchmarks: str = "all",
    nrows: Optional[int] = None,
    experiment_name: str = "baseline",
    max_workers: int = 4,
    timeout_per_query: int = 600,
) -> None:
    """Run librarian evaluation on benchmark samples.

    Args:
        benchmarks: Comma-separated list or "all". E.g. "librarian,litsearch"
        nrows: Max queries per benchmark (for smoke testing)
        experiment_name: Name for this experiment run
        max_workers: Parallel query workers
        timeout_per_query: HTTP timeout per query in seconds
    """
    if benchmarks == "all":
        bench_list = list(BENCHMARK_FILES.keys())
    else:
        bench_list = [b.strip() for b in benchmarks.split(",")]

    # Verify app
    try:
        agents = requests.get(f"{APP_URL}/agents/list", timeout=5).json()
        agent_names = [a["name"] for a in agents]
        assert "librarian" in agent_names, "librarian agent not found!"
        print(f"App OK, agents: {agent_names}")
    except Exception as e:
        print(f"ERROR: App not reachable at {APP_URL}: {e}")
        sys.exit(1)

    # Load all queries
    all_tasks = []
    for bench_name in bench_list:
        fname = BENCHMARK_FILES.get(bench_name)
        if not fname:
            print(f"Unknown benchmark: {bench_name}")
            continue
        fpath = DATA_DIR / fname
        if not fpath.exists():
            print(f"Missing: {fpath}")
            continue
        with open(fpath) as f:
            records = [json.loads(line) for line in f]
        if nrows:
            records = records[:nrows]
        for i, rec in enumerate(records):
            all_tasks.append((i, bench_name, rec, experiment_name, timeout_per_query))
        print(f"  {bench_name}: {len(records)} queries")

    total = len(all_tasks)
    print(f"\n{'='*60}")
    print(f"Experiment: {experiment_name}")
    print(f"Total queries: {total}, Workers: {max_workers}")
    print(f"Endpoint: {AGENT_ENDPOINT} (direct librarian)")
    print(f"{'='*60}\n")

    t_start = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(run_one, t): t for t in all_tasks}
        for future in as_completed(futures):
            results.append(future.result())

    t_total = time.time() - t_start

    # Per-benchmark summary
    print(f"\n{'='*60}")
    print(f"RESULTS (experiment: {experiment_name})")
    print(f"{'='*60}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    infodeepseek_results = []  # For later LLM-judge eval

    for bench_name in bench_list:
        bench_results = [r for r in results if r["benchmark"] == bench_name]
        if not bench_results:
            continue
        correct = sum(1 for r in bench_results if r["correct"])
        total_b = len(bench_results)
        acc = correct / total_b * 100 if total_b else 0
        avg_t = sum(r["time_s"] for r in bench_results) / total_b if total_b else 0

        print(f"\n  {bench_name}: {correct}/{total_b} ({acc:.1f}%) avg={avg_t:.0f}s/query")
        for r in sorted(bench_results, key=lambda x: x["index"]):
            tag = "PASS" if r["correct"] else "FAIL"
            print(f"    [{tag}] Q{r['index']+1}: {r['query'][:70]}...")

        # Collect InfoDeepSeek results for LLM-judge
        if bench_name == "infodeepseek":
            infodeepseek_results = bench_results

    overall_correct = sum(1 for r in results if r["correct"])
    print(f"\n  OVERALL: {overall_correct}/{total} ({overall_correct/total*100:.1f}%)")
    print(f"  Wall time: {t_total:.0f}s ({t_total/total:.0f}s/query effective)")

    # Save raw results
    raw_path = RESULTS_DIR / f"{experiment_name}_raw.jsonl"
    with open(raw_path, "w") as f:
        for r in results:
            entry = {k: v for k, v in r.items() if k != "record"}
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"\n  Raw results: {raw_path}")

    # Save InfoDeepSeek results in their eval format
    if infodeepseek_results:
        ids_path = RESULTS_DIR / f"{experiment_name}_infodeepseek_en.jsonl"
        _save_infodeepseek_format(infodeepseek_results, ids_path)
        print(f"  InfoDeepSeek eval input: {ids_path}")
        print(f"  Run: cd /tmp/InfoDeepSeek && python eval/eval.py --input_file {ids_path.resolve()}")

    # Append to experiment log
    log_entry = {
        "experiment": experiment_name,
        "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_queries": total,
        "wall_time_s": round(t_total),
        "max_workers": max_workers,
    }
    for bench_name in bench_list:
        br = [r for r in results if r["benchmark"] == bench_name]
        if br:
            log_entry[f"{bench_name}_correct"] = sum(1 for r in br if r["correct"])
            log_entry[f"{bench_name}_total"] = len(br)
            log_entry[f"{bench_name}_accuracy"] = round(
                sum(1 for r in br if r["correct"]) / len(br) * 100, 1
            )
    log_path = RESULTS_DIR / "experiment_log.jsonl"
    with open(log_path, "a") as f:
        f.write(json.dumps(log_entry) + "\n")
    print(f"  Experiment log: {log_path}")
    print(f"{'='*60}")


def _save_infodeepseek_format(results: list, output_path: Path) -> None:
    """Save InfoDeepSeek results in their original eval format."""
    original_data = {}
    orig_path = Path("/tmp/InfoDeepSeek/data/InfoDeepSeek_v1.json")
    if orig_path.exists():
        with open(orig_path) as f:
            for item in json.load(f):
                original_data[item["id"]] = item

    with open(output_path, "w") as f:
        for r in results:
            record = r["record"]
            record_id = int(record["id"].replace("infodeepseek_", ""))
            orig = original_data.get(record_id, {})
            out = {
                "id": record_id,
                "query_en": record["query"],
                "answer_en": record.get("answer", ""),
                "query_zh": orig.get("query_zh", ""),
                "answer_zh": orig.get("answer_zh", ""),
                "false_premise": orig.get("false_premise", False),
                "domain": record.get("domain", []),
                "advantage_language": orig.get("advantage_language", []),
                "difficulty_GPT": record.get("difficulty_hard", False),
                "difficulty_DS": orig.get("difficulty_DS", False),
                "multi_hop": record.get("multi_hop", False),
                "long_tail": orig.get("long_tail", False),
                "time_sensitive": orig.get("time_sensitive", False),
                "freshness": orig.get("freshness", False),
                "distracting_info": orig.get("distracting_info", False),
                "sources": record.get("sources", []),
                "result": {
                    "response": r["result"],
                    "more_info": {
                        "answer_at_k": {"1": r["result"]},
                        "ranked_webpages": [],
                        "offline_response": "",
                    },
                },
            }
            f.write(json.dumps(out, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    fire.Fire(run_eval)
