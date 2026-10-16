#!/usr/bin/env python3
"""Eval runner for Holosophus over Claude Code (Agent SDK).

Replaces CodeArkt's run_batch() with claude-agent-sdk query() calls.
Runs eval questions as Claude Code sub-agents in parallel.

Usage:
    # Librarian eval
    python scripts/run_eval_claude_code.py librarian \
        --data holosophos/reports/data/librarian_test.jsonl \
        --max-parallel 4

    # GAIA eval (subset)
    python scripts/run_eval_claude_code.py gaia \
        --split validation --start-index 0 --nrows 10 \
        --max-parallel 6

    # GAIA with file attachments
    python scripts/run_eval_claude_code.py gaia \
        --split validation --nrows 5 \
        --max-parallel 4

Results saved to workdir/eval_<benchmark>_<timestamp>/
"""

import argparse
import asyncio
import json
import os
import re
import string
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# ── Paths ───────────────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).resolve().parent.parent
WORKDIR = PROJECT_DIR / "workdir"
REPORTS_DATA = PROJECT_DIR / "holosophos" / "reports" / "data"


# ── GAIA answer scoring (ported from run_gaia.py) ──────────────────────
def _normalize_number_str(number_str: str) -> float:
    for char in ["$", "%", ","]:
        number_str = number_str.replace(char, "")
    try:
        return float(number_str)
    except ValueError:
        return float("inf")


def _normalize_str(input_str: str, remove_punct: bool = True) -> str:
    no_spaces = re.sub(r"\s", "", input_str)
    if remove_punct:
        translator = str.maketrans("", "", string.punctuation)
        return no_spaces.lower().translate(translator)
    return no_spaces.lower()


def _split_string(s: str) -> list[str]:
    return re.split(r"[,;]", s)


def gaia_answer_scorer(model_answer: Optional[str], ground_truth: str) -> bool:
    def is_float(element: Any) -> bool:
        try:
            float(element)
            return True
        except ValueError:
            return False

    if model_answer is None:
        model_answer = "None"

    if is_float(ground_truth):
        return _normalize_number_str(model_answer) == float(ground_truth)
    elif any(c in ground_truth for c in [",", ";"]):
        gt_elems = _split_string(ground_truth)
        ma_elems = _split_string(model_answer)
        if len(gt_elems) != len(ma_elems):
            return False
        return all(
            _normalize_number_str(ma) == float(gt) if is_float(gt)
            else _normalize_str(ma, False) == _normalize_str(gt, False)
            for ma, gt in zip(ma_elems, gt_elems)
        )
    else:
        return _normalize_str(model_answer) == _normalize_str(ground_truth)


def get_final_answer(text: str) -> str:
    if "Final answer:**" in text:
        return text.split("Final answer:**")[-1].strip()
    if "Final answer:" in text:
        return text.split("Final answer:")[-1].strip()
    return text.strip()


# ── Librarian scoring ──────────────────────────────────────────────────
def librarian_scorer(response: str, targets: list[str]) -> bool:
    return any(arxiv_id in response for arxiv_id in targets)


# ── Claude Code Agent SDK runner ───────────────────────────────────────
async def run_single_query(
    query: str,
    agent_type: str = "librarian",
    workdir_name: str = "eval",
    question_id: int = 0,
) -> str:
    """Run a single eval question via Claude Agent SDK."""
    try:
        from claude_agent_sdk import query as sdk_query, ClaudeAgentOptions

        result_text = ""
        async for message in sdk_query(
            prompt=query,
            options=ClaudeAgentOptions(
                allowed_tools=["Read", "Write", "Edit", "Bash", "Glob", "Grep",
                               "WebSearch", "WebFetch",
                               "mcp__academia__*", "mcp__alphaxiv__*",
                               "mcp__mle_kit__*"],
                model="opus",
                effort="high",
                max_turns=50,
            ),
        ):
            if hasattr(message, "result"):
                result_text = message.result
        return result_text
    except ImportError:
        # Fallback: use claude CLI
        import subprocess
        result = subprocess.run(
            ["claude", "-p", query, "--output-format", "text", "--max-turns", "50"],
            capture_output=True, text=True, timeout=600,
            cwd=str(PROJECT_DIR),
        )
        return result.stdout


async def run_eval_batch(
    questions: list[dict],
    agent_type: str,
    eval_dir: Path,
    max_parallel: int = 4,
) -> list[dict]:
    """Run eval questions with bounded parallelism."""
    semaphore = asyncio.Semaphore(max_parallel)
    results = []

    async def run_one(idx: int, q: dict) -> dict:
        async with semaphore:
            query = q["query"]
            print(f"  [{idx+1}/{len(questions)}] Starting: {query[:80]}...")
            start = time.time()
            try:
                response = await run_single_query(
                    query=query,
                    agent_type=agent_type,
                    question_id=idx,
                )
            except Exception as e:
                response = f"ERROR: {e}"
            elapsed = time.time() - start
            result = {
                "idx": idx,
                "query": query,
                "response": response,
                "elapsed_s": round(elapsed, 1),
                **{k: v for k, v in q.items() if k != "query"},
            }
            # Write individual result
            with open(eval_dir / f"q{idx:03d}.json", "w") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            print(f"  [{idx+1}/{len(questions)}] Done ({elapsed:.0f}s)")
            return result

    tasks = [run_one(i, q) for i, q in enumerate(questions)]
    results = await asyncio.gather(*tasks)
    return sorted(results, key=lambda r: r["idx"])


# ── Benchmark loaders ──────────────────────────────────────────────────
def load_librarian_data(data_path: str) -> list[dict]:
    with open(data_path) as f:
        return [json.loads(line) for line in f if line.strip()]


def load_gaia_data(split: str, start_index: int, nrows: Optional[int]) -> list[dict]:
    from datasets import load_dataset
    ds = load_dataset("gaia-benchmark/GAIA", "2023_all")[split]
    records = []
    for i, row in enumerate(ds):
        if i < start_index:
            continue
        if nrows and len(records) >= nrows:
            break
        question = row["Question"]
        prompt = f"""You have one question to answer. It is paramount that you provide a correct answer.
Give it all you can: I know for a fact that you have access to all the relevant tools to solve it.
Failure or 'I cannot answer' will not be tolerated, success will be rewarded.

Here is the question:
===
{question}
===

Return only the answer after 'Final answer:'.
Don't provide any explanations after the 'Final answer:' line."""
        records.append({
            "query": prompt,
            "true_answer": row["Final answer"],
            "level": row["Level"],
            "has_file": bool(row["file_path"]),
        })
    return records


# ── Scoring and reporting ──────────────────────────────────────────────
def score_and_report(results: list[dict], benchmark: str, eval_dir: Path):
    correct = 0
    total = len(results)

    report_lines = [f"# Eval Report: {benchmark}", f"Date: {datetime.now().isoformat()}", ""]

    for r in results:
        if benchmark == "librarian":
            targets = r.get("target", [])
            is_correct = librarian_scorer(r["response"], targets)
        elif benchmark == "gaia":
            predicted = get_final_answer(r["response"])
            is_correct = gaia_answer_scorer(predicted, r["true_answer"])
        else:
            is_correct = False

        correct += int(is_correct)
        status = "PASS" if is_correct else "FAIL"
        report_lines.append(f"**Q{r['idx']+1}** [{status}] ({r['elapsed_s']}s)")
        report_lines.append(f"  Query: {r['query'][:120]}...")
        if benchmark == "librarian":
            report_lines.append(f"  Target: {r.get('target', [])}")
        elif benchmark == "gaia":
            report_lines.append(f"  True: {r.get('true_answer', '?')}")
            report_lines.append(f"  Predicted: {get_final_answer(r['response'])[:200]}")
        report_lines.append(f"  Response: {r['response'][:200]}...")
        report_lines.append("")

    accuracy = correct / total * 100 if total else 0
    summary = f"## Summary\n\nAccuracy: {correct}/{total} = {accuracy:.1f}%\n"
    report_lines.insert(3, summary)

    report_path = eval_dir / "report.md"
    with open(report_path, "w") as f:
        f.write("\n".join(report_lines))

    # Also save raw results
    with open(eval_dir / "results.jsonl", "w") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n{'='*60}")
    print(f"  {benchmark.upper()} EVAL: {correct}/{total} = {accuracy:.1f}%")
    print(f"  Report: {report_path}")
    print(f"{'='*60}\n")


# ── Main ───────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Run evals over Claude Code")
    parser.add_argument("benchmark", choices=["librarian", "gaia"])
    parser.add_argument("--data", type=str, help="Path to JSONL data file (librarian)")
    parser.add_argument("--split", type=str, default="validation", help="GAIA split")
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--nrows", type=int, default=None)
    parser.add_argument("--max-parallel", type=int, default=4)
    args = parser.parse_args()

    # Regenerate .claude/agents/*.md from YAML so the SDK reads the current
    # canonical prompts. The .md files are gitignored artifacts; YAML wins.
    sys.path.insert(0, str(PROJECT_DIR / "scripts"))
    from sync_claude_agents import ensure_synced

    ensure_synced()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    eval_dir = WORKDIR / f"eval_{args.benchmark}_{timestamp}"
    eval_dir.mkdir(parents=True, exist_ok=True)

    if args.benchmark == "librarian":
        if not args.data:
            print("Error: --data required for librarian eval")
            sys.exit(1)
        questions = load_librarian_data(args.data)
    elif args.benchmark == "gaia":
        questions = load_gaia_data(args.split, args.start_index, args.nrows)

    if args.start_index:
        questions = questions[args.start_index:]
    if args.nrows:
        questions = questions[:args.nrows]

    print(f"Running {args.benchmark} eval: {len(questions)} questions, max_parallel={args.max_parallel}")
    print(f"Output: {eval_dir}")

    results = asyncio.run(run_eval_batch(questions, args.benchmark, eval_dir, args.max_parallel))
    score_and_report(results, args.benchmark, eval_dir)


if __name__ == "__main__":
    main()
