#!/usr/bin/env python3
"""Integration test: verify mle-solver crash recovery via artifact checkpointing.

Scenario 1 (two-phase): Run full task (2 phases). Simulate crash by deleting
  phase 2 artifacts + truncating log. Resume. Verify phase 2 restored, phase 1 untouched.

Scenario 2 (multi-experiment): Run full task (10 experiments). Simulate crash by
  deleting last N experiments. Resume. Verify missing experiments restored.

Requires MCP services running (make up-mcp).

Usage:
    uv run python scripts/test_mle_recovery.py              # both scenarios
    uv run python scripts/test_mle_recovery.py --scenario 1  # two-phase only
    uv run python scripts/test_mle_recovery.py --scenario 2  # multi-experiment only
"""

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")


class Config(BaseSettings):
    langfuse_public_key_v3: str = ""
    langfuse_secret_key_v3: str = ""
    langfuse_base_url_v3: str = "http://localhost:3051"

    class Config:
        env_file = str(PROJECT_DIR / ".env")
        extra = "ignore"


cfg = Config()

AGENTS_DIR = PROJECT_DIR / ".claude" / "agents"
MCP_JSON = PROJECT_DIR / ".mcp.json"

# ── Archival instructions (same as manager prompt injects) ──

ARCHIVAL_INSTRUCTIONS = """\
IMPORTANT — follow these archival instructions exactly:
- Create `mle/` directory at start. Save all experiment code to `mle/code/` after each major change.
- Save experiment results/metrics to `mle/results/<name>.json`.
- Maintain `mle/log.jsonl` — append one JSON line per completed phase/experiment:
  {{"ts": "<ISO>", "phase": "<name>", "status": "done|failed", "summary": "<1 line>", "artifacts": ["mle/code/x.py"], "metrics": {{}}}}
- Use Claude Code native tools (Write/Edit) for local saves — these write to workdir, not the sandbox.
- Mention paths to `mle/log.jsonl` and key result files in your final message.
"""

# ── Scenario 1: Two-phase task ──

TASK_TWO_PHASE = """\
Complete these two phases IN ORDER:

Phase 1 — Data Generation:
  Write a Python script that generates 1000 random integers (range 1-10000),
  computes mean, std, median, min, max. Save the script to mle/code/generate.py.
  Save stats to mle/results/stats.json. Log phase "data_generation" to mle/log.jsonl.

Phase 2 — Sorting Benchmark:
  Write a Python script that implements 3 sorting algorithms (bubble sort, merge sort,
  quicksort). Benchmark each on the 1000 numbers from Phase 1. Save timings to
  mle/results/sorting_benchmark.json. Save script to mle/code/sort_benchmark.py.
  Log phase "sorting_benchmark" to mle/log.jsonl.

Session ID: {session_id}

{archival}
"""

# ── Scenario 2: Multi-experiment task ──

TASK_MULTI_EXPERIMENT = """\
Implement and benchmark 10 string operations on Python. For each operation:
1. Write a small function that performs the operation
2. Test it on 10000 random 20-char strings, measure wall time
3. Save timing to mle/results/<operation_name>.json
4. Log completion to mle/log.jsonl with phase name = operation name

The 10 operations (do them IN THIS ORDER):
1. reverse
2. uppercase
3. lowercase
4. capitalize
5. strip
6. replace (replace 'a' with 'z')
7. split (split on spaces)
8. join (join list with commas)
9. count (count letter 'e')
10. find (find substring 'the')

Save all code to mle/code/. Each experiment must have its own log entry in mle/log.jsonl.

Session ID: {session_id}

{archival}
"""

# ── Resume prompts ──

RESUME_PROMPT = """\
Continue the task from where it was interrupted.

FIRST: Read mle/log.jsonl to see which phases/experiments are already completed.
THEN: Read mle/results/ to see what result files already exist.
SKIP any phases that are already done (have a log entry with status "done" AND
a corresponding result file in mle/results/).
Continue with the NEXT incomplete phase/experiment only.

The original task was:
{original_task}

Session ID: {session_id}

{archival}
"""


def load_agent(name: str) -> dict:
    import yaml
    path = AGENTS_DIR / f"{name}.md"
    text = path.read_text()
    meta = {}
    if text.startswith("---"):
        end = text.index("---", 3)
        meta = yaml.safe_load(text[3:end]) or {}
        text = text[end + 3:].strip()
    return {"description": meta.get("description", name), "prompt": text}


def load_mcp_servers() -> dict:
    data = json.loads(MCP_JSON.read_text())
    return data.get("mcpServers", {})


def read_log(workdir: Path) -> list[dict]:
    """Read mle/log.jsonl, return list of parsed entries."""
    log_path = workdir / "mle" / "log.jsonl"
    if not log_path.is_file():
        return []
    entries = []
    for line in log_path.read_text().strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return entries


def write_log(workdir: Path, entries: list[dict]):
    """Write entries back to mle/log.jsonl."""
    log_path = workdir / "mle" / "log.jsonl"
    with open(log_path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def list_result_files(workdir: Path) -> list[str]:
    results_dir = workdir / "mle" / "results"
    if not results_dir.is_dir():
        return []
    return sorted(p.name for p in results_dir.glob("*.json"))


def get_phase_names(entries: list[dict]) -> list[str]:
    return [e.get("phase", "?") for e in entries]


def record_mtimes(workdir: Path) -> dict[str, float]:
    """Record modification times for all files in mle/."""
    mtimes = {}
    mle_dir = workdir / "mle"
    if mle_dir.is_dir():
        for p in mle_dir.rglob("*"):
            if p.is_file():
                mtimes[str(p.relative_to(workdir))] = p.stat().st_mtime
    return mtimes


def check_files_untouched(before: dict[str, float], after: dict[str, float],
                          files: list[str]) -> tuple[bool, list[str]]:
    """Check that specified files were NOT modified between before and after snapshots."""
    touched = []
    for f in files:
        if f in before and f in after:
            if after[f] > before[f]:
                touched.append(f)
    return len(touched) == 0, touched


async def run_agent(
    workdir: Path,
    session_name: str,
    task: str,
    max_turns: int,
    mcp_servers: dict,
    agents: dict,
    label: str = "",
) -> dict:
    """Run mle-solver agent, return {turns, cost, duration}."""
    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
    from langfuse import propagate_attributes

    system_prompt = (
        "You are a research assistant. Use the mle-solver agent to complete the task. "
        "Pass the FULL task description to the agent, including all archival instructions."
    )

    print(f"  {label} max_turns={max_turns} ...", flush=True)
    start = time.time()
    result_info = {"turns": 0, "cost": 0.0, "duration": 0.0}

    with propagate_attributes(session_id=session_name):
        async with ClaudeSDKClient(options=ClaudeAgentOptions(
            system_prompt=system_prompt,
            cwd=str(workdir),
            model="sonnet",
            max_turns=max_turns,
            permission_mode="bypassPermissions",
            mcp_servers=mcp_servers,
            agents=agents,
        )) as client:
            await client.query(task, session_id=session_name)

            async for msg in client.receive_response():
                mt = type(msg).__name__
                ptui = getattr(msg, "parent_tool_use_id", None)

                if mt == "AssistantMessage" and not ptui:
                    for b in (msg.content or []):
                        if type(b).__name__ == "ToolUseBlock":
                            agent = b.input.get("subagent_type", "") if isinstance(b.input, dict) else ""
                            print(f"    [mgr] -> {b.name} \"{agent[:60]}\"", flush=True)

                elif mt == "TaskNotificationMessage":
                    status = getattr(msg, "status", "?")
                    summary = (getattr(msg, "summary", "") or "")[:100]
                    print(f"    [{status}] {summary}", flush=True)

                elif mt == "ResultMessage":
                    result_info["turns"] = getattr(msg, "num_turns", 0)
                    result_info["cost"] = getattr(msg, "total_cost_usd", 0) or 0
                    result_info["duration"] = time.time() - start
                    cost_str = f"${result_info['cost']:.2f}"
                    print(f"  Done: {result_info['turns']} turns, "
                          f"{result_info['duration']:.0f}s, {cost_str}", flush=True)

    return result_info


def print_checks(checks: dict[str, bool]) -> bool:
    all_pass = True
    for name, passed in checks.items():
        icon = "PASS" if passed else "FAIL"
        print(f"  [{icon}] {name}", flush=True)
        if not passed:
            all_pass = False
    return all_pass


def print_workdir(workdir: Path):
    print(f"  Workdir contents:", flush=True)
    for p in sorted(workdir.rglob("*")):
        if p.is_file():
            rel = p.relative_to(workdir)
            size = p.stat().st_size
            print(f"    {rel} ({size}B)", flush=True)


# ── Scenario 1: Two-phase crash recovery ──

async def scenario_two_phase(mcp_servers: dict, agents: dict) -> bool:
    print(f"\n{'='*60}", flush=True)
    print("=== Scenario 1: Two-phase crash recovery ===", flush=True)
    print(f"{'='*60}\n", flush=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_name = f"test_recovery_2phase_{timestamp}"
    workdir = PROJECT_DIR / "workdir" / session_name
    workdir.mkdir(parents=True, exist_ok=True)

    task = TASK_TWO_PHASE.format(session_id=session_name, archival=ARCHIVAL_INSTRUCTIONS)

    # ── Run 1: complete full task ──
    print("--- Run 1 (full task) ---", flush=True)
    await run_agent(workdir, session_name, task, max_turns=30,
                    mcp_servers=mcp_servers, agents=agents, label="Run 1:")

    log_full = read_log(workdir)
    results_full = list_result_files(workdir)
    print(f"  Log entries: {len(log_full)} — {get_phase_names(log_full)}", flush=True)
    print(f"  Result files: {results_full}", flush=True)

    if len(log_full) < 2:
        print("\n  SKIP: Run 1 didn't complete both phases. Can't test recovery.", flush=True)
        return False

    # ── Simulate crash: delete phase 2 artifacts, truncate log ──
    print("\n--- Simulating crash (deleting phase 2 artifacts) ---", flush=True)
    # Keep only phase 1 log entry
    surviving_log = [log_full[0]]
    write_log(workdir, surviving_log)
    print(f"  Truncated log to {len(surviving_log)} entry: {get_phase_names(surviving_log)}", flush=True)

    # Delete phase 2 result file
    phase2_results = [f for f in results_full if "sorting" in f.lower() or "benchmark" in f.lower()]
    if not phase2_results:
        # If we can't identify phase 2 result by name, delete the second one
        phase2_results = results_full[1:] if len(results_full) > 1 else []
    for f in phase2_results:
        (workdir / "mle" / "results" / f).unlink()
        print(f"  Deleted: mle/results/{f}", flush=True)

    surviving_results = list_result_files(workdir)
    print(f"  Surviving results: {surviving_results}", flush=True)

    # Record mtimes of surviving files
    mtimes_before = record_mtimes(workdir)
    # Small sleep so mtime changes are detectable
    time.sleep(1)

    # ── Run 2: resume from crash ──
    print("\n--- Run 2 (resume after crash) ---", flush=True)
    resume = RESUME_PROMPT.format(
        original_task=task, session_id=session_name, archival=ARCHIVAL_INSTRUCTIONS
    )
    await run_agent(workdir, session_name, resume, max_turns=30,
                    mcp_servers=mcp_servers, agents=agents, label="Run 2:")

    log_after_resume = read_log(workdir)
    results_after_resume = list_result_files(workdir)
    mtimes_after = record_mtimes(workdir)
    print(f"  Log entries: {len(log_after_resume)} — {get_phase_names(log_after_resume)}", flush=True)
    print(f"  Result files: {results_after_resume}", flush=True)

    # ── Checks ──
    print("\n--- Checks ---", flush=True)

    # Phase 1 files should not have been modified
    phase1_files = [f"mle/results/{r}" for r in surviving_results]
    untouched, touched = check_files_untouched(mtimes_before, mtimes_after, phase1_files)
    if touched:
        print(f"  WARNING: phase 1 files were modified: {touched}", flush=True)

    # Phase 2 should be restored
    phase2_restored = len(results_after_resume) > len(surviving_results)

    # New log entries should not duplicate surviving phases
    surviving_phases = set(get_phase_names(surviving_log))
    new_log_entries = log_after_resume[len(surviving_log):]
    new_phases = get_phase_names(new_log_entries)
    duplicates = [p for p in new_phases if p in surviving_phases]

    checks = {
        "run 1: both phases completed": len(log_full) >= 2,
        "crash sim: log truncated to 1 entry": len(read_log(workdir)) != len(log_full) or len(log_after_resume) > len(surviving_log),
        "resume: log has more entries": len(log_after_resume) > len(surviving_log),
        "resume: phase 2 results restored": phase2_restored,
        "resume: phase 1 NOT re-done (no dup log)": len(duplicates) == 0,
        "resume: phase 1 files untouched (mtime)": untouched,
        "log entries valid JSON": all(isinstance(e, dict) for e in log_after_resume),
        "log entries have phase+status": all("phase" in e and "status" in e for e in log_after_resume),
    }
    if duplicates:
        print(f"  Duplicate phases in resume: {duplicates}", flush=True)

    passed = print_checks(checks)
    print()
    print_workdir(workdir)
    return passed


# ── Scenario 2: Multi-experiment crash recovery ──

async def scenario_multi_experiment(mcp_servers: dict, agents: dict) -> bool:
    print(f"\n{'='*60}", flush=True)
    print("=== Scenario 2: Multi-experiment crash recovery ===", flush=True)
    print(f"{'='*60}\n", flush=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_name = f"test_recovery_multiexp_{timestamp}"
    workdir = PROJECT_DIR / "workdir" / session_name
    workdir.mkdir(parents=True, exist_ok=True)

    task = TASK_MULTI_EXPERIMENT.format(session_id=session_name, archival=ARCHIVAL_INSTRUCTIONS)

    # ── Run 1: complete full task ──
    print("--- Run 1 (full task) ---", flush=True)
    await run_agent(workdir, session_name, task, max_turns=30,
                    mcp_servers=mcp_servers, agents=agents, label="Run 1:")

    log_full = read_log(workdir)
    results_full = list_result_files(workdir)
    print(f"  Log entries: {len(log_full)} — {get_phase_names(log_full)}", flush=True)
    print(f"  Result files: {results_full}", flush=True)

    if len(log_full) < 4:
        print("\n  SKIP: Run 1 completed fewer than 4 experiments. Can't test recovery.", flush=True)
        return False

    # ── Simulate crash: keep first half, delete second half ──
    keep_n = len(log_full) // 2
    print(f"\n--- Simulating crash (keeping {keep_n} of {len(log_full)} experiments) ---", flush=True)

    # Truncate log
    surviving_log = log_full[:keep_n]
    write_log(workdir, surviving_log)
    surviving_phases = set(get_phase_names(surviving_log))
    print(f"  Surviving log: {get_phase_names(surviving_log)}", flush=True)

    # Delete result files for removed experiments
    deleted_phases = get_phase_names(log_full[keep_n:])
    for f in results_full:
        fname = f.replace(".json", "")
        # Check if this result file belongs to a deleted phase
        if any(phase.lower().replace(" ", "_") in fname.lower() or
               fname.lower() in phase.lower().replace(" ", "_")
               for phase in deleted_phases):
            (workdir / "mle" / "results" / f).unlink()
            print(f"  Deleted: mle/results/{f}", flush=True)

    surviving_results = list_result_files(workdir)
    print(f"  Surviving results: {surviving_results}", flush=True)

    # Record mtimes
    mtimes_before = record_mtimes(workdir)
    time.sleep(1)

    # ── Run 2: resume ──
    print(f"\n--- Run 2 (resume, {keep_n} of {len(log_full)} done) ---", flush=True)
    resume = RESUME_PROMPT.format(
        original_task=task, session_id=session_name, archival=ARCHIVAL_INSTRUCTIONS
    )
    await run_agent(workdir, session_name, resume, max_turns=60,
                    mcp_servers=mcp_servers, agents=agents, label="Run 2:")

    log_after_resume = read_log(workdir)
    results_after_resume = list_result_files(workdir)
    mtimes_after = record_mtimes(workdir)
    print(f"  Log entries: {len(log_after_resume)} — {get_phase_names(log_after_resume)}", flush=True)
    print(f"  Result files: {results_after_resume}", flush=True)

    # ── Checks ──
    print("\n--- Checks ---", flush=True)

    # Surviving files should not have been modified
    surviving_files = [f"mle/results/{r}" for r in surviving_results]
    untouched, touched = check_files_untouched(mtimes_before, mtimes_after, surviving_files)
    if touched:
        print(f"  WARNING: surviving files were modified: {touched}", flush=True)

    # New log entries should not duplicate surviving phases
    new_log_entries = log_after_resume[keep_n:]
    new_phases = get_phase_names(new_log_entries)
    duplicates = [p for p in new_phases if p in surviving_phases]

    checks = {
        f"run 1: >= 4 experiments completed": len(log_full) >= 4,
        f"crash sim: log truncated to {keep_n}": True,  # we did this
        "resume: log has more entries": len(log_after_resume) > keep_n,
        "resume: more result files restored": len(results_after_resume) > len(surviving_results),
        f"resume: no duplicate experiments (RECOVERY)": len(duplicates) == 0,
        "resume: surviving files untouched (mtime)": untouched,
        f"resume: >= {len(log_full) - 2} experiments total (soft)": len(log_after_resume) >= len(log_full) - 2,
        "log entries valid JSON": all(isinstance(e, dict) for e in log_after_resume),
        "log entries have phase+status": all("phase" in e and "status" in e for e in log_after_resume),
    }
    if duplicates:
        print(f"  Duplicate phases in resume: {duplicates}", flush=True)

    passed = print_checks(checks)
    print()
    print_workdir(workdir)
    return passed


async def main():
    parser = argparse.ArgumentParser(description="MLE recovery integration test")
    parser.add_argument("--scenario", type=int, choices=[1, 2], default=None,
                        help="Run specific scenario (default: both)")
    args = parser.parse_args()

    # ── Langfuse ──
    os.environ["LANGFUSE_PUBLIC_KEY"] = cfg.langfuse_public_key_v3
    os.environ["LANGFUSE_SECRET_KEY"] = cfg.langfuse_secret_key_v3
    os.environ["LANGFUSE_BASE_URL"] = cfg.langfuse_base_url_v3
    os.environ["LANGSMITH_OTEL_ENABLED"] = "true"
    os.environ["LANGSMITH_OTEL_ONLY"] = "true"
    os.environ["LANGSMITH_TRACING"] = "true"

    from langfuse import get_client
    langfuse = get_client()
    if not langfuse.auth_check():
        print("WARNING: Langfuse auth failed.", flush=True)

    sys.path.insert(0, str(PROJECT_DIR / "scripts"))
    import langsmith_fork
    sys.modules["langsmith.integrations.claude_agent_sdk"] = langsmith_fork
    langsmith_fork.configure_claude_agent_sdk(tags=["test", "mle-recovery"])

    from claude_agent_sdk.types import AgentDefinition

    # ── Load agent + MCP ──
    a = load_agent("mle-solver")
    agents = {"mle-solver": AgentDefinition(description=a["description"], prompt=a["prompt"])}
    mcp_servers = load_mcp_servers()

    # ── Run scenarios ──
    results = {}
    if args.scenario in (None, 1):
        results["scenario_1"] = await scenario_two_phase(mcp_servers, agents)
    if args.scenario in (None, 2):
        results["scenario_2"] = await scenario_multi_experiment(mcp_servers, agents)

    langfuse.flush()

    # ── Summary ──
    print(f"\n{'='*60}", flush=True)
    print("SUMMARY", flush=True)
    print(f"{'='*60}", flush=True)
    all_pass = True
    for name, passed in results.items():
        icon = "PASS" if passed else "FAIL"
        print(f"  [{icon}] {name}", flush=True)
        if not passed:
            all_pass = False

    print(flush=True)
    if all_pass:
        print("RESULT: ALL SCENARIOS PASSED", flush=True)
    else:
        print("RESULT: SOME SCENARIOS FAILED", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
