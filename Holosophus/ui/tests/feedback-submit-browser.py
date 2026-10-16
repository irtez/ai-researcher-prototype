"""Offline UI check: real history API, synthetic runner metadata, no model."""

import os
import asyncio
import socket
import sys
from pathlib import Path
import tempfile
import threading
import time
import json
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi import Request
import uvicorn
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "holosophos"), str(ROOT / "codearkt"), str(ROOT / "scripts")]
from holosophos import research_iterations as history  # noqa: E402
from holosophos.ui_api import create_app  # noqa: E402

root = ROOT
profile = os.environ.get("UI_TEST_PROFILE", "general")
workspace = Path(tempfile.mkdtemp(prefix="iteration-ui-"))
work = workspace / "ui-history-demo"
work.mkdir()
marker = work / ".holosophus_ui"
marker.mkdir()
(marker / "owner").write_text("ui-test")
(marker / "orchestrator").write_text("claude_code")
(work / "started_prompt.md").write_text("# Синтетическая проверка интерфейса")
(work / "mle").mkdir()
(work / "mle/data.csv").write_text("name,mass\nethanol,46.069\n")
(work / "mle/calculation.log").write_text("synthetic calculation log")
(work / "mle/results").mkdir()
(work / "mle/results/metrics.json").write_text('{"mass":46.069}')
(work / ".holosophus_ctl").mkdir()
(work / ".holosophus_ctl/internal-control.json").write_text('{"status":"pending"}')
(work / ".holosophus_ctl/session.json").write_text('{"session_id":"fixture"}')
(work / "inputs").mkdir()
(work / "inputs/data.csv").write_bytes(b"original")


def event(item, kind, agent="manager", text=None):
    history.append_event(
        work,
        item["id"],
        {
            "session_id": work.name,
            "agent_name": agent,
            "event_type": kind,
            "content": text,
        },
    )


source_decision = None


def plan(item, text):
    global source_decision
    path = work / item["plan_directory"] / "actions.md"
    path.write_text(text)
    decision = {
        "iteration_id": item["id"],
        "qid": "q" + item["id"],
        "stage": "plan",
        "status": "approved",
        "resolution": "manual",
        "resolved_at": time.time(),
        "text": "Подходит",
        "document_path": str(path.relative_to(work)),
        "document_text": text,
    }
    if source_decision is None:
        source_path = item["sources_directory"] + "/inputs.md"
        (work / source_path).write_text("# Источники\nЗаданные пользователем данные.")
        source_decision = {**decision, "qid": "sources-1", "stage": "sources",
                           "document_path": source_path,
                           "document_text": (work / source_path).read_text()}
    history.record_approvals(
        work, item["id"], {"sources": source_decision, "plan": decision},
        {source_decision["qid"]: source_decision, decision["qid"]: decision}
    )


def finish(item, text):
    (work / item["output_path"]).write_text(text)
    event(item, "agent_end")
    history.finish_iteration(
        work,
        item["id"],
        status="complete",
        answer="Готово, документ сохранён.",
        result_path=item["output_path"],
    )


first = history.start_iteration(work, kind="initial", user_message="Исходная задача", settings={"input_files": ["inputs/data.csv"]})
plan(first, "# План\nСохранить результат.")
event(first, "agent_start")
event(first, "output", text="Исходная задача завершена.")
finish(first, "# Готовый результат\nЧисло: 46.069")
active = False
pending = None
released = threading.Event()
received_requests = []
reauth = os.environ.get("UI_TEST_REAUTH") == "1"
reject_next = False
app = create_app(workspace_root=workspace)
upload_requests = []
lost_upload_response = os.environ.get("UI_TEST_UPLOAD_LOST_RESPONSE") == "1"
reauth_upload = os.environ.get("UI_TEST_UPLOAD_REAUTH") == "1"


@app.middleware("http")
async def uploads(request, call_next):
    if request.method == "POST" and request.url.path.endswith("/inputs"):
        upload_requests.append(request.url.query)
        if reauth_upload and len(upload_requests) <= 2:
            return JSONResponse({"detail": "Synthetic rejected retry"}, status_code=401 if len(upload_requests) == 1 else 413)
        response = await call_next(request)
        if lost_upload_response and len(upload_requests) == 1 and response.status_code == 200:
            return JSONResponse({"detail": "Synthetic lost upload response"}, status_code=503)
        return response
    return await call_next(request)



@app.post("/agents/manager")
async def feedback(request: Request):
    global active
    body = await request.json()
    received_requests.append(body)
    assert body["resume"] is True
    if reauth and len(received_requests) == 1:
        return JSONResponse({"detail": "Synthetic expired login"}, status_code=401)
    if reject_next:
        return JSONResponse({"detail": "Synthetic refusal"}, status_code=503)
    assert body["input_files"] and body["feedback_upload_id"]
    active = True
    item = history.start_iteration(work, kind="feedback", user_message=body["messages"][-1]["content"],
        settings={"input_files": body["input_files"], "feedback_upload_id": body["feedback_upload_id"]})
    def record(kind, text=None):
        return history.append_event(work, item["id"], {"session_id":work.name,"agent_name":"manager", "event_type":kind,"content":text,"output_mode":"message"})
    async def stream():
        global active
        yield json.dumps(record("session_running")) + "\n"
        yield json.dumps(record("agent_start")) + "\n"
        while not released.is_set():
            await asyncio.sleep(.02)
        yield json.dumps(record("output", "Доработка идёт без перезагрузки."), ensure_ascii=False) + "\n"
        await asyncio.sleep(.1)
        (work/item["output_path"]).write_text("# Доработанный результат\nЧисло: 46.07")
        end = record("agent_end")
        history.finish_iteration(work, item["id"], status="complete", result_path=item["output_path"])
        active = False
        yield json.dumps(end) + "\n"
    return StreamingResponse(stream(),media_type="application/x-ndjson")

@app.post("/agents/login")
def login():
    return {"token": "fixture-token", "username": "ui-test"}

@app.get("/agents/session/{session_id}")
def metadata(session_id: str):
    return {
        "session_id": session_id,
        "research_profile": profile,
        "model_name": "sonnet",
        "compute_mode": "Local",
        "approvals": {
            "sources": False,
            "plan": True,
            "auto_approve_after_seconds": None,
        },
        "active": active,
        "can_resume": not active,
        "status": "running" if active else "complete",
    }


@app.get("/agents/question/{session_id}")
def question(session_id: str):
    return pending if pending else Response(status_code=204)


@app.api_route("/agents/{rest:path}", methods=["GET", "POST"])
def no_model(rest: str):
    return JSONResponse({"detail": "No model backend in this test"}, status_code=404)


@app.get("/capacity")
def capacity():
    return {
        "sessions": {"active": 0, "limit": 1, "queued": 0},
        "host": {
            "load1": 0,
            "load5": 0,
            "load15": 0,
            "cpu_count": 1,
            "mem_used_pct": 0,
        },
    }


@app.get("/{path:path}")
def frontend(path: str):
    file = root / "ui/dist" / path
    return FileResponse(file if file.is_file() else root / "ui/dist/index.html")


listener = socket.socket()
listener.bind(("127.0.0.1", 0))
base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
thread.start()
for _ in range(100):
    if server.started:
        break
    time.sleep(0.05)
assert server.started
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=os.environ.get("UI_TEST_CHROMIUM"), headless=True
        )
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.add_init_script("localStorage.setItem('holosophus.ui.clientId','ui-test')")
        page_errors = []
        capacity_requests = []
        page.on(
            "request",
            lambda request: (
                capacity_requests.append(request.url)
                if request.url.endswith("/capacity")
                else None
            ),
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.goto(base_url + "/sessions/ui-history-demo/run")
        selector = page.get_by_label("Итерация", exact=True)
        expect(selector).to_have_value("0001")
        page.locator(".feedback-toggle").click()
        page.locator(".feedback-editor textarea").fill("Округлить до двух знаков")
        file_input = page.locator('.feedback-editor input[type="file"]')
        file_input.set_input_files({"name": "data.csv", "mimeType": "text/csv", "buffer": b"corrected"})
        page.locator(".feedback-submit-row button.start").click()
        if reauth_upload:
            expect(page.locator(".login-modal")).to_be_visible()
            page.locator('.login-form input[type=text]').fill("ui-test")
            page.locator('.login-form input[type=password]').fill("fixture-password")
            page.locator('.login-form button[type=submit]').click()
            expect(page.locator(".feedback-submit-row .operator-error")).to_contain_text("Synthetic rejected retry")
            expect(page.locator(".feedback-editor .input-file-list")).to_contain_text("data.csv")
            assert not received_requests
            page.locator(".feedback-submit-row button.start").click()
        if lost_upload_response:
            expect(page.locator(".feedback-submit-row .operator-error")).to_be_visible()
            assert not received_requests
            expect(page.locator(".feedback-editor .input-file-list")).to_contain_text("data.csv")
            page.locator(".feedback-submit-row button.start").click()
        if reauth:
            expect(page.locator(".login-modal")).to_be_visible()
            page.locator('.login-form input[type=text]').fill("ui-test")
            page.locator('.login-form input[type=password]').fill("fixture-password")
            page.locator('.login-form button[type=submit]').click()
        expect(selector).to_have_value("0002", timeout=7000)
        assert len(received_requests) == (2 if reauth else 1)
        expect(page.locator(".activity-empty")).to_contain_text("Ожидается первый ответ менеджера")
        expect(page.locator(".iteration-status")).to_contain_text("выполняется")
        expect(page.locator(".feedback-editor")).to_have_count(0)
        # Native stream remains open and silent until the fixture allows a response.
        released.set()
        expect(page.locator(".activity-message")).to_contain_text("Доработка идёт без перезагрузки.", timeout=7000)
        expect(page.locator(".iteration-result")).to_contain_text("46.07", timeout=7000)
        expect(page.locator(".feedback-toggle")).to_be_enabled(timeout=7000)
        assert len(upload_requests) == (3 if reauth_upload else 2 if lost_upload_response else 1)
        if lost_upload_response:
            assert upload_requests[0] == upload_requests[1]
        assert (work / "inputs/data.csv").read_bytes() == b"original"
        assert len(list((work / "inputs").iterdir())) == 2
        expect(page.locator(".iteration-inputs a")).to_have_text("data.csv")
        saved_path = received_requests[-1]["input_files"][0]
        assert (work / saved_path).read_bytes() == b"corrected"
        assert saved_path != "inputs/data.csv"
        selector.select_option("0001")
        expect(page.locator(".iteration-inputs a")).to_have_attribute("title", "inputs/data.csv")
        selector.select_option("0002")
        expect(page.locator(".iteration-inputs a")).to_have_attribute("title", saved_path)
        page.locator(".feedback-toggle").click()
        expect(page.locator(".feedback-editor textarea")).to_have_value("")
        expect(page.locator(".feedback-editor .input-file-list")).to_have_count(0)
        reject_next = True
        page.locator(".feedback-editor textarea").fill("Сохранить замечание при отказе сервера")
        page.locator('.feedback-editor input[type="file"]').set_input_files({"name": "data.csv", "mimeType": "text/csv", "buffer": b"second correction"})
        page.locator(".feedback-submit-row button.start").click()
        expect(page.locator(".feedback-submit-row .operator-error")).to_be_visible()
        expect(page.locator(".feedback-editor textarea")).to_have_value("Сохранить замечание при отказе сервера")
        expect(page.locator(".feedback-submit-row button.start")).to_be_enabled()
        assert len(received_requests) == (3 if reauth else 2)
        uploads_before_retry = len(upload_requests)
        reject_next = False
        page.locator(".feedback-submit-row button.start").click()
        expect(selector).to_have_value("0003", timeout=7000)
        expect(page.locator(".iteration-status")).to_contain_text("завершено", timeout=7000)
        assert len(upload_requests) == uploads_before_retry
        assert received_requests[-1]["input_files"] == received_requests[-2]["input_files"]
        assert len(list((work / "inputs").iterdir())) == 3
        assert (work / saved_path).read_bytes() == b"corrected"
        assert not page_errors, page_errors
        print(json.dumps({"browser":"PASS","profile":profile,"reauth":reauth,"lost_upload_response":lost_upload_response,
            "upload_reauth_error_visible":reauth_upload,
            "accepted_feedbacks":2,"rejected_draft_preserved":True,"uploads":len(upload_requests),"old_versions_preserved":True,
            "reloads":0,"page_errors":page_errors,"workspace":str(workspace)}))
        browser.close()
finally:
    released.set()
    server.should_exit = True
    thread.join(timeout=5)
