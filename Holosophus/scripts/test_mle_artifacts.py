#!/usr/bin/env python3
"""Integration test: verify mle-solver creates structured artifacts.

Runs mle-solver with a simple task and checks that the artifact structure
(mle/code/, mle/results/, mle/log.jsonl) is created in the workdir.

Requires MCP services running (make up-mcp).

Usage:
    uv run python scripts/test_mle_artifacts.py
    uv run python scripts/test_mle_artifacts.py --remote   # test mle-solver-remote
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

TASK = """\
Write a Python script that computes the first 20 Fibonacci numbers and prints them.
Run the script and report the output.

Session ID: {session_id}

IMPORTANT — follow these archival instructions exactly:
- Create `mle/` directory at start. Save all experiment code to `mle/code/` after each major change.
- Save experiment results/metrics to `mle/results/fibonacci.json` — include the list of numbers and wall time.
- Maintain `mle/log.jsonl` — append one JSON line per phase:
  {{"ts": "<ISO timestamp>", "phase": "<name>", "status": "done|failed", "summary": "<1 line>", "artifacts": ["mle/code/fib.py"], "metrics": {{}}}}
- Use Claude Code native tools (Write/Edit) for local saves — these write to workdir, not the sandbox.
- Mention paths to `mle/log.jsonl` and key result files in your final message.
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
    return {
        "description": meta.get("description", name),
        "prompt": text,
    }


def load_mcp_servers() -> dict:
    data = json.loads(MCP_JSON.read_text())
    return data.get("mcpServers", {})


def check_artifacts(workdir: Path) -> dict[str, bool]:
    """Check which expected artifacts exist."""
    checks = {
        "mle/ dir": (workdir / "mle").is_dir(),
        "mle/code/ dir": (workdir / "mle" / "code").is_dir(),
        "mle/code/ has files": any((workdir / "mle" / "code").glob("*")) if (workdir / "mle" / "code").is_dir() else False,
        "mle/results/ dir": (workdir / "mle" / "results").is_dir(),
        "mle/results/ has files": any((workdir / "mle" / "results").glob("*")) if (workdir / "mle" / "results").is_dir() else False,
        "mle/log.jsonl exists": (workdir / "mle" / "log.jsonl").is_file(),
        "mle/log.jsonl has entries": False,
    }

    log_path = workdir / "mle" / "log.jsonl"
    if log_path.is_file():
        lines = [l for l in log_path.read_text().strip().split("\n") if l.strip()]
        checks["mle/log.jsonl has entries"] = len(lines) > 0
        if lines:
            try:
                entry = json.loads(lines[0])
                checks["log entry has 'phase'"] = "phase" in entry
                checks["log entry has 'status'"] = "status" in entry
            except json.JSONDecodeError:
                checks["log entry valid JSON"] = False

    return checks


async def main():
    parser = argparse.ArgumentParser(description="MLE artifact integration test")
    parser.add_argument("--remote", action="store_true",
                        help="Test mle-solver-remote instead of mle-solver")
    args = parser.parse_args()

    agent_name = "mle-solver-remote" if args.remote else "mle-solver"

    # ── Langfuse ──
    os.environ["LANGFUSE_PUBLIC_KEY"] = cfg.langfuse_public_key_v3
    os.environ["LANGFUSE_SECRET_KEY"] = cfg.langfuse_secret_key_v3
    os.environ["LANGFUSE_BASE_URL"] = cfg.langfuse_base_url_v3
    os.environ["LANGSMITH_OTEL_ENABLED"] = "true"
    os.environ["LANGSMITH_OTEL_ONLY"] = "true"
    os.environ["LANGSMITH_TRACING"] = "true"

    from langfuse import get_client, propagate_attributes
    langfuse = get_client()
    if not langfuse.auth_check():
        print("WARNING: Langfuse auth failed.", flush=True)

    sys.path.insert(0, str(PROJECT_DIR / "scripts"))
    import langsmith_fork
    sys.modules["langsmith.integrations.claude_agent_sdk"] = langsmith_fork
    langsmith_fork.configure_claude_agent_sdk(tags=["test", "mle-artifacts"])

    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
    from claude_agent_sdk.types import AgentDefinition

    # ── Session ──
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_name = f"test_mle_artifacts_{timestamp}"
    workdir = PROJECT_DIR / "workdir" / session_name
    workdir.mkdir(parents=True, exist_ok=True)

    # ── Load agent ──
    a = load_agent(agent_name)
    agents = {agent_name: AgentDefinition(description=a["description"], prompt=a["prompt"])}
    mcp_servers = load_mcp_servers()
    task = TASK.format(session_id=session_name)

    system_prompt = (
        f"You are a research assistant. Use the {agent_name} agent to complete the task. "
        f"Save all results to the workdir."
    )

    print(f"Agent:    {agent_name}", flush=True)
    print(f"Session:  {session_name}", flush=True)
    print(f"Workdir:  {workdir}", flush=True)
    print(f"Langfuse: {cfg.langfuse_base_url_v3}", flush=True)
    print(flush=True)

    start = time.time()

    with propagate_attributes(session_id=session_name):
        async with ClaudeSDKClient(options=ClaudeAgentOptions(
            system_prompt=system_prompt,
            cwd=str(workdir),
            model="sonnet",
            max_turns=30,
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
                        bt = type(b).__name__
                        if bt == "ToolUseBlock":
                            agent = b.input.get("subagent_type", "") if isinstance(b.input, dict) else ""
                            print(f"  [mgr] -> {b.name} \"{agent[:80]}\"", flush=True)

                elif mt == "TaskNotificationMessage":
                    status = getattr(msg, "status", "?")
                    summary = (getattr(msg, "summary", "") or "")[:120]
                    print(f"  [{status}] {summary}", flush=True)

                elif mt == "ResultMessage":
                    cost = getattr(msg, "total_cost_usd", None)
                    turns = getattr(msg, "num_turns", 0)
                    dur = time.time() - start
                    cost_str = f"${cost:.2f}" if cost else "?"
                    print(f"\n{'='*60}", flush=True)
                    print(f"  Done: {turns} turns, {dur:.0f}s, {cost_str}", flush=True)
                    print(f"{'='*60}\n", flush=True)

    langfuse.flush()

    # ── Verify artifacts ──
    print("Artifact checks:", flush=True)
    checks = check_artifacts(workdir)
    all_pass = True
    for name, passed in checks.items():
        icon = "PASS" if passed else "FAIL"
        print(f"  [{icon}] {name}", flush=True)
        if not passed:
            all_pass = False

    # Show what was actually created
    print(f"\nWorkdir contents:", flush=True)
    for p in sorted(workdir.rglob("*")):
        if p.is_file():
            size = p.stat().st_size
            print(f"  {p.relative_to(workdir)} ({size}B)", flush=True)

    print(flush=True)
    if all_pass:
        print("RESULT: ALL CHECKS PASSED", flush=True)
    else:
        print("RESULT: SOME CHECKS FAILED", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
