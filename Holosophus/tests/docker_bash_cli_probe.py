"""Opt-in integration: installed Claude CLI + production Docker adapter.
RUN_DOCKER_TESTS=1 uv run --locked --offline python tests/docker_bash_cli_probe.py
All model responses are scripted locally; no real LLM or working credentials.
"""

import json
import os
from pathlib import Path
import shlex
import re
import signal
import subprocess
import tempfile
import threading
import time
import uuid
import sys
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if os.environ.get("RUN_DOCKER_TESTS") != "1":
    raise SystemExit(
        "Set RUN_DOCKER_TESTS=1 to run the local CLI/Docker check (no LLM)."
    )
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from docker_bash import DockerBashSession

ROOT = Path(tempfile.mkdtemp(prefix="cc-docker-bash-"))
WORK = ROOT / "workspace"
WORK.mkdir()
(ROOT / "tmp").mkdir()
(ROOT / "config").mkdir()
CLI = os.environ.get("CC_PROBE_CLI") or shutil.which("claude")
assert CLI, "Install Claude CLI before running this probe"
counts = {"manager": 0, "worker": 0}
calls = []


def tool(name, args, identifier):
    return {"type": "tool_use", "id": identifier, "name": name, "input": args}


def proof(name):
    code = (
        "import json, pathlib, os; data={'docker':pathlib.Path('/.dockerenv').exists(),'cwd':os.getcwd()}; pathlib.Path('"
        + name
        + ".json').write_text(json.dumps(data)); print(json.dumps(data))"
    )
    return "python -c " + shlex.quote(code)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if "count_tokens" in self.path:
            data = b'{"input_tokens":10}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)
            return
        names = [x["name"] for x in body.get("tools", [])]
        role = "manager" if "Agent" in names or "Task" in names else "worker"
        step = counts[role]
        counts[role] += 1
        calls.append({"role": role, "step": step, "tools": names})
        print(f"Scripted request {role}/{step}", flush=True)
        if role == "worker" and step == 0:
            block = tool(
                "Bash",
                {"command": proof("worker"), "description": "Check subagent container"},
                "toolu_worker",
            )
        elif role == "manager" and step == 0:
            block = tool(
                "Bash",
                {
                    "command": "mkdir -p stage && cd /workdir/workspace/stage && "
                    + proof("manager"),
                    "description": "Check manager container and alias cwd",
                },
                "toolu_manager",
            )
        elif role == "manager" and step == 1:
            block = tool(
                "Bash",
                {
                    "command": "pwd && test -f manager.json && printf 'cwd preserved\\n'",
                    "description": "Check persisted directory",
                },
                "toolu_cwd",
            )
        elif role == "manager" and step == 2:
            block = tool(
                "Agent" if "Agent" in names else "Task",
                {
                    "subagent_type": "probe-worker",
                    "prompt": "Execute the scripted container probe",
                    "description": "Check subagent Bash",
                },
                "toolu_agent",
            )
        elif role == "manager" and step == 3:
            block = tool(
                "Bash",
                {
                    "command": "sleep 2; printf background > background.txt",
                    "run_in_background": True,
                    "description": "Check background execution",
                },
                "toolu_bg",
            )
        elif role == "manager" and step == 4:
            block = tool(
                "Bash",
                {
                    "command": "sleep 4; printf survived > after-timeout.txt",
                    "timeout": 1000,
                    "description": "Check timeout cleanup",
                },
                "toolu_timeout",
            )
        elif role == "manager" and step == 5:
            block = tool(
                "Bash",
                {
                    "command": "printf started > stop-started.txt; sleep 5; printf survived > after-stop.txt",
                    "run_in_background": True,
                    "description": "Start cancellable background command",
                },
                "toolu_bg_stop",
            )
        elif role == "manager" and step == 6:
            for _ in range(150):
                if list(WORK.rglob("stop-started.txt")):
                    break
                time.sleep(0.02)
            outputs = [
                str(part.get("content"))
                for msg in body["messages"]
                if isinstance(msg.get("content"), list)
                for part in msg["content"]
                if isinstance(part, dict) and part.get("tool_use_id") == "toolu_bg_stop"
            ]
            task_id = re.search(r"ID: ([A-Za-z0-9]+)", outputs[-1]).group(1)
            block = tool("TaskStop", {"task_id": task_id}, "toolu_stop")
        else:
            block = {"type": "text", "text": "Scripted probe finished."}
        stop = "tool_use" if block["type"] == "tool_use" else "end_turn"
        message = {
            "id": "msg_" + uuid.uuid4().hex,
            "type": "message",
            "role": "assistant",
            "model": body.get("model", "claude-sonnet-4-6"),
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 1},
        }
        start = {**block}
        if block["type"] == "tool_use":
            start["input"] = {}
            delta = {
                "type": "input_json_delta",
                "partial_json": json.dumps(block["input"]),
            }
        else:
            start["text"] = ""
            delta = {"type": "text_delta", "text": block["text"]}
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
            "event: " + name + "\ndata: " + json.dumps({"type": name, **data}) + "\n\n"
            for name, data in events
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
executor = DockerBashSession(WORK, runtime_root=ROOT / "runtime")
# Do not inherit real credentials or the working Claude configuration.
env = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "ANTHROPIC_API_KEY": "fixture-not-a-real-key",
    "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
    "CLAUDE_CONFIG_DIR": str(ROOT / "config"),
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "DISABLE_AUTOUPDATER": "1",
    "DISABLE_TELEMETRY": "1",
    "DISABLE_ERROR_REPORTING": "1",
    "CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION": "false",
}
agent = {
    "probe-worker": {
        "description": "Container probe specialist",
        "prompt": "Only execute the scripted probe; no other actions.",
        "tools": ["Bash"],
        "model": "sonnet",
    }
}
command = [
    CLI,
    "-p",
    "Run the scripted technical probe.",
    "--verbose",
    "--output-format",
    "stream-json",
    "--model",
    "sonnet",
    "--permission-mode",
    "bypassPermissions",
    "--setting-sources",
    "",
    "--strict-mcp-config",
    "--mcp-config",
    '{"mcpServers":{}}',
    "--agents",
    json.dumps(agent),
    "--max-turns",
    "12",
]
print(f"Probe artifacts: {ROOT}", flush=True)
try:
    executor.open()
    env.update(executor.env)
    with (
        (ROOT / "stream.jsonl").open("w") as out,
        (ROOT / "stderr.log").open("w") as err,
    ):
        proc = subprocess.Popen(
            command, env=env, cwd=WORK, stdout=out, stderr=err, start_new_session=True
        )
        try:
            code = proc.wait(timeout=50)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=10)
            code = "timeout"
    time.sleep(5)
    proofs = {
        name: [json.loads(path.read_text()) for path in WORK.rglob(name + ".json")]
        for name in ("manager", "worker")
    }
    stream = (ROOT / "stream.jsonl").read_text()
    summary = {
        "root": str(ROOT),
        "returncode": code,
        "requests": counts,
        "proofs": proofs,
        "cwd_preserved": "cwd preserved" in stream,
        "background_finished": bool(list(WORK.rglob("background.txt"))),
        "command_survived_timeout": bool(list(WORK.rglob("after-timeout.txt"))),
        "stoppable_command_started": bool(list(WORK.rglob("stop-started.txt"))),
        "command_survived_stop": bool(list(WORK.rglob("after-stop.txt"))),
        "model_calls": 0,
    }
    (ROOT / "summary.json").write_text(json.dumps(summary, indent=2))
    (ROOT / "requests.json").write_text(json.dumps(calls, indent=2))
    print(json.dumps(summary), flush=True)
    assert code == 0 and all(proofs.values()), summary
    assert all(p["docker"] for entries in proofs.values() for p in entries), summary
    assert proofs["manager"][0]["cwd"] == str(WORK / "stage"), summary
    assert summary["cwd_preserved"] and summary["background_finished"], summary
    assert (
        summary["stoppable_command_started"] and not summary["command_survived_stop"]
    ), summary
    assert not summary["command_survived_timeout"], summary
finally:
    executor.close()
    subprocess.run(["docker", "rm", "-f", executor.name], capture_output=True)
    server.shutdown()
