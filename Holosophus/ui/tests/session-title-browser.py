"""Real UI/API title refresh against local files; no runner or model endpoint."""

import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

import uvicorn
from fastapi.responses import FileResponse, Response
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "holosophos"), str(ROOT / "codearkt")]
from holosophos import research_session, research_iterations as history
from holosophos.ui_api import create_app

profile = os.environ.get("UI_TEST_PROFILE", "general")
with tempfile.TemporaryDirectory(prefix="ui-title-") as directory:
    workspace = Path(directory)
    work = workspace / research_session.new_session_id()
    work.mkdir()
    research_session.initialize_display_metadata(work, "Сравни две молекулы")
    research_session.save_research_profile(work, profile)
    research_session.save_session_model(work, "sonnet")
    marker = work / ".holosophus_ui"
    (marker / "owner").write_text("title-test")
    (marker / "orchestrator").write_text("claude_code")
    (marker / "title_attempted").write_text(str(time.time()))
    (work / "started_prompt.md").write_text("Сравни две молекулы")
    item = history.start_iteration(
        work, kind="initial", user_message="Task", settings={}
    )
    (work / item["output_path"]).write_text("Synthetic result")
    history.finish_iteration(
        work, item["id"], status="complete", result_path=item["output_path"]
    )
    app = create_app(workspace_root=workspace)

    @app.get("/agents/session/{sid}")
    def metadata(sid: str):
        return {
            "session_id": sid,
            "active": False,
            "can_resume": True,
            "status": "complete",
            "research_profile": profile,
            "model_name": "sonnet",
            "compute_mode": "Local",
            "approvals": {"sources": False, "plan": False},
        }

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
    thread = threading.Thread(
        target=lambda: server.run(sockets=[listener]), daemon=True
    )
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path=os.environ.get("UI_TEST_CHROMIUM"), headless=True
            )
            page = browser.new_page()
            page.add_init_script(
                "localStorage.setItem('holosophus.ui.clientId','title-test')"
            )
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.route(
                "**/*",
                lambda route: (
                    route.continue_()
                    if route.request.method in {"GET", "HEAD", "OPTIONS"}
                    and route.request.url.startswith(base_url)
                    else route.abort()
                ),
            )
            page.goto(base_url + "/sessions/" + work.name + "/run")
            expect(page.locator(".session-title")).to_have_text("Сравни две молекулы")
            expect(page.locator(".iteration-result")).to_contain_text(
                "Synthetic result"
            )
            original_url = page.url
            (marker / "title").write_text("Сравнение молекулярных масс")
            (marker / "title_attempted").write_text("done")
            expect(page.locator(".session-title")).to_have_text(
                "Сравнение молекулярных масс", timeout=10000
            )
            assert page.url == original_url and not errors
            page.reload()
            expect(page.locator(".session-title")).to_have_text(
                "Сравнение молекулярных масс"
            )
            browser.close()
            print(
                json.dumps(
                    {
                        "profile": profile,
                        "title_refresh_without_reload": True,
                        "id_unchanged": True,
                        "model_requests": 0,
                    }
                )
            )
    finally:
        server.should_exit = True
        thread.join(timeout=5)
