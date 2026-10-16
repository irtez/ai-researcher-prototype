#!/usr/bin/env python3
"""Run librarian eval via Claude Agent SDK — parallel batches.

Splits questions across N parallel librarian agents, each processing
a batch sequentially. Results saved to a shared output directory.

Usage (from Holosophus/):
    uv run python scripts/run_librarian_eval.py                     # all 95q, 6 workers
    uv run python scripts/run_librarian_eval.py --workers 8         # 8 parallel workers
    uv run python scripts/run_librarian_eval.py --dataset librarian # only librarian (15q)
    uv run python scripts/run_librarian_eval.py --max-questions 5   # smoke test (5q)
"""

import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")

EVALS_DATA = PROJECT_DIR / "holosophos" / "evals_and_reports" / "data"
RESULTS_RAW = PROJECT_DIR / "holosophos" / "evals_and_reports" / "results" / "raw"

DATASETS = {
    "librarian": {
        "file": EVALS_DATA / "librarian_test_sample.jsonl",
        "verify": "arxiv_id_in_response",
    },
    "litsearch": {
        "file": EVALS_DATA / "litsearch_sample.jsonl",
        "verify": "corpus_id_found",
    },
    "infodeepseek": {
        "file": EVALS_DATA / "infodeepseek_sample.jsonl",
        "verify": "keyword_in_response",
    },
}


def load_questions(dataset_filter: str | None = None, max_questions: int | None = None) -> list[dict]:
    all_by_ds: dict[str, list[dict]] = {}
    for name, ds in DATASETS.items():
        if dataset_filter and name != dataset_filter:
            continue
        items = []
        with open(ds["file"]) as f:
            for line in f:
                q = json.loads(line)
                q["_benchmark"] = name
                q["_verify"] = ds["verify"]
                items.append(q)
        all_by_ds[name] = items

    if max_questions and not dataset_filter and len(all_by_ds) > 1:
        # Stratified sampling: distribute proportionally across datasets
        total_available = sum(len(v) for v in all_by_ds.values())
        questions = []
        remaining = max_questions
        ds_names = sorted(all_by_ds.keys())
        for i, name in enumerate(ds_names):
            items = all_by_ds[name]
            if i == len(ds_names) - 1:
                n_take = remaining
            else:
                n_take = max(1, round(max_questions * len(items) / total_available))
            n_take = min(n_take, len(items), remaining)
            questions.extend(items[:n_take])
            remaining -= n_take
        return questions

    questions = [q for items in all_by_ds.values() for q in items]
    if max_questions:
        questions = questions[:max_questions]
    return questions


def format_query_for_librarian(q: dict) -> str:
    """Format a question into a librarian task description."""
    benchmark = q["_benchmark"]
    query = q["query"]

    if benchmark == "librarian":
        return (
            f"Find the paper described by this query and return its ArXiv ID.\n\n"
            f"Query: {query}\n\n"
            f"Reply with ONLY the ArXiv ID (e.g., 2203.17270). Nothing else."
        )
    elif benchmark == "litsearch":
        return (
            f"Find academic papers matching this query. Search using multiple tools.\n\n"
            f"Query: {query}\n\n"
            f"For each found paper, include ALL available identifiers: "
            f"ArXiv ID, Semantic Scholar corpus ID (numeric), DOI, and title. "
            f"Use Semantic Scholar API (s2_search, s2_paper_details) to get corpus IDs."
        )
    elif benchmark == "infodeepseek":
        return (
            f"Answer this factual question by searching academic papers and the web. "
            f"Provide a concise, specific answer.\n\n"
            f"Question: {query}\n\n"
            f"Reply with the factual answer."
        )
    return query


def verify_result(q: dict, response: str) -> bool:
    """Best-effort automatic check. Not authoritative — use manual review."""
    response = str(response)
    response_lower = response.lower()
    benchmark = q["_benchmark"]

    if benchmark == "librarian":
        targets = q.get("target_arxiv_ids", q.get("target", []))
        return any(t.lower() in response_lower for t in targets)
    elif benchmark == "litsearch":
        corpus_ids = q.get("target_corpus_ids", [])
        return any(str(cid) in response for cid in corpus_ids)
    elif benchmark == "infodeepseek":
        answer = q.get("answer", "")
        # Simple substring check on first 30 chars of answer
        return answer.lower()[:30] in response_lower
    return False


class Progress:
    """Thread-safe progress counter shared across workers."""

    def __init__(self, total: int):
        self.total = total
        self.done = 0
        self.found = 0
        self._lock = asyncio.Lock()

    async def record(self, found: bool) -> str:
        async with self._lock:
            self.done += 1
            if found:
                self.found += 1
            pct = 100 * self.done / self.total
            acc = 100 * self.found / self.done
            return f"[{self.done}/{self.total} ({pct:.0f}%) acc={acc:.1f}%]"


async def worker_loop(
    queue: "asyncio.Queue[dict]",
    worker_id: int,
    output_dir: Path,
    session_id: str,
    results: list[dict],
    progress: Progress,
) -> None:
    """Pull questions from shared queue until empty, one agent per question."""
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

    agents_dir = PROJECT_DIR / ".claude" / "agents"
    librarian_md = agents_dir / "librarian.md"

    text = librarian_md.read_text()
    if text.startswith("---"):
        end = text.index("---", 3)
        text = text[end + 3:].strip()

    mcp_servers = json.loads((PROJECT_DIR / ".mcp.json").read_text()).get("mcpServers", {})

    q_num = 0
    while True:
        try:
            q = queue.get_nowait()
        except asyncio.QueueEmpty:
            break

        q_num += 1
        query_text = format_query_for_librarian(q)
        qid = q.get("id", f"w{worker_id}_q{q_num}")
        print(f"  [worker-{worker_id}] [{q['_benchmark']}] {q['query'][:60]}...", flush=True)

        t0 = time.time()
        response = ""
        error = ""

        try:
            async with ClaudeSDKClient(options=ClaudeAgentOptions(
                system_prompt=text,
                cwd=str(output_dir),
                model="opus",
                max_turns=30,
                permission_mode="bypassPermissions",
                mcp_servers=mcp_servers,
            )) as client:
                await client.query(query_text, session_id=session_id)
                async for msg in client.receive_response():
                    mt = type(msg).__name__
                    if mt == "ResultMessage":
                        response = getattr(msg, "result", "") or ""
        except Exception as e:
            error = str(e)
            print(f"  [worker-{worker_id}] ERROR: {error[:100]}", flush=True)

        elapsed = time.time() - t0
        found = verify_result(q, response) if response else False

        result = {
            "benchmark": q["_benchmark"],
            "id": qid,
            "query": q["query"],
            "found": found,
            "time_s": round(elapsed, 1),
            "response_preview": response[:2000] if response else error[:500],
        }
        results.append(result)

        status = "FOUND" if found else "MISS"
        prog = await progress.record(found)
        print(f"  [worker-{worker_id}] → {status} ({elapsed:.0f}s)  {prog}", flush=True)

        with open(output_dir / f"worker_{worker_id}.jsonl", "a") as f:
            f.write(json.dumps(result) + "\n")

        queue.task_done()


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Librarian eval via Claude SDK")
    parser.add_argument("--workers", type=int, default=6, help="Parallel workers (default: 6)")
    parser.add_argument("--dataset", type=str, default=None, help="Filter: librarian, litsearch, infodeepseek")
    parser.add_argument("--max-questions", type=int, default=None, help="Limit total questions")
    args = parser.parse_args()

    # ── Langfuse v3 + langsmith tracing ──
    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", os.environ.get("LANGFUSE_PUBLIC_KEY_V3", ""))
    os.environ.setdefault("LANGFUSE_SECRET_KEY", os.environ.get("LANGFUSE_SECRET_KEY_V3", ""))
    os.environ.setdefault("LANGFUSE_BASE_URL", os.environ.get("LANGFUSE_BASE_URL_V3", "http://localhost:3051"))
    os.environ["LANGSMITH_OTEL_ENABLED"] = "true"
    os.environ["LANGSMITH_OTEL_ONLY"] = "true"
    os.environ["LANGSMITH_TRACING"] = "true"

    from langfuse import get_client as get_langfuse_client
    langfuse = get_langfuse_client()
    if not langfuse.auth_check():
        print("WARNING: Langfuse auth failed. Traces won't appear.", flush=True)

    # Patch ClaudeSDKClient with tracing BEFORE any worker imports it
    sys.path.insert(0, str(PROJECT_DIR / "scripts"))
    import langsmith_fork
    sys.modules["langsmith.integrations.claude_agent_sdk"] = langsmith_fork
    langsmith_fork.configure_claude_agent_sdk(tags=["eval", "librarian"])

    questions = load_questions(args.dataset, args.max_questions)
    print(f"Loaded {len(questions)} questions", flush=True)

    # Count per benchmark
    from collections import Counter
    counts = Counter(q["_benchmark"] for q in questions)
    for name, count in sorted(counts.items()):
        print(f"  {name}: {count}", flush=True)

    # Create output dir
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = RESULTS_RAW / f"claude_librarian_{ts}"
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output: {output_dir}", flush=True)

    # Build shared work queue
    n_workers = min(args.workers, len(questions))
    queue: asyncio.Queue[dict] = asyncio.Queue()
    for q in questions:
        queue.put_nowait(q)

    # Shared session_id — all questions grouped under one Langfuse session
    session_id = f"libeval_{ts}"
    print(f"  session_id: {session_id}", flush=True)
    print(f"  workers: {n_workers}", flush=True)

    # Run in parallel with dynamic dispatch
    print(f"\nStarting {n_workers} parallel workers...\n", flush=True)
    t0 = time.time()

    progress = Progress(total=len(questions))
    all_results_lists: list[list[dict]] = [[] for _ in range(n_workers)]
    worker_tasks = [
        worker_loop(queue, i, output_dir, session_id, all_results_lists[i], progress)
        for i in range(n_workers)
    ]
    await asyncio.gather(*worker_tasks)

    elapsed = time.time() - t0
    flat_results = [r for batch in all_results_lists for r in batch]

    # Aggregate
    print(f"\n{'='*60}", flush=True)
    print(f"  RESULTS ({elapsed:.0f}s total)", flush=True)
    print(f"{'='*60}", flush=True)

    by_benchmark: dict[str, list] = {}
    for r in flat_results:
        by_benchmark.setdefault(r["benchmark"], []).append(r)

    total_found = total_all = 0
    for name in sorted(by_benchmark):
        items = by_benchmark[name]
        found = sum(1 for r in items if r["found"])
        total = len(items)
        total_found += found
        total_all += total
        print(f"  {name:<15} {found}/{total} ({100*found/total:.1f}%)", flush=True)

    print(f"  {'TOTAL':<15} {total_found}/{total_all} ({100*total_found/total_all:.1f}%)", flush=True)
    print(flush=True)

    # Save summary
    summary = {
        "date": ts,
        "manager": "Claude Code",
        "model": "Opus 4.6",
        "effort": "high",
        "total_questions": total_all,
        "total_found": total_found,
        "accuracy": round(100 * total_found / total_all, 1),
        "wall_time_s": round(elapsed, 1),
        "workers": n_workers,
        "per_benchmark": {
            name: {
                "found": sum(1 for r in items if r["found"]),
                "total": len(items),
                "accuracy": round(100 * sum(1 for r in items if r["found"]) / len(items), 1),
            }
            for name, items in by_benchmark.items()
        },
    }
    with open(output_dir / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Saved: {output_dir / 'summary.json'}", flush=True)

    # Flush traces to Langfuse
    langfuse.flush()
    print(f"Langfuse session: {session_id}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
