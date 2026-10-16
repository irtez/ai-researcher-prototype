"""
Adapter to run Holosophus librarian against the InfoDeepSeek benchmark.

Produces output in the EXACT format expected by InfoDeepSeek's eval/eval.py,
so we can use their original LLM-as-Judge evaluator and cal_acc.py metrics.

Usage:
    # Run agent on our sample (30 queries)
    cd Holosophus/holosophos
    uv run python -m reports.adapters.infodeepseek_adapter \
        --query_path reports/data/infodeepseek_sample.jsonl \
        --output_path reports/results/infodeepseek_holosophus_en.jsonl

    # Evaluate with original InfoDeepSeek eval (requires InfoDeepSeek repo):
    cd /tmp/InfoDeepSeek
    python eval/eval.py \
        --input_file <path_to_output> \
        --eval_llm_name deepseek-chat --reuse
    python eval/cal_acc.py \
        --input_file <scored_output> --print_attribute
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

import fire  # type: ignore
from dotenv import load_dotenv

from codearkt.server import run_batch
from holosophos.main_agent import MCP_CONFIG, compose_main_agent


async def run_infodeepseek(
    query_path: str = "reports/data/infodeepseek_sample.jsonl",
    output_path: str = "reports/results/infodeepseek_holosophus_en.jsonl",
    model_name: str = "deepseek/deepseek-chat-v3-0324",
    max_workers: int = 1,
    nrows: Optional[int] = None,
    verbosity_level: int = logging.WARNING,
) -> None:
    """Run Holosophus librarian on InfoDeepSeek queries, output in their eval format."""
    load_dotenv()

    # Load our sample (already in simplified format)
    with open(query_path) as f:
        records = [json.loads(line) for line in f]
    if nrows:
        records = records[:nrows]

    # Also load the original full data to get all required fields for eval
    original_data: dict[int, dict[str, Any]] = {}
    original_path = Path("/tmp/InfoDeepSeek/data/InfoDeepSeek_v1.json")
    if original_path.exists():
        with open(original_path) as f:
            for item in json.load(f):
                original_data[item["id"]] = item

    # Compose librarian-only agent
    agent = compose_main_agent(
        model_name=model_name,
        verbosity_level=verbosity_level,
        included_agents=("librarian",),
    )

    queries = [r["query"] for r in records]
    print(f"Running {len(queries)} InfoDeepSeek queries...")
    start_time = time.time()

    results = await run_batch(
        queries,
        agent,
        mcp_config=MCP_CONFIG,
        max_concurrency=max_workers,
        add_mcp_server_prefixes=False,
    )

    total_time = time.time() - start_time

    # Build output in InfoDeepSeek eval format
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        for record, result_text in zip(records, results):
            response = str(result_text)

            # Get original record ID (infodeepseek_123 → 123)
            record_id = int(record["id"].replace("infodeepseek_", ""))
            orig = original_data.get(record_id, {})

            # Build output in exact InfoDeepSeek eval format
            out = {
                "id": record_id,
                "query_zh": orig.get("query_zh", ""),
                "query_en": record["query"],
                "answer_zh": orig.get("answer_zh", ""),
                "answer_en": record["answer"],
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
                    "response": response,
                    "more_info": {
                        "answer_at_k": {"1": response},
                        "ranked_webpages": [],
                        "offline_response": "",
                    },
                },
            }
            f.write(json.dumps(out, ensure_ascii=False) + "\n")

    print(f"\nSaved {len(records)} results to {output_path}")
    print(f"Total time: {total_time:.0f}s ({total_time/len(queries):.0f}s/query)")
    print(f"\nNext: evaluate with original InfoDeepSeek eval:")
    print(f"  cd /tmp/InfoDeepSeek")
    print(f"  python eval/eval.py --input_file {Path(output_path).resolve()} "
          f"--eval_llm_name deepseek-chat --reuse")
    print(f"  python eval/cal_acc.py --input_file <scored_file> --print_attribute")


def main(**kwargs: Any) -> None:
    asyncio.run(run_infodeepseek(**kwargs))


if __name__ == "__main__":
    fire.Fire(main)
