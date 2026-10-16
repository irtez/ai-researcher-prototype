#!/usr/bin/env python3
"""GAIA eval runner over Claude Code Agent SDK.

Runs GAIA questions as full manager→agent pipelines via Agent SDK.
Each question gets its own Claude Code session with manager prompt
and access to librarian + mle_solver sub-agents.

Usage:
    # Run 30 stratified questions, 6 parallel workers
    python scripts/run_gaia_eval.py \
        --data evals_and_reports/data/gaia_eval_30_stratified.json \
        --max-parallel 6

    # Run specific shard (questions 0-4)
    python scripts/run_gaia_eval.py \
        --data evals_and_reports/data/gaia_eval_30_stratified.json \
        --start 0 --nrows 5
"""

import argparse
import asyncio
import json
import re
import string
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_DIR / "scripts"
WORKDIR = PROJECT_DIR / "workdir"

# Add scripts/ to path for langfuse_tracer import
sys.path.insert(0, str(SCRIPTS_DIR))

# ── Manager system prompt (from CLAUDE.md, research orchestration part) ──
MANAGER_PROMPT = """You are an expert manager who solves tasks using team members (sub-agents).
Do not stop until the task is fully solved.

Rules:
- Rely on your team members (librarian, mle-solver) to do tasks related to search and coding.
- Team members are stateless — provide them with fully detailed context.
- Do everything step by step. Wait for results before moving to the next step.
- Call one team member at a time.

You have access to these specialists via the Agent tool:
- librarian — searches papers, web, databases. Use for any information retrieval.
- mle-solver — writes and runs code. Use for computation, data processing, analysis.
"""

# ── Librarian agent definition ──
LIBRARIAN_PROMPT = (PROJECT_DIR / ".claude" / "agents" / "librarian.md").read_text()
# Strip frontmatter
if LIBRARIAN_PROMPT.startswith("---"):
    end = LIBRARIAN_PROMPT.index("---", 3)
    LIBRARIAN_PROMPT = LIBRARIAN_PROMPT[end + 3:].strip()

# ── MLE Solver agent definition ──
MLE_SOLVER_PROMPT = (PROJECT_DIR / ".claude" / "agents" / "mle-solver.md").read_text()
if MLE_SOLVER_PROMPT.startswith("---"):
    end = MLE_SOLVER_PROMPT.index("---", 3)
    MLE_SOLVER_PROMPT = MLE_SOLVER_PROMPT[end + 3:].strip()

# ── GAIA question prompt ──
QUESTION_TEMPLATE = """You have one question to answer. It is paramount that you provide a correct answer.
Give it all you can: I know for a fact that you have access to all the relevant tools to solve it
and find the exact correct answer (the answer does exist).
Failure or 'I cannot answer' will not be tolerated, success will be rewarded.
Run verification steps if needed, you must make sure you find the correct answer!

Here is the question:
===
{question}
===

Return only the answer after 'Final answer:'.
Don't provide any explanations after the 'Final answer:' line.
Answer the given question exactly as requested.
For instance if asked "how many thousand hours..." and the answer is 2000 hours, return "2" not "2000".
Don't include measurement units (like m³ or Å or %) in a numerical final answer."""


# ── GAIA scoring (ported from run_gaia.py) ──
def normalize_number(s: str) -> float:
    for c in ["$", "%", ","]:
        s = s.replace(c, "")
    try:
        return float(s)
    except ValueError:
        return float("inf")


def normalize_str(s: str, remove_punct: bool = True) -> str:
    s = re.sub(r"\s", "", s)
    if remove_punct:
        return s.lower().translate(str.maketrans("", "", string.punctuation))
    return s.lower()


def score_answer(predicted: str | None, ground_truth: str) -> bool:
    if predicted is None:
        predicted = "None"

    def is_float(x):
        try:
            float(x)
            return True
        except ValueError:
            return False

    if is_float(ground_truth):
        return normalize_number(predicted) == float(ground_truth)
    elif any(c in ground_truth for c in [",", ";"]):
        gt_parts = re.split(r"[,;]", ground_truth)
        pred_parts = re.split(r"[,;]", predicted)
        if len(gt_parts) != len(pred_parts):
            return False
        return all(
            normalize_number(p) == float(g) if is_float(g)
            else normalize_str(p, False) == normalize_str(g, False)
            for p, g in zip(pred_parts, gt_parts)
        )
    else:
        return normalize_str(predicted) == normalize_str(ground_truth)


def extract_final_answer(text: str) -> str:
    if "Final answer:**" in text:
        return text.split("Final answer:**")[-1].strip()
    if "Final answer:" in text:
        return text.split("Final answer:")[-1].strip()
    return text.strip()


# ── Agent SDK runner ──
async def run_question(question: str, idx: int, eval_dir: Path) -> dict:
    """Run one GAIA question as a full manager session with sub-agents."""
    from claude_agent_sdk import query, ClaudeAgentOptions
    from claude_agent_sdk.types import AgentDefinition

    # Each question gets its own workspace subdirectory
    q_dir = eval_dir / f"q{idx:03d}"
    q_dir.mkdir(parents=True, exist_ok=True)

    # Langfuse tracing
    from langfuse_tracer import LangfuseTracer
    session_id = f"eval_gaia_{eval_dir.name}"
    tracer = LangfuseTracer(
        session_id=session_id,
        trace_name=f"gaia:q{idx}",
    )
    tracer.update_trace(input={"question": question[:500], "idx": idx})

    prompt = QUESTION_TEMPLATE.format(question=question)
    start = time.time()
    result_text = ""
    trajectory = []
    token_usage = {}
    meta = {}

    try:
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                system_prompt=MANAGER_PROMPT,
                cwd=str(q_dir.resolve()),
                model="opus",
                effort="high",
                max_turns=80,
                permission_mode="bypassPermissions",
                setting_sources=["project"],
                hooks=tracer.get_hooks(),
                agents={
                    "librarian": AgentDefinition(
                        description="Searches and analyzes papers, web, databases. Delegate any information retrieval.",
                        prompt=LIBRARIAN_PROMPT,
                        maxTurns=50,
                    ),
                    "mle-solver": AgentDefinition(
                        description="Writes and runs code for computation, data processing, analysis.",
                        prompt=MLE_SOLVER_PROMPT,
                        maxTurns=30,
                    ),
                },
            ),
        ):
            tracer.handle_message(message)
            msg_type = type(message).__name__

            if msg_type == "ResultMessage":
                result_text = message.result or ""
                token_usage = message.model_usage or {}
                meta = {
                    "num_turns": message.num_turns,
                    "duration_ms": message.duration_ms,
                    "duration_api_ms": message.duration_api_ms,
                    "total_cost_usd": message.total_cost_usd,
                    "stop_reason": message.stop_reason,
                    "session_id": message.session_id,
                }

            elif msg_type == "AssistantMessage":
                for block in (message.content or []):
                    block_type = type(block).__name__
                    if block_type == "ToolUseBlock":
                        entry = {"tool": block.name}
                        if block.name == "Agent":
                            entry["agent"] = block.input.get("subagent_type", block.input.get("description", "?"))
                            entry["prompt_preview"] = str(block.input.get("prompt", ""))[:150]
                        else:
                            entry["args_preview"] = str(block.input)[:150]
                        trajectory.append(entry)

    except Exception as e:
        result_text = f"ERROR: {e}"

    elapsed = time.time() - start
    tracer.end_trace(
        output={"answer": extract_final_answer(result_text)},
        metadata={"elapsed_s": round(elapsed, 1), "idx": idx},
    )

    # Sweep stray PDFs from workdir root into q_dir
    workdir = PROJECT_DIR / "workdir"
    for pdf in workdir.glob("*.pdf"):
        try:
            pdf.rename(q_dir / pdf.name)
        except OSError:
            pass

    final_answer = extract_final_answer(result_text)

    # Summarize trajectory
    tool_counts = {}
    agents_used = set()
    for t in trajectory:
        tool_counts[t["tool"]] = tool_counts.get(t["tool"], 0) + 1
        if t.get("agent"):
            agents_used.add(t["agent"])

    result = {
        "idx": idx,
        "question": question[:200],
        "answer": final_answer,
        "elapsed_s": round(elapsed, 1),
        "agents_used": sorted(agents_used),
        "tool_counts": tool_counts,
        "token_usage": token_usage,
        "meta": meta,
        "trajectory": trajectory,
        "response_full": result_text,
    }

    with open(q_dir / "result.json", "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    agents_str = ",".join(sorted(agents_used)) or "none"
    print(f"  [Q{idx}] {elapsed:.0f}s | agents={agents_str} | tools={sum(tool_counts.values())} | {final_answer[:50]}", flush=True)
    return result


async def run_eval(
    questions: list[dict],
    eval_dir: Path,
    max_parallel: int = 6,
):
    semaphore = asyncio.Semaphore(max_parallel)
    completed = {"count": 0}
    total = len(questions)

    async def bounded(q: dict) -> dict:
        async with semaphore:
            result = await run_question(q["question"], q["idx"], eval_dir)
            completed["count"] += 1
            # Write progress file
            with open(eval_dir / "progress.txt", "w") as f:
                f.write(f"{completed['count']}/{total} completed\n")
                f.write(f"Last: Q{q['idx']} in {result['elapsed_s']}s\n")
            return result

    print(f"Running {total} questions, max_parallel={max_parallel}", flush=True)
    print(f"Progress: {eval_dir}/progress.txt", flush=True)
    print(f"Results:  {eval_dir}/q{{idx}}/result.json", flush=True)
    print(flush=True)
    tasks = [bounded(q) for q in questions]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Handle exceptions gracefully
    clean_results = []
    for q, r in zip(questions, results):
        if isinstance(r, Exception):
            print(f"  [Q{q['idx']}] EXCEPTION: {r}")
            clean_results.append({
                "idx": q["idx"],
                "question": q["question"][:200],
                "response": f"ERROR: {r}",
                "elapsed_s": 0,
            })
        else:
            clean_results.append(r)

    return sorted(clean_results, key=lambda r: r["idx"])


def score_and_report(results: list[dict], questions: list[dict], eval_dir: Path):
    q_by_idx = {q["idx"]: q for q in questions}
    correct = 0
    total = len(results)
    lines = [
        f"# GAIA Eval Report",
        f"Date: {datetime.now().isoformat()}",
        f"Questions: {total}",
        "",
    ]

    total_tokens_in = 0
    total_tokens_out = 0
    all_agents = set()

    for r in results:
        q = q_by_idx.get(r["idx"], {})
        gt = q.get("answer", "")
        predicted = r.get("answer", "") or extract_final_answer(r.get("response_full", r.get("response", "")))
        is_correct = score_answer(predicted, gt)
        correct += int(is_correct)
        status = "PASS" if is_correct else "FAIL"
        level = q.get("level", "?")
        agents = r.get("agents_used", [])
        tools_n = sum(r.get("tool_counts", {}).values())
        usage = r.get("token_usage", {})
        tok_in = usage.get("input_tokens", 0)
        tok_out = usage.get("output_tokens", 0)
        total_tokens_in += tok_in
        total_tokens_out += tok_out
        all_agents.update(agents)

        lines.append(f"**Q{r['idx']}** L{level} [{status}] ({r['elapsed_s']}s) agents={','.join(agents) or 'none'} tools={tools_n}")
        lines.append(f"  True: {gt}")
        lines.append(f"  Predicted: {predicted[:200]}")
        lines.append("")

    accuracy = correct / total * 100 if total else 0
    total_elapsed = sum(r.get("elapsed_s", 0) for r in results)
    summary = f"""
## Summary

| Metric | Value |
|--------|-------|
| Accuracy | {correct}/{total} = {accuracy:.1f}% |
| Total time | {total_elapsed:.0f}s ({total_elapsed/60:.1f}min) |
| Input tokens | {total_tokens_in:,} |
| Output tokens | {total_tokens_out:,} |
| Agents used | {', '.join(sorted(all_agents)) or 'none'} |
"""
    lines.insert(3, summary)

    report_path = eval_dir / "report.md"
    with open(report_path, "w") as f:
        f.write("\n".join(lines))

    with open(eval_dir / "results.jsonl", "w") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n{'='*60}")
    print(f"  GAIA EVAL: {correct}/{total} = {accuracy:.1f}%")
    print(f"  Report: {report_path}")
    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, help="Path to GAIA questions JSON")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--nrows", type=int, default=None)
    parser.add_argument("--max-parallel", type=int, default=6)
    args = parser.parse_args()

    with open(args.data) as f:
        questions = json.load(f)

    questions = questions[args.start:]
    if args.nrows:
        questions = questions[:args.nrows]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    eval_dir = WORKDIR / f"eval_gaia_{timestamp}"
    eval_dir.mkdir(parents=True, exist_ok=True)

    print(f"GAIA eval: {len(questions)} questions → {eval_dir}")
    results = asyncio.run(run_eval(questions, eval_dir, args.max_parallel))
    score_and_report(results, questions, eval_dir)


if __name__ == "__main__":
    main()
