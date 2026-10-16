"""The feedback label distinguishes launch choices from actual settings; no LLM."""
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
from holosophos.ui_api import create_app
from holosophos import research_iterations, research_session

with tempfile.TemporaryDirectory(prefix="requested-settings-") as directory:
    work = Path(directory) / "research_finished"
    marker = work / ".holosophus_ui"
    marker.mkdir(parents=True)
    (marker / "owner").write_text("ui-test")
    (marker / "orchestrator").write_text("claude_code")
    (work / "started_prompt.md").write_text("Completed fixture")
    item = research_iterations.start_iteration(work, kind="initial", user_message="Fixture", settings={})
    (work / item["output_path"]).write_text("# Готовый результат")
    research_iterations.finish_iteration(work, item["id"], status="complete", result_path=item["output_path"])
    research_session.save_run_status(work, "complete")
    state = {}
    app = create_app(workspace_root=Path(directory))

    @app.get("/agents/session/{sid}")
    def metadata(sid: str):
        return {"session_id": sid, "active": False, "status": "complete", "can_resume": True,
                "research_profile": "chemistry", "model_name": "opus", "model_label": "Claude Opus",
                "effort": "high", "compute_mode": "Local",
                "approvals": {"sources": False, "plan": True}, **state}

    @app.get("/agents/question/{sid}")
    def question(sid: str):
        return Response(status_code=204)

    @app.post("/agents/manager")
    def no_research():
        raise AssertionError("Display test must never launch research")

    @app.get("/{path:path}")
    def frontend(path: str):
        if path.startswith("agents/"):
            return Response(status_code=404)
        file = ROOT / "ui/dist" / path
        return FileResponse(file if file.is_file() else ROOT / "ui/dist/index.html")

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(.05)
    errors = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=os.environ.get("UI_TEST_CHROMIUM"), headless=True)
            requested = {"model_name": "deepseek-v4-pro", "model_label": "DeepSeek V4 Pro (0813)",
                         "effort": "max", "compute_mode": "GPU"}
            for choice in (requested, None, "legacy"):
                state.clear()
                if choice != "legacy":
                    state["requested_launch"] = choice
                page = browser.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.add_init_script("localStorage.setItem('holosophus.ui.clientId','ui-test')")
                page.goto(base + "/sessions/" + work.name + "/run")
                page.locator(".feedback-toggle").click()
                label = page.locator(".feedback-heading p")
                if choice == requested:
                    expect(label).to_have_text("Продолжение с прежними настройками: Химия, DeepSeek V4 Pro (0813) · Максимальный, Вычисления: удалённо.")
                else:
                    expect(label).to_have_text("Продолжение с прежними настройками: Химия, Claude Opus · Высокий, Вычисления: локально.")
                page.reload()
                page.locator(".feedback-toggle").click()
                expect(page.locator(".feedback-heading p")).to_contain_text("DeepSeek" if choice == requested else "Claude Opus")
                page.close()
            assert not errors, errors
            browser.close()
            print("Requested, actual and legacy feedback labels survive reload: passed")
    finally:
        server.should_exit = True
        thread.join(timeout=5)
