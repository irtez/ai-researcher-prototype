"""The paid smoke must be bounded before it is allowed to reach the SDK."""

import asyncio
import sys
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk.types import AgentDefinition

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from smoke_test_cc_local import SmokeToolGate, limit_options


def test_limits_do_not_mutate_normal_research_options():
    solver = AgentDefinition(description="solver", prompt="original", maxTurns=100)
    original = ClaudeAgentOptions(
        model="opus",
        effort="max",
        max_turns=300,
        agents={"mle-solver": solver, "writer": solver},
        mcp_servers={
            "mle_kit": {"type": "http", "url": "http://localhost:5057/mcp"},
            "academia": {},
        },
    )
    limited = limit_options(original, SmokeToolGate("test-session"))
    assert limited.model == "sonnet"
    assert limited.max_turns <= 4
    assert limited.effort == "low"
    assert set(limited.agents) == {"mle-solver"}
    assert limited.agents["mle-solver"].maxTurns <= 4
    assert limited.agents["mle-solver"].tools == ["mcp__mle_kit__bash"]
    assert set(limited.mcp_servers) == {"mle_kit"}
    assert "Bash" not in limited.tools
    assert original.model == "opus" and original.max_turns == 300
    assert original.agents["mle-solver"].maxTurns == 100
    assert set(original.mcp_servers) == {"mle_kit", "academia"}


def invoke(gate, name, inputs=None):
    return asyncio.run(
        gate(
            {
                "tool_name": name,
                "tool_input": inputs or {},
                "agent_id": "solver-1",
                "agent_type": "mle-solver",
            },
            None,
            None,
        )
    )


def decision(output):
    return output["hookSpecificOutput"]["permissionDecision"]


def test_only_one_foreground_local_delegation():
    gate = SmokeToolGate("test-session")
    assert decision(invoke(gate, "Agent", {"subagent_type": "writer"})) == "deny"
    allowed = invoke(
        gate,
        "Agent",
        {"subagent_type": "mle-solver", "run_in_background": True, "model": "opus"},
    )
    assert decision(allowed) == "allow"
    assert allowed["hookSpecificOutput"]["updatedInput"]["model"] == "sonnet"
    assert allowed["hookSpecificOutput"]["updatedInput"]["run_in_background"] is False
    assert decision(invoke(gate, "Agent", {"subagent_type": "mle-solver"})) == "deny"


def test_shell_is_local_single_call_and_uses_smoke_workspace():
    gate = SmokeToolGate("test-session")
    manager_call = asyncio.run(
        gate({"tool_name": "mcp__mle_kit__bash", "tool_input": {}}, None, None)
    )
    assert decision(manager_call) == "deny"
    for name in (
        "Bash",
        "WebSearch",
        "mcp__mle_kit__remote_bash",
        "mcp__mle_kit__llm_proxy_local",
    ):
        assert decision(invoke(gate, name)) == "deny"
    allowed = invoke(
        gate,
        "mcp__mle_kit__bash",
        {
            "command": "true",
            "timeout": 9999,
            "cwd": "../../",
            "session_id": "another-run",
        },
    )
    args = allowed["hookSpecificOutput"]["updatedInput"]
    assert decision(allowed) == "allow"
    assert args["session_id"] == "test-session"
    assert args["timeout"] <= 20 and args["cwd"] is None
    assert decision(invoke(gate, "mcp__mle_kit__bash")) == "deny"
