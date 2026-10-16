"""Installed CLI/SDK lifecycle probe against fixed localhost Messages responses.

Run: .venv/bin/python tests/agent_resume_cli_probe.py
No model, account, remote service, Docker, or research work is used. The isolated
workspace and complete fixture logs remain under /tmp/agent-resume-cli-*.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import Counter
from dataclasses import asdict, is_dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_SECONDS = 75
FAKE_KEY = "agent-resume-fixture-key"
MODEL = "fixture-model"


def jsonable(value):
    if is_dataclass(value):
        return asdict(value)
    return str(value)


class Probe:
    def __init__(self, directory: Path):
        self.directory = directory
        self.work = directory / "work"
        self.work.mkdir()
        self.config = directory / "cli-config"
        self.config.mkdir()
        self.steps = Counter()
        self.requests = []
        self.events = []
        self.messages = []
        self.hooks = []
        self.errors = []
        self.first_wait = threading.Event()
        self.resumed_wait = threading.Event()
        self.worker_requests = 0
        self.notifications = []
        self.target = None
        self.output = None
        self.lock = threading.Lock()

    def log(self, filename, value):
        with (
            self.lock,
            (self.directory / filename).open("a", encoding="utf-8") as stream,
        ):
            stream.write(json.dumps(value, default=jsonable, ensure_ascii=False) + "\n")

    def response_block(self, body):
        schemas = {tool["name"]: tool for tool in body.get("tools", [])}
        role = "manager" if "Agent" in schemas or "Task" in schemas else "worker"
        step = self.steps[role]
        self.steps[role] += 1
        self.requests.append({"role": role, "step": step, "model": body.get("model")})
        self.log("requests.jsonl", {"role": role, "step": step, "body": body})
        if len(self.requests) > 12:
            raise AssertionError("Fixture exceeded its fixed request budget")

        def tool(name, inputs):
            return {
                "type": "tool_use",
                "id": f"toolu_{role}_{step}",
                "name": name,
                "input": inputs,
            }

        if role == "worker":
            self.worker_requests += 1
            waiting = self.first_wait if step == 0 else self.resumed_wait
            if not waiting.wait(20):
                raise AssertionError(
                    f"Manager did not remain open while worker step {step} waited"
                )
            if step == 0:
                return tool(
                    "Write",
                    {
                        "file_path": str(self.work / "worker-marker.txt"),
                        "content": "first attempt reached marker\n",
                    },
                )
            if step == 1:
                return {
                    "type": "text",
                    "text": "The same writer resumed and completed the fixture.",
                }
            raise AssertionError(f"Unexpected worker request {step}")

        if step == 0:
            return tool(
                "Agent" if "Agent" in schemas else "Task",
                {
                    "subagent_type": "writer",
                    "description": "Resume fixture writer",
                    "prompt": "Write the marker, then finish only after receiving the continuation.",
                    "run_in_background": True,
                },
            )
        if step == 1:
            return {"type": "text", "text": "Waiting for initial worker."}
        if step == 2:
            text = json.dumps(body.get("messages", []))
            matches = re.findall(r"<task_id>([^<]+)</task_id>", text)
            if not matches:
                matches = re.findall(r"agentId:\s*([a-zA-Z0-9_-]+)", text)
            self.target = matches[-1] if matches else self.target
            if not self.target:
                raise AssertionError(
                    "Real CLI notification contained no resumable agent ID"
                )
            schema = (
                schemas["SendMessage"].get("input_schema", {}).get("properties", {})
            )
            recipient = "to" if "to" in schema else "recipient"
            message = "message" if "message" in schema else "content"
            inputs = {
                recipient: self.target,
                message: "Continue the same fixture and return its final answer.",
            }
            if "type" in schema:
                inputs["type"] = "message"
            return tool("SendMessage", inputs)
        if step == 3:
            return {"type": "text", "text": "Waiting for resumed worker."}
        if step == 4:
            if not self.resumed_wait.is_set() or self.worker_requests != 2:
                raise AssertionError(
                    "Manager tried to save result before the resumed worker"
                )
            return tool(
                "Write",
                {
                    "file_path": str(self.output),
                    "content": "# Fixture result\n\nThe same writer completed after its turn limit.\n",
                },
            )
        if step == 5:
            return {"type": "text", "text": "All fixture work complete."}
        raise AssertionError(f"Unexpected manager request {step}")


def make_handler(probe):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send_json(self, data, status=200):
            payload = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self.send_json(
                {"data": [{"id": MODEL, "type": "model", "display_name": "Fixture"}]}
            )

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            try:
                authenticated = (
                    self.headers.get("x-api-key") == FAKE_KEY
                    or self.headers.get("Authorization") == f"Bearer {FAKE_KEY}"
                )
                assert authenticated, "Request did not use the isolated fake key"
                if "count_tokens" in self.path:
                    self.send_json({"input_tokens": 10})
                    return
                assert self.path.startswith("/v1/messages"), self.path
                block = probe.response_block(body)
                start = (
                    {**block, "input": {}}
                    if block["type"] == "tool_use"
                    else {**block, "text": ""}
                )
                delta = (
                    {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(block["input"]),
                    }
                    if block["type"] == "tool_use"
                    else {"type": "text_delta", "text": block["text"]}
                )
                stop = "tool_use" if block["type"] == "tool_use" else "end_turn"
                message = {
                    "id": f"msg_{len(probe.requests)}",
                    "type": "message",
                    "role": "assistant",
                    "model": body.get("model"),
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 10, "output_tokens": 1},
                }
                events = [
                    ("message_start", {"message": message}),
                    ("content_block_start", {"index": 0, "content_block": start}),
                    ("content_block_delta", {"index": 0, "delta": delta}),
                    ("content_block_stop", {"index": 0}),
                    (
                        "message_delta",
                        {
                            "delta": {"stop_reason": stop, "stop_sequence": None},
                            "usage": {"output_tokens": 10},
                        },
                    ),
                    ("message_stop", {}),
                ]
                payload = "".join(
                    f"event: {event}\ndata: {json.dumps({'type': event, **value})}\n\n"
                    for event, value in events
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except (AssertionError, KeyError, ValueError, OSError) as error:
                probe.errors.append(repr(error))
                probe.log("errors.jsonl", {"error": repr(error)})
                self.send_json(
                    {
                        "type": "error",
                        "error": {
                            "type": "invalid_request_error",
                            "message": str(error),
                        },
                    },
                    400,
                )

    return Handler


async def run_probe(probe, cli):
    sys.path[:0] = [
        str(ROOT / "holosophos"),
        str(ROOT / "codearkt"),
        str(ROOT / "scripts"),
    ]
    import run_research
    from claude_agent_sdk import (
        AgentDefinition,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        HookMatcher,
    )
    from result_contract import ResultContract

    from holosophos import research_iterations as history

    item = history.start_iteration(
        probe.work, kind="initial", user_message="Native resume fixture", settings={}
    )
    completion = ResultContract(probe.work, item["id"])
    probe.output = probe.work / completion.output_path
    session_id = str(uuid.uuid4())

    async def lifecycle(data, tool_use_id=None, context=None):
        if (
            data.get("hook_event_name") == "PreToolUse"
            and data.get("tool_name") == "Write"
            and data.get("tool_input", {}).get("file_path") == str(probe.output)
        ):
            assert len(probe.notifications) == 2, (
                "Final document preceded the second native completion"
            )
            assert not completion.activity.active, (
                "Final document preceded resumed-agent completion"
            )
        response = await completion.activity.lifecycle_hook(data, tool_use_id, context)
        entry = {
            "input": data,
            "response": response,
            "active": list(completion.activity.active),
        }
        probe.hooks.append(entry)
        probe.log("hooks.jsonl", entry)
        return response

    async def stop(data, tool_use_id=None, context=None):
        response = await completion.stop_hook(data, tool_use_id, context)
        entry = {
            "input": data,
            "response": response,
            "active": list(completion.activity.active),
            "waiting": completion.waiting_at_stop,
        }
        probe.hooks.append(entry)
        probe.log("hooks.jsonl", entry)
        return response

    def render(message, *_):
        include = completion.observe(message)
        for record in completion.activity.consume(
            message, include_result_output=include
        ):
            record.update(
                session_id="fixture",
                iteration_id=item["id"],
                sequence=len(probe.events) + 1,
            )
            probe.events.append(record)
            probe.log("activity.jsonl", record)
        kind = type(message).__name__
        entry = {
            "type": kind,
            "message": message,
            "active": list(completion.activity.active),
        }
        probe.messages.append(entry)
        probe.log("sdk.jsonl", entry)
        if kind == "TaskNotificationMessage":
            probe.notifications.append(message)
            probe.target = message.task_id
        if kind == "ResultMessage":
            if message.result == "Waiting for initial worker.":
                probe.first_wait.set()
            if message.result == "Waiting for resumed worker.":
                probe.resumed_wait.set()

    options = ClaudeAgentOptions(
        cli_path=cli,
        cwd=str(probe.work),
        session_id=session_id,
        system_prompt="Execute the deterministic lifecycle fixture only.",
        tools=["Agent", "SendMessage", "Read", "Write"],
        agents={
            "writer": AgentDefinition(
                description="Fixture writer",
                prompt="Execute fixture writes only.",
                tools=["Read", "Write"],
                maxTurns=1,
            )
        },
        setting_sources=[],
        permission_mode="bypassPermissions",
        max_turns=12,
        model=MODEL,
        thinking={"type": "disabled"},
        hooks={
            **{
                name: [HookMatcher(hooks=[lifecycle])]
                for name in ("PreToolUse", "PostToolUse", "PostToolUseFailure")
            },
            "Stop": [HookMatcher(hooks=[stop])],
        },
        stderr=lambda line: probe.log("stderr.jsonl", line),
    )

    class RecordingClient(ClaudeSDKClient):
        """Instrument the actual SDK client without changing its wire behavior."""

        queries = 0
        response_reads = 0

        async def query(self, prompt, session_id="default"):
            self.queries += 1
            return await super().query(prompt, session_id=session_id)

        async def receive_response(self):
            self.response_reads += 1
            async for message in super().receive_response():
                yield message

    async with RecordingClient(options=options) as client:
        outcome = await run_research._drive_with_pause_control(
            client,
            completion.query(
                "Run the fixture writer, continue it after its limit, then save the final result.",
                mode="initial",
            ),
            session_id,
            probe.work,
            time.time(),
            render=render,
            completion=completion,
            poll_interval=0.02,
            freeze=lambda _: False,
            unfreeze=lambda _: False,
        )
    assert outcome is None, outcome
    assert not probe.errors, probe.errors
    assert probe.first_wait.is_set() and probe.resumed_wait.is_set(), (
        "Driver did not retain both waiting turns"
    )
    assert len(probe.notifications) == 2, probe.notifications
    assert probe.notifications[0].task_id == probe.notifications[1].task_id, (
        "Native resume created another agent"
    )
    starts = [
        entry["message"]
        for entry in probe.messages
        if entry["type"] == "TaskStartedMessage"
    ]
    assert len(starts) == 2 and all(
        message.task_id == probe.target for message in starts
    ), starts
    assert client.queries == 1 and client.response_reads == 3, (
        client.queries,
        client.response_reads,
    )
    assert probe.worker_requests == 2, probe.requests
    assert (
        probe.work / "worker-marker.txt"
    ).read_text() == "first attempt reached marker\n"
    assert (
        completion.validated_result()
        == b"# Fixture result\n\nThe same writer completed after its turn limit.\n"
    )
    lifecycle_events = [
        record["activity"]
        for record in probe.events
        if record["activity"]["kind"] in {"agent_start", "agent_resume", "agent_end"}
    ]
    assert [event["kind"] for event in lifecycle_events] == [
        "agent_start",
        "agent_end",
        "agent_resume",
        "agent_end",
    ], lifecycle_events
    assert [event.get("status") for event in lifecycle_events] == [
        None,
        "limited",
        None,
        "complete",
    ], lifecycle_events
    assert len({event["invocationId"] for event in lifecycle_events}) == 1, (
        lifecycle_events
    )
    assert completion.answer == "All fixture work complete.", completion.answer
    assert (
        len(
            [
                item
                for item in probe.hooks
                if item["input"].get("tool_name") == "Write"
                and item["input"].get("hook_event_name") == "PostToolUse"
            ]
        )
        == 2
    )
    assert not any(
        item["response"].get("decision") == "block" for item in probe.hooks
    ), "A waiting turn spent the final-result reminder"
    verify_ui_replay(probe)
    return {
        "status": "passed",
        "requests": probe.requests,
        "native_agent_id": probe.target,
        "invocation_id": lifecycle_events[0]["invocationId"],
        "notifications": len(probe.notifications),
        "driver_queries": client.queries,
        "driver_response_reads": client.response_reads,
        "ui_replay": "passed",
        "logs": str(probe.directory),
    }


def verify_ui_replay(probe):
    """Use the real UI reducer on real SDK-derived records, including replay."""
    script = """
import assert from 'node:assert/strict';
import fs from 'node:fs';
const { activityFromEvents } = await import(process.argv[1]);
const { buildActivity } = await import(process.argv[2]);
const records = fs.readFileSync(process.argv[3], 'utf8').trim().split('\\n').map(line => JSON.parse(line));
const limitedIndex = records.findIndex(record => record.activity.kind === 'agent_end' && record.activity.status === 'limited');
const resumeIndex = records.findIndex(record => record.activity.kind === 'agent_resume');
const view = items => buildActivity(activityFromEvents(items).events);
const limited = view(records.slice(0, limitedIndex + 1)).agents;
const resumed = view(records.slice(0, resumeIndex + 1)).agents;
const final = view(records).agents;
assert.equal(limited.length, 1);
assert.equal(resumed.length, 1);
assert.equal(final.length, 1);
assert.equal(limited[0].status, 'limited');
assert.equal(resumed[0].status, 'running');
assert.equal(resumed[0].id, limited[0].id);
assert.equal(resumed[0].finishedAt, undefined);
assert.equal(resumed[0].summary, undefined);
assert.ok(resumed[0].children.some(node => node.kind === 'note' && node.text.includes('1-turn limit')));
assert.equal(final[0].status, 'complete');
assert.equal(final[0].id, limited[0].id);
assert.ok(final[0].children.some(node => node.kind === 'message' && node.text.includes('resumed and completed')));
assert.deepEqual(view([...records, ...records]), view(records));
console.log('Native event replay: one stable agent tab, honest limit and completion, no duplicates.');
"""
    result = subprocess.run(
        [
            shutil.which("node") or "node",
            "--experimental-strip-types",
            "--input-type=module",
            "-e",
            script,
            (ROOT / "ui/src/activity/fromEvents.ts").as_uri(),
            (ROOT / "ui/src/activity/activity.ts").as_uri(),
            str(probe.directory / "activity.jsonl"),
        ],
        text=True,
        capture_output=True,
        timeout=15,
        check=True,
    )
    probe.log(
        "ui-verification.jsonl", {"stdout": result.stdout, "stderr": result.stderr}
    )


def main():
    cli = shutil.which("claude")
    assert cli, "Installed Claude CLI not found"
    probe = Probe(Path(tempfile.mkdtemp(prefix="agent-resume-cli-")))
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(probe))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # The SDK merges os.environ into its child. Whitelist before imports as well
    # as launching CLI; do not inherit OAuth, API keys, proxies, MCP or OTEL config.
    process_path = os.environ.get("PATH", os.defpath)
    os.environ.clear()
    os.environ.update(
        {
            "PATH": process_path,
            "LANG": "C.UTF-8",
            "PYTHON_DOTENV_DISABLED": "1",
            "CLAUDE_CONFIG_DIR": str(probe.config),
            "ANTHROPIC_API_KEY": FAKE_KEY,
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
            "MAX_THINKING_TOKENS": "0",
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": MODEL,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": MODEL,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": MODEL,
            "ANTHROPIC_SMALL_FAST_MODEL": MODEL,
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "NO_PROXY": "127.0.0.1,localhost",
        }
    )
    print(
        json.dumps({"status": "starting", "cli": cli, "logs": str(probe.directory)}),
        flush=True,
    )
    try:
        report = asyncio.run(asyncio.wait_for(run_probe(probe, cli), TIMEOUT_SECONDS))
        print(json.dumps(report, ensure_ascii=False), flush=True)
    finally:
        probe.first_wait.set()
        probe.resumed_wait.set()
        server.shutdown()


if __name__ == "__main__":
    main()
