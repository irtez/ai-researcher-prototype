"""
Adapter to run Holosophus librarian against the LitSearch benchmark.

Uses the original LitSearch evaluation format: for each query, produces
a list of retrieved corpus_ids. Then uses the original LitSearch metrics
(Recall@5, Recall@20, nDCG) for evaluation.

Follows the exact same protocol as the LitSearch paper (EMNLP 2024).

Usage:
    # Run on our sample (50 queries)
    cd Holosophus/holosophos
    uv run python -m reports.adapters.litsearch_adapter \
        --query_path reports/data/litsearch_sample.jsonl \
        --output_path reports/results/litsearch_holosophus.jsonl

    # Evaluate (uses original LitSearch metrics)
    uv run python -m reports.adapters.litsearch_adapter --evaluate \
        --results_path reports/results/litsearch_holosophus.jsonl
"""

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Optional

import fire  # type: ignore
from datasets import load_dataset
from dotenv import load_dotenv

from codearkt.server import run_batch
from holosophos.main_agent import MCP_CONFIG, compose_main_agent


def _build_title_to_corpusid_map() -> dict[str, int]:
    """Build a mapping from lowercase title → corpus_id from the LitSearch corpus."""
    corpus = load_dataset("princeton-nlp/LitSearch", "corpus_clean", split="full")
    title_map: dict[str, int] = {}
    for item in corpus:
        title = item["title"].strip().lower()
        title_map[title] = item["corpusid"]
    return title_map


def _extract_corpus_ids_from_result(
    result_text: str, title_map: dict[str, int]
) -> list[int]:
    """Extract corpus_ids from agent's text output by matching paper titles."""
    result_lower = result_text.lower()
    matched_ids: list[int] = []
    seen: set[int] = set()

    for title, cid in title_map.items():
        if len(title) > 15 and title in result_lower:
            if cid not in seen:
                matched_ids.append(cid)
                seen.add(cid)

    # Also try to match corpus IDs mentioned directly (unlikely but possible)
    for num_match in re.findall(r"\b(\d{5,10})\b", result_text):
        cid = int(num_match)
        if cid in set(title_map.values()) and cid not in seen:
            matched_ids.append(cid)
            seen.add(cid)

    return matched_ids


def calculate_recall(retrieved: list[int], relevant: list[int]) -> float:
    """Original LitSearch recall metric."""
    num_relevant_retrieved = len(set(retrieved).intersection(set(relevant)))
    num_relevant = len(relevant)
    return num_relevant_retrieved / num_relevant if num_relevant > 0 else 0


def calculate_ndcg(retrieved: list[int], relevant: list[int]) -> float:
    """Original LitSearch nDCG metric."""
    dcg = 0.0
    for idx, docid in enumerate(retrieved):
        if docid in relevant:
            dcg += 1 / (idx + 1)
    idcg = sum(1 / (idx + 1) for idx in range(len(relevant)))
    return dcg / idcg if idcg > 0 else 0


async def run_litsearch(
    query_path: str = "reports/data/litsearch_sample.jsonl",
    output_path: str = "reports/results/litsearch_holosophus.jsonl",
    model_name: str = "deepseek/deepseek-chat-v3-0324",
    max_workers: int = 1,
    nrows: Optional[int] = None,
    verbosity_level: int = logging.WARNING,
) -> None:
    """Run Holosophus librarian on LitSearch queries and save results."""
    load_dotenv()

    # Load queries
    with open(query_path) as f:
        records = [json.loads(line) for line in f]
    if nrows:
        records = records[:nrows]

    print(f"Loading LitSearch corpus title map...")
    title_map = _build_title_to_corpusid_map()
    print(f"  Loaded {len(title_map)} titles")

    # Compose librarian-only agent
    agent = compose_main_agent(
        model_name=model_name,
        verbosity_level=verbosity_level,
        included_agents=("librarian",),
    )

    queries = [r["query"] for r in records]
    print(f"\nRunning {len(queries)} LitSearch queries...")
    start_time = time.time()

    results = await run_batch(
        queries,
        agent,
        mcp_config=MCP_CONFIG,
        max_concurrency=max_workers,
        add_mcp_server_prefixes=False,
    )

    total_time = time.time() - start_time

    # Process results
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    output_records = []
    for record, result_text in zip(records, results):
        retrieved = _extract_corpus_ids_from_result(str(result_text), title_map)
        out = {
            **record,
            "retrieved": retrieved,
            "agent_response": str(result_text)[:2000],
        }
        output_records.append(out)

    with open(output_path, "w") as f:
        for rec in output_records:
            f.write(json.dumps(rec) + "\n")
    print(f"\nSaved {len(output_records)} results to {output_path}")
    print(f"Total time: {total_time:.0f}s ({total_time/len(queries):.0f}s/query)")


def evaluate(
    results_path: str = "reports/results/litsearch_holosophus.jsonl",
) -> None:
    """Evaluate results using original LitSearch metrics (Recall@K, nDCG)."""
    with open(results_path) as f:
        records = [json.loads(line) for line in f]

    recall_at_5_list = []
    recall_at_20_list = []
    ndcg_list = []

    # Also track by specificity
    recall_by_spec: dict[int, list[float]] = {0: [], 1: []}

    for record in records:
        retrieved = record["retrieved"]
        relevant = record["target_corpus_ids"]
        spec = record.get("specificity", 1)

        r5 = calculate_recall(retrieved[:5], relevant)
        r20 = calculate_recall(retrieved[:20], relevant)
        ndcg = calculate_ndcg(retrieved, relevant)

        recall_at_5_list.append(r5)
        recall_at_20_list.append(r20)
        ndcg_list.append(ndcg)
        recall_by_spec[spec].append(r5)

    n = len(records)
    avg_r5 = sum(recall_at_5_list) / n * 100 if n else 0
    avg_r20 = sum(recall_at_20_list) / n * 100 if n else 0
    avg_ndcg = sum(ndcg_list) / n * 100 if n else 0

    print(f"\n{'='*60}")
    print(f"LitSearch Evaluation Results ({n} queries)")
    print(f"{'='*60}")
    print(f"  Recall@5:  {avg_r5:.1f}%")
    print(f"  Recall@20: {avg_r20:.1f}%")
    print(f"  nDCG:      {avg_ndcg:.1f}%")
    print()
    print("By specificity:")
    for spec, recalls in recall_by_spec.items():
        label = "broad" if spec == 0 else "specific"
        avg = sum(recalls) / len(recalls) * 100 if recalls else 0
        print(f"  {label} (n={len(recalls)}): Recall@5 = {avg:.1f}%")
    print()
    print("Reference SOTA (full 597 queries):")
    print("  GritLM:        74.8% Recall@5")
    print("  BM25:          50.0% Recall@5")
    print("  Google Search: 42.8% Recall@5")
    print(f"{'='*60}")


def main(
    evaluate: bool = False,
    results_path: str = "reports/results/litsearch_holosophus.jsonl",
    **kwargs: Any,
) -> None:
    if evaluate:
        globals()["evaluate"](results_path=results_path)
    else:
        asyncio.run(run_litsearch(**kwargs))


if __name__ == "__main__":
    fire.Fire(main)
