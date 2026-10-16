#!/usr/bin/env python3
"""Tracing test: verify Langfuse v3 tracing via langsmith fork bridge.

Basic mode (default): find a paper and summarize it (librarian only).
Extended mode (--extended): also generate a research proposal (librarian + proposer).

Usage:
    uv run python scripts/test_tracing_simple.py
    uv run python scripts/test_tracing_simple.py --extended
"""

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")


# ── Config from .env ──

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

TASK_BASIC = (
    "Find the paper 'Natural-Language Agent Harnesses' on arxiv, "
    "make it's summary and return it's ID."
)

TASK_EXTENDED = (
    "1. First, use the librarian agent to find the paper "
    "'Natural-Language Agent Harnesses' on arxiv and get its ID and summary.\n"
    "2. Then, use the proposer agent to generate one short research proposal "
    "inspired by that paper. The proposal should be 3-5 sentences.\n"
    "Save results to the workdir."
)


def load_agent(name: str) -> dict:
    """Load agent .md, return {description, prompt}."""
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
    """Load MCP servers from .mcp.json."""
    import json
    data = json.loads(MCP_JSON.read_text())
    return data.get("mcpServers", {})


async def main():
    parser = argparse.ArgumentParser(description="Tracing test")
    parser.add_argument("--extended", action="store_true",
                        help="Run extended test with librarian + proposer agents")
    args = parser.parse_args()

    extended = args.extended

    # ── Langfuse v3 + langsmith bridge ──
    os.environ["LANGFUSE_PUBLIC_KEY"] = cfg.langfuse_public_key_v3
    os.environ["LANGFUSE_SECRET_KEY"] = cfg.langfuse_secret_key_v3
    os.environ["LANGFUSE_BASE_URL"] = cfg.langfuse_base_url_v3
    os.environ["LANGSMITH_OTEL_ENABLED"] = "true"
    os.environ["LANGSMITH_OTEL_ONLY"] = "true"
    os.environ["LANGSMITH_TRACING"] = "true"

    from langfuse import get_client, propagate_attributes
    langfuse = get_client()
    if not langfuse.auth_check():
        print("WARNING: Langfuse v3 auth failed. Traces won't appear.", flush=True)

    # Use forked langsmith bridge
    sys.path.insert(0, str(PROJECT_DIR / "scripts"))
    import langsmith_fork
    sys.modules["langsmith.integrations.claude_agent_sdk"] = langsmith_fork
    langsmith_fork.configure_claude_agent_sdk()

    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
    from claude_agent_sdk.types import AgentDefinition

    # ── Session ──
    mode_tag = "extended" if extended else "basic"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_name = f"test_tracing_{mode_tag}_{timestamp}"
    workdir = PROJECT_DIR / "workdir" / session_name
    workdir.mkdir(parents=True, exist_ok=True)

    # ── Load agents ──
    agent_names = ["librarian", "proposer"] if extended else ["librarian"]
    agents = {}
    for name in agent_names:
        a = load_agent(name)
        agents[name] = AgentDefinition(description=a["description"], prompt=a["prompt"])

    mcp_servers = load_mcp_servers()

    task = TASK_EXTENDED if extended else TASK_BASIC
    max_turns = 20 if extended else 8

    system_prompt = (
        "You are a research assistant. "
        + ("Use the librarian agent for search and the proposer agent for ideas. "
           if extended else
           "Use the librarian agent to find and summarize the requested paper. ")
        + f"Save all results to workdir/{session_name}/"
    )

    print(f"Mode:     {'extended (librarian + proposer)' if extended else 'basic (librarian only)'}", flush=True)
    print(f"Session:  {session_name}", flush=True)
    print(f"Workdir:  {workdir}", flush=True)
    print(f"Agents:   {', '.join(agents.keys())}", flush=True)
    print(f"MCP:      {', '.join(mcp_servers.keys())}", flush=True)
    print(f"Langfuse: {cfg.langfuse_base_url_v3}", flush=True)
    print(f"Task:     {task[:120]}", flush=True)
    print(flush=True)

    start = time.time()

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
                    result = getattr(msg, "result", "") or ""

                    cost_str = f"${cost:.2f}" if cost else "?"
                    print(f"\n{'='*60}", flush=True)
                    print(f"  Done: {turns} turns, {dur:.0f}s, {cost_str}", flush=True)
                    print(f"{'='*60}\n", flush=True)
                    print(result[:2000], flush=True)

    langfuse.flush()
    time.sleep(5)
    print(f"\nTraces: {cfg.langfuse_base_url_v3}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
