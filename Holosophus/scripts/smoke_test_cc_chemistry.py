"""Opt-in bounded chemistry smoke through the real upload API and CC handler."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import signal
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from claude_agent_sdk.types import ClaudeAgentOptions, HookMatcher
from research_profiles import ChemistryToolGate, REMOTE_TOOLS

MOLECULES = ["CCO", "CC(=O)O", "CC(=O)C"]
READ_TABLE = "mcp__chem__read_data_file"
PROFILE = "mcp__chem__profile_molecule_physchem_and_adme_heuristic"
PROJECT_DIR = Path(__file__).resolve().parents[1]
EXAMPLE_DIR = PROJECT_DIR.parents[1] / "annual-report-2026/examples/chemistry-smoke"


class ChemistrySmokeGate:
    def __init__(self, session_id: str, workdir: Path):
        self.workdir = workdir.resolve()
        self.chemistry = ChemistryToolGate(session_id, workdir)
        self.calls: list[dict] = []
        self.used: set[str] = set()
        self.successful: set[str] = set()
        self.results: list[dict] = []
        self.solver_id: str | None = None

    def relative(self, value):
        if not isinstance(value, str):
            return None
        path = Path(value)
        if not path.is_absolute():
            path = self.workdir / path
        try:
            return str(path.resolve().relative_to(self.workdir))
        except ValueError:
            return None

    async def __call__(self, hook_input, tool_use_id, context):
        name = hook_input.get("tool_name", "")
        args = dict(hook_input.get("tool_input") or {})
        is_solver = hook_input.get("agent_type") == "mle-solver" and bool(
            hook_input.get("agent_id")
        )
        same_solver = self.solver_id in (None, hook_input.get("agent_id"))
        key = None
        if (
            name == "Agent"
            and not hook_input.get("agent_id")
            and args.get("subagent_type") == "mle-solver"
        ):
            key = "Agent"
            args.update(model="sonnet", run_in_background=False)
        elif is_solver and same_solver:
            if name == READ_TABLE:
                adapted = await self.chemistry(
                    {**hook_input, "tool_input": args}, tool_use_id, context
                )
                args = adapted.get("hookSpecificOutput", {}).get("updatedInput", args)
            if name == READ_TABLE and args.get("file_name") == "molecules.csv":
                key = READ_TABLE
            elif (
                name == PROFILE
                and isinstance(args.get("molecules"), list)
                and sorted(args["molecules"]) == sorted(MOLECULES)
            ):
                key = PROFILE
            elif (
                name == "Read"
                and self.relative(args.get("file_path"))
                == "inputs/synthetic-response.png"
            ):
                key = "Read"
                args["file_path"] = str(self.workdir / "inputs/synthetic-response.png")
            elif name == "Write" and self.relative(args.get("file_path")) in (
                "results.json",
                "result.md",
            ):
                relative = self.relative(args["file_path"])
                key = "Write:" + relative
                args["file_path"] = str(self.workdir / relative)
        allowed = key is not None and key not in self.used
        if allowed:
            adapted = await self.chemistry(
                {**hook_input, "tool_input": args}, tool_use_id, context
            )
            specific = adapted.get("hookSpecificOutput", {})
            if specific.get("permissionDecision") == "deny":
                allowed = False
            else:
                args = specific.get("updatedInput", args)
        self.calls.append(
            {
                "tool": name,
                "allowed": allowed,
                "agent_id": hook_input.get("agent_id"),
                "input": args,
            }
        )
        result = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow" if allowed else "deny",
        }
        if allowed:
            self.used.add(key)
            if is_solver:
                self.solver_id = hook_input["agent_id"]
            result["updatedInput"] = args
        else:
            result["permissionDecisionReason"] = (
                "This bounded smoke only permits one delegation, one table read, one PNG read, one chemical batch, and one write per result file. Finish without retries."
            )
        return {"hookSpecificOutput": result}

    async def after_tool(self, hook_input, tool_use_id, context):
        name = hook_input.get("tool_name", "")
        response = hook_input.get("tool_response")
        failed = hook_input.get("hook_event_name") == "PostToolUseFailure"
        if isinstance(response, dict):
            failed = (
                failed
                or bool(response.get("isError") or response.get("is_error"))
                or response.get("ok") is False
            )
        key = name
        if name == "Write":
            key += ":" + str(
                self.relative(hook_input.get("tool_input", {}).get("file_path"))
            )
        if not failed and key in self.used:
            self.successful.add(key)
        # Keep real MCP responses for verification; omit inline image bytes.
        summary = (
            response
            if name in (READ_TABLE, PROFILE)
            else (
                {"type": response.get("type"), "keys": list(response)}
                if isinstance(response, dict)
                else {"python_type": type(response).__name__}
            )
        )
        self.results.append(
            {
                "tool": name,
                "failed": failed,
                "tool_use_id": hook_input.get("tool_use_id", tool_use_id),
                "response": summary,
                "error": hook_input.get("error"),
            }
        )
        return {}


def limit_options(
    options: ClaudeAgentOptions, gate: ChemistrySmokeGate
) -> ClaudeAgentOptions:
    solver = replace(
        options.agents["mle-solver"],
        model="sonnet",
        maxTurns=8,
        effort="low",
        background=False,
        tools=["Read", "Write", READ_TABLE, PROFILE],
        mcpServers=["chem"],
        prompt=options.agents["mle-solver"].prompt
        + '\nThis approved smoke replaces the usual research workflow. Read the supplied CSV once with read_data_file(file_name="molecules.csv"); read the supplied PNG once with native Read. Call the chemical profiler once with the three SMILES. Write only results.json and result.md at session root, once each; no code, logs, retries or other artifacts. Return the small result table and paragraph in full to the manager.',
    )
    return replace(
        options,
        model="sonnet",
        max_turns=4,
        effort="low",
        thinking={"type": "disabled"},
        max_budget_usd=1.0,
        agents={"mle-solver": solver},
        mcp_servers={"chem": options.mcp_servers["chem"]},
        # This is the global CLI base set, including subagents. Role/path
        # restrictions belong in the hook rather than removing native tools.
        tools=["Agent", "Read", "Write"],
        setting_sources=[],
        extra_args={**options.extra_args, "strict-mcp-config": None},
        system_prompt=(options.system_prompt or "")
        + "\nFor this bounded smoke, immediately delegate the complete task once to mle-solver. Do not read files or run tools yourself. After the specialist replies, return its short table and paragraph in full and finish. Only the specialist may read the two inputs and write the two result files.",
        disallowed_tools=list(
            dict.fromkeys(
                [
                    *options.disallowed_tools,
                    *REMOTE_TOOLS,
                    "Bash",
                    "WebSearch",
                    "WebFetch",
                    "mcp__mle_kit__llm_proxy_local",
                ]
            )
        ),
        hooks={
            "PreToolUse": [HookMatcher(hooks=[gate])],
            "PostToolUse": [HookMatcher(hooks=[gate.after_tool])],
            "PostToolUseFailure": [HookMatcher(hooks=[gate.after_tool])],
        },
        env={**options.env, "CLAUDE_CODE_MAX_OUTPUT_TOKENS": "2000"},
    )


def validate_result(workdir, gate, metrics):
    expected = json.loads((EXAMPLE_DIR / "expected.json").read_text())
    try:
        actual = json.loads((workdir / "results.json").read_text())
        rows = {row["sample_id"]: row for row in actual["molecules"]}
        numeric = (
            len(actual["molecules"]) == 3
            and set(rows) == {"A", "B", "C"}
            and all(
                rows[row["sample_id"]]["smiles"] == row["smiles"]
                and all(
                    math.isclose(
                        float(rows[row["sample_id"]][key]),
                        row[key],
                        rel_tol=0,
                        abs_tol=0.001,
                    )
                    for key in ("molecular_weight", "logp")
                )
                for row in expected["molecules"]
            )
        )
        image = (
            actual["image_response"] == expected["image_response"]
            and actual["selected_sample"] == expected["selected_sample"]
        )
    except (OSError, ValueError, KeyError, TypeError):
        actual, numeric, image = None, False, False
    used = {
        "Agent",
        READ_TABLE,
        PROFILE,
        "Read",
        "Write:results.json",
        "Write:result.md",
    }
    checks = {
        "numeric_results": numeric,
        "image_values_and_selection": image,
        "required_tools": gate.used == used and bool(gate.solver_id),
        "successful_tool_results": gate.successful == used,
        "sdk_success": bool(
            metrics
            and metrics[-1].get("is_error") is False
            and metrics[-1].get("subtype") == "success"
        ),
        "result_markdown_nonempty": (workdir / "result.md").is_file()
        and bool((workdir / "result.md").read_text().strip()),
    }
    return {"passed": all(checks.values()), "checks": checks, "calculation": actual}


async def run_smoke(session_id: str, client_id: str, ui_url: str):
    import httpx
    import cc_runner

    workdir = cc_runner._workspace_root() / session_id
    if workdir.exists():
        raise RuntimeError("Refusing to overwrite or implicitly retry a smoke session")
    gate = ChemistrySmokeGate(session_id, workdir)
    headers = {"X-Holo-Client": client_id, "X-Holo-Orchestrator": "claude_code"}
    # The real upload API establishes owner and inputs; it does not start an agent.
    async with httpx.AsyncClient(timeout=20) as client:
        uploaded = await client.post(
            f"{ui_url}/ui/sessions/{session_id}/inputs",
            headers=headers,
            files=[
                ("files", (name, (EXAMPLE_DIR / "inputs" / name).read_bytes(), mime))
                for name, mime in [
                    ("molecules.csv", "text/csv"),
                    ("synthetic-response.png", "image/png"),
                ]
            ],
        )
        uploaded.raise_for_status()
        input_files = [item["path"] for item in uploaded.json()["files"]]
    for name in input_files:
        if (workdir / name).read_bytes() != (EXAMPLE_DIR / name).read_bytes():
            raise RuntimeError("Uploaded input differs from the reviewed fixture")
    original_builder = cc_runner._build_sdk_options
    original_mapper = cc_runner.cc_research_lib.map_sdk_message_to_events
    original_no_wait = cc_runner.CC_NO_AUTO_WAIT
    original_register = cc_runner.REGISTRY.register
    owned_session = None
    metrics = []
    report = {"session_id": session_id, "client_id": client_id, "passed": False}

    def register(session):
        nonlocal owned_session
        owned_session = session
        original_register(session)

    def build(*args, **kwargs):
        if kwargs.get("resume"):
            raise RuntimeError("Smoke resume/retries are disabled")
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
        EXAMPLE_DIR / "prompt.md"
    ).read_text() + "\nМенеджер: верни полученную от экспериментатора небольшую таблицу и абзац целиком в финальном ответе, затем пути результатов: штатный runner сохраняет финальный ответ в result.md."
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=cc_runner.create_app()),
            base_url="http://smoke",
        ) as client:
            response = await asyncio.wait_for(
                client.post(
                    "/agents/manager",
                    headers=headers,
                    json={
                        "session_id": session_id,
                        "messages": [{"role": "user", "content": prompt}],
                        "model_name": "sonnet",
                        "compute_mode": "Local",
                        "orchestrator": "claude_code",
                        "research_profile": "chemistry",
                        "input_files": input_files,
                    },
                ),
                timeout=180,
            )
            response.raise_for_status()
        (workdir / "smoke-events.ndjson").write_text(response.text)
        report.update(validate_result(workdir, gate, metrics))
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        # Registry removal by StreamingResponse does not guarantee SDK shutdown.
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
        report.update(
            tools=gate.calls,
            tool_results=gate.results,
            solver_id=gate.solver_id,
            metrics=metrics,
        )
        try:
            (workdir / "smoke-check.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2)
            )
        except OSError as error:
            # Preserve the actual pre-SDK failure if the upload directory is
            # not writable by this host process.
            report["report_file_error"] = str(error)
    return report


def run_bounded(command, timeout=180):
    """One worker/CLI process group; kill it even if SDK shutdown hangs."""
    worker = subprocess.Popen(command, start_new_session=True)
    try:
        return worker.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124
    finally:
        try:
            os.killpg(worker.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        worker.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="store_true",
        help="Execute the explicitly approved single LLM run",
    )
    parser.add_argument("--client-id", default="chemistry-smoke")
    parser.add_argument("--ui-url", default="http://127.0.0.1:5062")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--session-id", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.run:
        print(
            "No LLM call. Opt-in limits: Sonnet low effort, 4 manager / 8 solver turns, one delegation, CSV+PNG, one chemical batch, two outputs, 180s timeout, $1 SDK estimate cap; no retries."
        )
        return
    session_id = args.session_id or "debug-sonnet-chemistry-" + datetime.now(
        timezone.utc
    ).strftime("%Y%m%d-%H%M%S")
    if not args.worker:
        code = run_bounded(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--run",
                "--worker",
                "--session-id",
                session_id,
                "--client-id",
                args.client_id,
                "--ui-url",
                args.ui_url,
            ]
        )
        if code == 124:
            print(
                f"Smoke {session_id} stopped by the 180s process watchdog; no retry.",
                flush=True,
            )
        raise SystemExit(code)
    print(f"Starting exactly one approved smoke: {session_id}", flush=True)
    report = asyncio.run(run_smoke(session_id, args.client_id, args.ui_url.rstrip("/")))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
