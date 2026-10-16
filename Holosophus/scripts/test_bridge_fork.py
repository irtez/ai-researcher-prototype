#!/usr/bin/env python3
"""Test the forked langsmith bridge with per-agent isolated history.

Uses the forked bridge from scripts/langsmith_fork/ instead of the
installed langsmith package. Everything else identical to run_research.py.

Usage:
    uv run python scripts/test_bridge_fork.py
    uv run python scripts/test_bridge_fork.py "Your question"
"""

import asyncio
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")

# Patch langsmith integration BEFORE importing claude_agent_sdk
# Replace the installed module with our fork
sys.path.insert(0, str(PROJECT_DIR / "scripts"))
import langsmith_fork
sys.modules["langsmith.integrations.claude_agent_sdk"] = langsmith_fork


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

DEFAULT_QUESTION = (
    "Find paper 2307.11760 about EmotionPrompt and summarize it. "
    "Then propose one follow-up research idea."
)

MANAGER_PROMPT = """You are a research manager. You have two agents:
- librarian: finds and summarizes papers
- proposer: generates research ideas

For this task:
1. Call librarian to find and summarize the requested paper
2. Call proposer with the summary to generate one research idea
3. Return the final proposal

Be concise. This is a tracing test."""


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
    import json
    data = json.loads(MCP_JSON.read_text())
    return data.get("mcpServers", {})


async def main():
    question = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else DEFAULT_QUESTION

    # Set up Langfuse v3 via langsmith OTEL bridge
    os.environ["LANGFUSE_PUBLIC_KEY"] = cfg.langfuse_public_key_v3
    os.environ["LANGFUSE_SECRET_KEY"] = cfg.langfuse_secret_key_v3
    os.environ["LANGFUSE_BASE_URL"] = cfg.langfuse_base_url_v3
    os.environ["LANGSMITH_OTEL_ENABLED"] = "true"
    os.environ["LANGSMITH_OTEL_ONLY"] = "true"
    os.environ["LANGSMITH_TRACING"] = "true"

    from langfuse import get_client
    langfuse = get_client()
    try:
        if not langfuse.auth_check():
            print("WARNING: Langfuse v3 auth failed.", flush=True)
    except Exception as e:
        print(f"WARNING: Langfuse unreachable ({e})", flush=True)

    # Apply our forked bridge
    langsmith_fork.configure_claude_agent_sdk()

    from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
    from claude_agent_sdk.types import AgentDefinition

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    session_name = f"bridge_fork_test__{timestamp}"
    workdir = PROJECT_DIR / "workdir" / session_name
    workdir.mkdir(parents=True, exist_ok=True)

    agents = {}
    for name in ["librarian", "proposer"]:
        a = load_agent(name)
        agents[name] = AgentDefinition(description=a["description"], prompt=a["prompt"])

    mcp_servers = load_mcp_servers()

    print(f"Session:  {session_name}", flush=True)
    print(f"Workdir:  {workdir}", flush=True)
    print(f"Agents:   {', '.join(agents.keys())}", flush=True)
    print(f"Langfuse: {cfg.langfuse_base_url_v3} (forked bridge)", flush=True)
    print(f"Question: {question[:120]}", flush=True)
    print(flush=True)

    start = time.time()

    async with ClaudeSDKClient(options=ClaudeAgentOptions(
        system_prompt=MANAGER_PROMPT,
        cwd=str(workdir),
        model="sonnet",
        max_turns=15,
        permission_mode="bypassPermissions",
        mcp_servers=mcp_servers,
        agents=agents,
    )) as client:
        await client.query(question, session_id=session_name)

        async for msg in client.receive_response():
            mt = type(msg).__name__
            ptui = getattr(msg, "parent_tool_use_id", None)
            elapsed = time.time() - start

            if mt == "AssistantMessage" and not ptui:
                for b in (msg.content or []):
                    bt = type(b).__name__
                    if bt == "TextBlock" and b.text:
                        print(f"  [{elapsed:.0f}s] [mgr] {b.text[:150]}", flush=True)
                    elif bt == "ToolUseBlock":
                        desc = b.input.get("description", "") if isinstance(b.input, dict) else ""
                        agent = b.input.get("subagent_type", "") if isinstance(b.input, dict) else ""
                        label = agent or desc
                        print(f"  [{elapsed:.0f}s] [mgr] -> {b.name} \"{label[:80]}\"", flush=True)

            elif mt == "TaskNotificationMessage":
                status = getattr(msg, "status", "?")
                summary = (getattr(msg, "summary", "") or "")[:120]
                print(f"  [{elapsed:.0f}s] [{status}] {summary}", flush=True)

            elif mt == "ResultMessage":
                cost = getattr(msg, "total_cost_usd", None)
                turns = getattr(msg, "num_turns", 0)
                dur = time.time() - start
                result = getattr(msg, "result", "") or ""
                cost_str = f"${cost:.2f}" if cost else "?"
                print(f"\n  Done: {turns} turns, {dur:.0f}s, {cost_str}", flush=True)
                print(result[:2000], flush=True)

    langfuse.flush()
    time.sleep(5)
    print(f"\nTraces: {cfg.langfuse_base_url_v3}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
