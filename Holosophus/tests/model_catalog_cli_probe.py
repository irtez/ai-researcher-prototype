"""Installed CLI -> local scripted Messages API, no LLM/account/remote compute.

Run with uv run --locked --offline python tests/model_catalog_cli_probe.py.
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
import yaml
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "holosophos"), str(ROOT / "scripts")]
from holosophos import model_catalog
from claude_agent_sdk import ClaudeAgentOptions, AgentDefinition, ResultMessage, query
import session_titles

calls = []
steps = Counter()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        data = json.dumps(
            {
                "data": [
                    {"id": "fixture-model", "type": "model", "display_name": "Fixture"}
                ]
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if "count_tokens" in self.path:
            data = b'{"input_tokens": 10}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        tools = [t["name"] for t in body.get("tools", [])]
        role = (
            "title"
            if not tools
            else "manager"
            if "Agent" in tools or "Task" in tools
            else "worker"
        )
        calls.append(
            {
                "role": role,
                "model": body.get("model"),
                "max_tokens": body.get("max_tokens"),
                "effort": body.get("output_config", {}).get("effort"),
                "authenticated": self.headers.get("Authorization")
                == "Bearer fixture-key",
            }
        )
        step = steps[role]
        steps[role] += 1
        if role == "manager" and step == 0:
            block = {
                "type": "tool_use",
                "id": "toolu_delegate",
                "name": "Agent" if "Agent" in tools else "Task",
                "input": {
                    "subagent_type": "probe-worker",
                    "description": "Read fixture",
                    "prompt": "Read input.txt",
                },
            }
        elif role == "worker" and step == 0:
            block = {
                "type": "tool_use",
                "id": "toolu_read",
                "name": "Read",
                "input": {"file_path": str(WORK / "input.txt")},
            }
        else:
            block = {
                "type": "text",
                "text": "Название проверки" if role == "title" else "Fixture complete",
            }
        start = (
            {**block, "input": {}}
            if block["type"] == "tool_use"
            else {**block, "text": ""}
        )
        delta = (
            {"type": "input_json_delta", "partial_json": json.dumps(block["input"])}
            if block["type"] == "tool_use"
            else {"type": "text_delta", "text": block["text"]}
        )
        message = {
            "id": f"msg_{len(calls)}",
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
                    "delta": {
                        "stop_reason": "tool_use"
                        if block["type"] == "tool_use"
                        else "end_turn",
                        "stop_sequence": None,
                    },
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


async def main():
    configured = model_catalog.claude_options(SELECTION)
    extra = {
        "CLAUDE_CONFIG_DIR": str(WORK / "cli-config"),
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
    }
    options = ClaudeAgentOptions(
        cli_path=CLI,
        cwd=str(WORK),
        system_prompt="Execute the supplied fixture only.",
        tools=["Agent", "Read"],
        agents={
            "probe-worker": AgentDefinition(
                description="Fixture reader",
                prompt="Read the provided file.",
                tools=["Read"],
            )
        },
        setting_sources=[],
        permission_mode="bypassPermissions",
        max_turns=4,
        **{**configured, "env": {**configured["env"], **extra}},
    )
    results = [
        m
        async for m in query(prompt="Delegate the fixture read", options=options)
        if isinstance(m, ResultMessage)
    ]
    assert results and not results[-1].is_error, results
    # Title uses the production helper, with the same isolated CLI environment.
    import cc_config

    original = session_titles.cli_environment
    session_titles.cli_environment = lambda: {**original(), **extra}
    title = await session_titles.request_title("Fixture title", SELECTION)
    assert title == "Название проверки", title
    assert {c["role"] for c in calls} == {"manager", "worker", "title"}, calls
    assert all(c["model"] == "fixture-model" and c["authenticated"] for c in calls), (
        calls
    )
    assert all(c["max_tokens"] == 4096 for c in calls), calls
    assert all(c["effort"] == "low" for c in calls), calls
    print(json.dumps({"requests": calls, "status": "passed"}, ensure_ascii=False))


CLI = shutil.which("claude")
assert CLI
with tempfile.TemporaryDirectory(prefix="model-catalog-cli-") as directory:
    WORK = Path(directory)
    (WORK / "input.txt").write_text("fixture data")
    (WORK / "cli-config").mkdir()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # Avoid any access to real user credentials/settings by the child CLI.
    os.environ["FIXTURE_MODEL_KEY"] = "fixture-key"
    SELECTION = {
        "id": "fixture",
        "label": "Fixture",
        "runtime": "claude_code",
        "connection": "api",
        "model_id": "fixture-model",
        "base_url": f"http://127.0.0.1:{server.server_port}",
        "auth": {"type": "bearer", "token_env": "FIXTURE_MODEL_KEY"},
        "effort": "low",
        "generation": {
            "effort": {"choices": ["low"], "default": "low"},
            "thinking": {"type": "disabled"},
            "max_output_tokens": 4096,
        },
    }
    catalog = WORK / "models.yaml"
    catalog.write_text(
        yaml.safe_dump(
            {
                "defaults": {"claude_code": "fixture"},
                "models": {
                    "fixture": {
                        k: v for k, v in SELECTION.items() if k not in {"id", "effort"}
                    }
                },
            }
        )
    )
    os.environ["AI_RESEARCHER_MODELS_CONFIG"] = str(catalog)
    try:
        asyncio.run(asyncio.wait_for(main(), 60))
    finally:
        server.shutdown()
