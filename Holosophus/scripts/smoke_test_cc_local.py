"""Bounded opt-in LLM smoke using the real CC HTTP handler in this process.

Without --run this only displays the limits. It does not change the normal
server, YAML prompts, generated agents, or browser ownership settings.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claude_agent_sdk.types import ClaudeAgentOptions, HookMatcher

LOCAL_BASH = "mcp__mle_kit__bash"
PROJECT_DIR = Path(__file__).resolve().parent.parent


class SmokeToolGate:
    """Allow one local delegation and one sandbox command, deny all other tools."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.calls: list[str] = []
        self.solver_id: str | None = None

    async def __call__(self, hook_input, tool_use_id, context):
        name = hook_input["tool_name"]
        args = dict(hook_input.get("tool_input", {}))
        allowed = False
        if (
            name == "Agent"
            and args.get("subagent_type") == "mle-solver"
            and name not in self.calls
        ):
            args.update(model="sonnet", run_in_background=False)
            allowed = True
        elif (
            name == LOCAL_BASH
            and name not in self.calls
            and hook_input.get("agent_type") == "mle-solver"
            and hook_input.get("agent_id")
        ):
            args.update(session_id=self.session_id, timeout=20, cwd=None)
            self.solver_id = hook_input["agent_id"]
            allowed = True
        result: dict[str, Any] = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow" if allowed else "deny",
        }
        if allowed:
            self.calls.append(name)
            result["updatedInput"] = args
        else:
            result["permissionDecisionReason"] = (
                "The smoke permits one local delegation and one bash call only. Finish now."
            )
        return {"hookSpecificOutput": result}


def limit_options(
    options: ClaudeAgentOptions, gate: SmokeToolGate
) -> ClaudeAgentOptions:
    solver = replace(
        options.agents["mle-solver"],
        model="sonnet",
        maxTurns=4,
        effort="low",
        tools=[LOCAL_BASH],
        mcpServers=["mle_kit"],
        background=False,
        prompt=options.agents["mle-solver"].prompt
        + (
            "\nThis is a tiny approved smoke test. Use exactly one mcp__mle_kit__bash call "
            "to calculate the requested values with Python stdlib and save the requested files. "
            "Do not search, install packages, train models, or retry. Return a short result."
        ),
    )
    return replace(
        options,
        model="sonnet",
        max_turns=4,
        effort="low",
        thinking={"type": "disabled"},
        max_budget_usd=0.50,
        agents={"mle-solver": solver},
        mcp_servers={"mle_kit": options.mcp_servers["mle_kit"]},
        tools=["Agent"],
        disallowed_tools=[
            "Bash",
            "WebSearch",
            "WebFetch",
            "mcp__mle_kit__remote_bash",
            "mcp__mle_kit__remote_text_editor",
            "mcp__mle_kit__remote_download",
            "mcp__mle_kit__llm_proxy_remote",
            "mcp__mle_kit__llm_proxy_local",
            "mcp__mle_kit__text_editor",
        ],
        hooks={"PreToolUse": [HookMatcher(hooks=[gate])]},
        env={**options.env, "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "2000"},
    )


async def run_smoke(session_id: str, client_id: str) -> dict:
    import httpx
    import cc_runner

    workdir = cc_runner._workspace_root() / session_id
    if workdir.exists():
        raise RuntimeError(
            "Refusing to overwrite an existing run or retry it implicitly"
        )
    gate = SmokeToolGate(session_id)
    original_builder = cc_runner._build_sdk_options
    original_mapper = cc_runner.cc_research_lib.map_sdk_message_to_events
    original_no_wait = cc_runner.CC_NO_AUTO_WAIT
    original_register = cc_runner.REGISTRY.register
    owned_session = None
    metrics: list[dict] = []

    def register(session):
        nonlocal owned_session
        owned_session = session
        original_register(session)

    def build(*args, **kwargs):
        if kwargs.get("resume"):
            raise RuntimeError("Smoke retries/resume are disabled")
        return limit_options(original_builder(*args, **kwargs), gate)

    def map_messages(message, *args, **kwargs):
        if type(message).__name__ == "ResultMessage":
            metrics.append(
                {
                    key: getattr(message, key, None)
                    for key in (
                        "subtype",
                        "is_error",
                        "num_turns",
                        "total_cost_usd",
                        "usage",
                        "duration_ms",
                    )
                }
            )
        return original_mapper(message, *args, **kwargs)

    cc_runner._build_sdk_options = build
    cc_runner.cc_research_lib.map_sdk_message_to_events = map_messages
    cc_runner.CC_NO_AUTO_WAIT = True
    cc_runner.REGISTRY.register = register
    prompt = (
        "This is a small integration smoke test. Delegate exactly once to mle-solver. "
        "Ask it to use exactly one local mcp__mle_kit__bash invocation with Python stdlib "
        f"and session_id={session_id!r}. Calculate sum and arithmetic mean of [1,2,3,4,5]. "
        "Save calculation.json containing numbers, sum and mean, and result.md with a short result. "
        "Print the computed JSON as stdout. No literature, proposals, training, downloads, "
        "other agents or retries are needed. After its reply, return the numbers and file paths "
        "in your final answer and finish immediately. This request supersedes the usual full-paper workflow."
    )
    try:
        transport = httpx.ASGITransport(app=cc_runner.create_app())
        async with httpx.AsyncClient(
            transport=transport, base_url="http://smoke"
        ) as client:
            response = await asyncio.wait_for(
                client.post(
                    "/agents/manager",
                    headers={
                        "X-Holo-Client": client_id,
                        "X-Holo-Orchestrator": "claude_code",
                    },
                    json={
                        "session_id": session_id,
                        "messages": [{"role": "user", "content": prompt}],
                        "model_name": "sonnet",
                        "compute_mode": "Local",
                        "orchestrator": "claude_code",
                    },
                ),
                timeout=180,
            )
            response.raise_for_status()
        (workdir / "smoke-events.ndjson").write_text(response.text, encoding="utf-8")
        events = [
            json.loads(line) for line in response.text.splitlines() if line.strip()
        ]
        calculation = (
            json.loads((workdir / "calculation.json").read_text())
            if (workdir / "calculation.json").exists()
            else None
        )
        passed = bool(
            calculation
            and calculation.get("sum") == 15
            and calculation.get("mean") == 3
            and gate.calls == ["Agent", LOCAL_BASH]
            and gate.solver_id
            and metrics
            and metrics[-1]["is_error"] is False
            and (workdir / "result.md").exists()
        )
        report = {
            "session_id": session_id,
            "client_id": client_id,
            "passed": passed,
            "tools": gate.calls,
            "solver_id": gate.solver_id,
            "calculation": calculation,
            "metrics": metrics,
            "events": [
                {"agent": e.get("agent_name"), "type": e.get("event_type")}
                for e in events
            ],
        }
        (workdir / "smoke-check.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )
        return report
    finally:
        # HTTP cleanup drops the registry entry even while the SDK task lives.
        # Keep our own reference to stop only this run on timeout/disconnect.
        if (
            owned_session is not None
            and owned_session.task is not None
            and not owned_session.task.done()
        ):
            owned_session.interrupted = True
            await cc_runner._terminate_run(owned_session, interrupt_timeout=2)
        cc_runner.REGISTRY.register = original_register
        cc_runner._build_sdk_options = original_builder
        cc_runner.cc_research_lib.map_sdk_message_to_events = original_mapper
        cc_runner.CC_NO_AUTO_WAIT = original_no_wait


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run", action="store_true", help="Make the explicitly approved LLM request"
    )
    parser.add_argument("--client-id", default="local-smoke")
    args = parser.parse_args()
    if not args.run:
        print(
            "No LLM call. With --run: Sonnet, low effort, 4 manager/4 solver turns, "
            "one delegation, one local command, 180s timeout, $0.50 SDK estimate cap, no retries."
        )
        return
    session_id = "debug-sonnet-local-" + datetime.now(timezone.utc).strftime(
        "%Y%m%d-%H%M%S"
    )
    report = asyncio.run(run_smoke(session_id, args.client_id))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
