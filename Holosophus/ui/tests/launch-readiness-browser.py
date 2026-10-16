"""Real UI/history API with deliberately slow admission; no model backend."""

import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

import uvicorn
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "holosophos"), str(ROOT / "codearkt")]
from holosophos.ui_api import create_app  # noqa: E402
from holosophos import research_iterations as history, research_session  # noqa: E402

workspace = Path(tempfile.mkdtemp(prefix="ui-launch-readiness-"))
profile = os.environ.get("UI_TEST_PROFILE", "general")
app = create_app(workspace_root=workspace)
admitted = False
history_broken = False
early_reads = []
manager_requests = []


@app.middleware("http")
async def check_reads(request, call_next):
    if request.method == "GET" and (
        request.url.path.startswith("/ui/sessions/")
        or request.url.path.startswith("/agents/session/")
    ):
        if not admitted:
            early_reads.append(request.url.path)
        if history_broken and request.url.path.endswith("/iterations"):
            return JSONResponse({"detail": "Real history failure"}, status_code=500)
    return await call_next(request)


@app.post("/agents/manager")
async def manager(request: Request):
    payload = await request.json()
    manager_requests.append(payload)
    sid = payload["session_id"]
    assert payload["research_profile"] == profile
    assert payload["input_files"] == ["inputs/data.csv"]
    assert (workspace / sid / "inputs/data.csv").read_bytes() == b"x,y\n1,2\n"
    paths = research_session.input_paths(workspace / sid, payload["input_files"])
    assert "inputs/data.csv" in research_session.with_input_files("Readiness test", paths)

    async def events():
        global admitted
        await asyncio.sleep(3)
        work = workspace / sid
        marker = work / ".holosophus_ui"
        marker.mkdir(parents=True, exist_ok=True)
        (marker / "owner").write_text("ui-test")
        (marker / "orchestrator").write_text("claude_code")
        (work / "started_prompt.md").write_text("Readiness test")
        item = history.start_iteration(work, kind="initial", user_message="Readiness test", settings={})
        admitted = True
        event = {"session_id": sid, "agent_name": "manager", "event_type": "agent_start", "content": None}
        history.append_event(work, item["id"], event)
        yield json.dumps(event) + "\n"
        await asyncio.sleep(1)
        (work / item["output_path"]).write_text("Synthetic result")
        history.finish_iteration(work, item["id"], status="complete", result_path=item["output_path"])
        yield json.dumps({**event, "event_type": "agent_end"}) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson")


@app.get("/agents/session/{sid}")
def metadata(sid: str):
    if not admitted:
        return Response(status_code=404)
    return {"session_id": sid, "active": False, "can_resume": True,
            "status": "complete", "research_profile": profile, "model_name": "sonnet",
            "compute_mode": "Local", "approvals": {"sources": False, "plan": False}}


@app.get("/agents/question/{sid}")
def question(sid: str):
    return Response(status_code=204)


@app.get("/{path:path}")
def frontend(path: str):
    file = ROOT / "ui/dist" / path
    return FileResponse(file if file.is_file() else ROOT / "ui/dist/index.html")


listener = socket.socket()
listener.bind(("127.0.0.1", 0))
base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
thread.start()
for _ in range(100):
    if server.started:
        break
    time.sleep(.05)
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("UI_TEST_CHROMIUM"), headless=True)
        page = browser.new_page()
        page.add_init_script("localStorage.setItem('holosophus.ui.clientId','ui-test')")
        page.goto(base_url)
        page.locator("textarea").first.fill("Readiness test")
        file_input = page.locator('input[type="file"]')
        expect(file_input).to_be_attached()
        file_input.set_input_files({"name": "data.csv", "mimeType": "text/csv", "buffer": b"x,y\n1,2\n"})
        for label in ("Химия", "Компьютерные науки", "Химия" if profile == "chemistry" else "Компьютерные науки"):
            page.locator(".profile-selector").get_by_role("button", name=label, exact=True).click()
            expect(page.locator(".input-file-list")).to_contain_text("data.csv")
        # Rejected uploads must leave the form usable and never call the manager.
        file_input.set_input_files({"name": "invalid.exe", "mimeType": "application/octet-stream", "buffer": b"invalid"})
        page.get_by_role("button", name="Старт", exact=True).click()
        expect(page.locator(".launch-error")).to_be_visible()
        assert not manager_requests
        assert not list(workspace.glob("*/inputs/*")), "failed batch left uploaded files"
        page.locator(".input-file").filter(has_text="invalid.exe").get_by_role("button").click()
        page.get_by_role("button", name="Старт", exact=True).click()
        page.wait_for_url("**/sessions/**/run")
        page.wait_for_timeout(1000)
        expect(page.locator(".workbench")).not_to_contain_text("Не удалось обновить историю")
        expect(page.locator("body")).not_to_contain_text("Доработка недоступна")
        assert not early_reads, early_reads
        expect(page.locator(".iteration-result")).to_contain_text("Synthetic result", timeout=12000)
        history_broken = True
        expect(page.locator(".workbench")).to_contain_text("Не удалось обновить историю", timeout=8000)
        assert len(manager_requests) == 1
        print(json.dumps({"browser": "PASS", "profile": profile, "attachments_preserved_and_uploaded": True,
                          "upload_failure_blocks_start": True, "early_reads": early_reads,
                          "real_error_visible": True, "model_requests": 0}))
        browser.close()
finally:
    server.should_exit = True
    thread.join(timeout=5)
