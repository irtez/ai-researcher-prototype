"""Offline checks of the approved smoke's spend and tool boundaries."""

import asyncio
import sys
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk.types import AgentDefinition

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import smoke_test_cc_chemistry as smoke


def invoke(gate, name, args=None, solver=True, agent_id="solver-1"):
    return asyncio.run(
        gate(
            {
                "tool_name": name,
                "tool_input": args or {},
                **(
                    {"agent_type": "mle-solver", "agent_id": agent_id} if solver else {}
                ),
            },
            None,
            None,
        )
    )["hookSpecificOutput"]


def test_only_one_delegation_and_no_extra_models(tmp_path):
    gate = smoke.ChemistrySmokeGate("session", tmp_path)
    assert (
        invoke(gate, "Agent", {"subagent_type": "writer"}, False)["permissionDecision"]
        == "deny"
    )
    allowed = invoke(
        gate,
        "Agent",
        {"subagent_type": "mle-solver", "model": "opus", "run_in_background": True},
        False,
    )
    assert allowed["updatedInput"]["model"] == "sonnet"
    assert allowed["updatedInput"]["run_in_background"] is False
    assert (
        invoke(gate, "Agent", {"subagent_type": "mle-solver"}, False)[
            "permissionDecision"
        ]
        == "deny"
    )
    for tool in (
        "Bash",
        "WebSearch",
        "mcp__mle_kit__llm_proxy_local",
        "mcp__chem__profile_molecule_physchem_and_adme_nn",
    ):
        assert invoke(gate, tool)["permissionDecision"] == "deny"


def test_inputs_outputs_session_binding_and_single_calls(tmp_path):
    gate = smoke.ChemistrySmokeGate("session", tmp_path)
    assert (
        invoke(gate, "Read", {"file_path": str(tmp_path / "expected.json")})[
            "permissionDecision"
        ]
        == "deny"
    )
    assert (
        invoke(gate, "Write", {"file_path": "../outside", "content": "x"})[
            "permissionDecision"
        ]
        == "deny"
    )
    read = invoke(
        gate, smoke.READ_TABLE, {"file_name": "molecules.csv", "session_id": "other"}
    )
    assert read["updatedInput"]["session_id"] == "session"
    assert (
        invoke(gate, smoke.READ_TABLE, {"file_name": "molecules.csv"})[
            "permissionDecision"
        ]
        == "deny"
    )
    assert (
        invoke(
            gate, "Read", {"file_path": "inputs/synthetic-response.png"}, solver=False
        )["permissionDecision"]
        == "deny"
    )
    assert (
        invoke(
            gate,
            "Read",
            {"file_path": "inputs/synthetic-response.png"},
            agent_id="another",
        )["permissionDecision"]
        == "deny"
    )
    assert (
        invoke(gate, "Read", {"file_path": "inputs/synthetic-response.png"})[
            "permissionDecision"
        ]
        == "allow"
    )
    assert (
        invoke(gate, smoke.PROFILE, {"molecules": ["CCO"]})["permissionDecision"]
        == "deny"
    )
    assert (
        invoke(gate, smoke.PROFILE, {"molecules": smoke.MOLECULES})[
            "permissionDecision"
        ]
        == "allow"
    )
    assert (
        invoke(gate, smoke.PROFILE, {"molecules": smoke.MOLECULES})[
            "permissionDecision"
        ]
        == "deny"
    )
    for name in ("results.json", "result.md"):
        assert (
            invoke(gate, "Write", {"file_path": name, "content": "fixture"})[
                "permissionDecision"
            ]
            == "allow"
        )
        assert (
            invoke(gate, "Write", {"file_path": name, "content": "overwrite"})[
                "permissionDecision"
            ]
            == "deny"
        )


def test_options_have_approved_caps_without_global_mutation(tmp_path):
    agent = AgentDefinition(
        description="solver", prompt="chemistry instructions", maxTurns=100
    )
    original = ClaudeAgentOptions(
        model="opus",
        max_turns=300,
        agents={"mle-solver": agent, "writer": agent},
        mcp_servers={"chem": {"url": "http://localhost:5063/mcp"}, "academia": {}},
        hooks={},
    )
    limited = smoke.limit_options(
        original, smoke.ChemistrySmokeGate("session", tmp_path)
    )
    assert (limited.model, limited.max_turns, limited.effort) == ("sonnet", 4, "low")
    assert limited.max_budget_usd == 1.0
    assert limited.env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "2000"
    assert set(limited.agents) == {"mle-solver"}
    assert limited.agents["mle-solver"].maxTurns == 8
    assert set(limited.agents["mle-solver"].tools) == {
        "Read",
        "Write",
        smoke.READ_TABLE,
        smoke.PROFILE,
    }
    assert set(limited.mcp_servers) == {"chem"}
    # --tools is global, including specialists; hooks restrict manager access.
    assert set(limited.tools) == {"Agent", "Read", "Write"}
    assert limited.setting_sources == []
    assert "strict-mcp-config" in limited.extra_args
    assert (
        original.model == "opus" and original.max_turns == 300 and agent.maxTurns == 100
    )


def test_failed_tool_is_not_counted_as_success(tmp_path):
    gate = smoke.ChemistrySmokeGate("session", tmp_path)
    invoke(gate, smoke.READ_TABLE, {"file_name": "molecules.csv"})
    asyncio.run(
        gate.after_tool(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": smoke.READ_TABLE,
                "tool_input": {},
                "tool_response": {"isError": True},
            },
            None,
            None,
        )
    )
    assert smoke.READ_TABLE not in gate.successful
    asyncio.run(
        gate.after_tool(
            {
                "hook_event_name": "PostToolUse",
                "tool_name": smoke.READ_TABLE,
                "tool_input": {},
                "tool_response": {
                    "isError": False,
                    "content": [{"type": "text", "text": '{"ok":true}'}],
                },
            },
            None,
            None,
        )
    )
    assert smoke.READ_TABLE in gate.successful


def test_recorded_ui_path_is_the_same_single_table_read(tmp_path):
    gate = smoke.ChemistrySmokeGate("session", tmp_path)
    output = invoke(
        gate,
        smoke.READ_TABLE,
        {"file_name": "inputs/molecules.csv", "session_id": "session"},
    )
    assert output["permissionDecision"] == "allow"
    assert output["updatedInput"]["file_name"] == "molecules.csv"
    assert (
        invoke(gate, smoke.READ_TABLE, {"file_name": "molecules.csv"})[
            "permissionDecision"
        ]
        == "deny"
    )


def test_watchdog_kills_worker_and_its_child(tmp_path):
    import os
    import time

    child_pid = tmp_path / "child.pid"
    program = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); open(sys.argv[1],'w').write(str(p.pid)); time.sleep(60)"
    result = smoke.run_bounded(
        [sys.executable, "-c", program, str(child_pid)], timeout=0.5
    )
    assert result == 124
    pid = int(child_pid.read_text())
    for _ in range(20):
        try:
            state = Path(f"/proc/{pid}/stat").read_text().split()[2]
        except FileNotFoundError:
            break
        if state == "Z":
            break
        time.sleep(0.05)
    else:
        raise AssertionError("Timed-out smoke left a live child")
