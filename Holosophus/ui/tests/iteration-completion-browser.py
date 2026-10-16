"""Real UI/API WebSocket and polling fallback; synthetic history, zero LLM calls.

UI_TEST_URL=http://localhost:5062 also exercises the deployed nginx proxy.
Without it, serve the same built UI and API in a temporary local server.
"""

import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import threading
import time
import uuid

import uvicorn
from fastapi.responses import FileResponse
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "holosophos"), str(ROOT / "codearkt")]
from holosophos import research_iterations as history, research_session  # noqa: E402
from holosophos.ui_api import create_app  # noqa: E402

base_url = os.environ.get("UI_TEST_URL")
workspace = ROOT / "workdir" if base_url else Path(tempfile.mkdtemp(prefix="ws-ui-"))
server = None
if not base_url:
    app = create_app(workspace_root=workspace)

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
    assert server.started

checks = []
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get("UI_TEST_CHROMIUM"), headless=True)
        for profile, fallback in [("general", False), ("chemistry", False), ("general", True)]:
            work = workspace / ("verify-ws-" + uuid.uuid4().hex)
            work.mkdir()
            research_session.check_session_owner(work, "ws-browser-check", claim=True)
            research_session.save_research_profile(work, profile)
            (work / ".holosophus_ui/orchestrator").write_text("claude_code")
            initial = history.start_iteration(work, kind="initial", user_message="Первичная задача", settings={})
            (work / initial["output_path"]).write_text("# Первый результат")
            history.finish_iteration(work, initial["id"], status="complete", result_path=initial["output_path"])
            item = history.start_iteration(work, kind="feedback", user_message="Доработка", settings={})
            history.update_iteration(work, item["id"], status="running")
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            messages, connections, errors, refreshes, intercepted = [], [], [], [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script("localStorage.setItem('holosophus.ui.clientId','ws-browser-check')")

            def route(request):
                url = request.request.url
                assert request.request.method == "GET", "No commands/model calls are allowed"
                if "/agents/session/" in url:
                    active = bool(history.list_iterations(work)["active_iteration_id"])
                    request.fulfill(json={"session_id": work.name, "active": active, "can_resume": False,
                                          "status": "running" if active else "complete", "research_profile": profile,
                                          "model_name": "sonnet", "compute_mode": "Local", "approvals": {}})
                elif "/agents/question/" in url:
                    request.fulfill(status=204)
                else:
                    request.fulfill(status=404, json={"detail": "No model backend in this test"})

            page.route("**/agents/**", route)
            page.on("request", lambda req: refreshes.append(time.monotonic()) if req.url.endswith("/iterations") else None)
            if fallback:
                def close_ws(ws):
                    connections.append(ws.url)
                    intercepted.append(ws)
                page.route_web_socket("**/ui/ws/**", close_ws)
            else:
                def observe(ws):
                    connections.append(ws.url)
                    ws.on("framereceived", lambda frame: messages.append(json.loads(frame)))
                page.on("websocket", observe)
            try:
                page.goto(f"{base_url}/sessions/{work.name}/run")
                selector = page.get_by_label("Итерация", exact=True)
                expect(selector).to_have_value("0002")
                page.wait_for_function("document.querySelector('.iteration-selector select') !== null")
                for _ in range(50):
                    if connections:
                        break
                    page.wait_for_timeout(100)
                assert len(connections) == 1, connections
                if fallback:
                    intercepted[0].close(code=1011, reason="Synthetic disconnect")
                selector.select_option("0001")
                page.wait_for_timeout(1000)  # subscription remains bound to running 0002
                before = len(refreshes)
                (work / item["output_path"]).write_text("# WebSocket результат")
                history.finish_iteration(work, item["id"], status="complete", result_path=item["output_path"])
                if not fallback:
                    for _ in range(35):
                        if messages:
                            break
                        page.wait_for_timeout(100)
                    assert messages == [{"iteration_id": "0002", "status": "complete"}], messages
                page.wait_for_timeout(3500)
                assert len(refreshes) > before, "HTTP state did not refresh"
                expect(selector).to_have_value("0001")
                selector.select_option("0002")
                expect(page.get_by_role("heading", name="WebSocket результат")).to_be_visible(timeout=7000)
                assert len(connections) == 1, "Unexpected reconnect"
                assert not errors, errors
                checks.append({"profile": profile, "fallback": fallback, "connections": len(connections),
                               "notification": messages, "selection_preserved": True, "result_visible": True})
            finally:
                page.close()
                shutil.rmtree(work)
        browser.close()
    print(json.dumps({"checks": checks, "model_requests": 0}, ensure_ascii=False))
finally:
    if server:
        server.should_exit = True
        thread.join(5)
        shutil.rmtree(workspace)
