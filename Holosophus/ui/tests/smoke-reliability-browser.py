"""Real built UI + history API + scripted activity, no models or external services."""
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

import pymupdf
import uvicorn
from fastapi.responses import FileResponse, Response, StreamingResponse
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "holosophos"), str(ROOT / "codearkt"), str(ROOT / "scripts")]
from holosophos.ui_api import create_app
from holosophos import research_iterations as history
from cc_activity import ResearchActivity
from claude_agent_sdk.types import AssistantMessage, ToolUseBlock, UserMessage, ToolResultBlock, TaskNotificationMessage, TextBlock

workspace = Path(tempfile.mkdtemp(prefix="ui-smoke-reliability-"))
sid = "research_browser_fixture"
work = workspace / sid
marker = work / ".holosophus_ui"
marker.mkdir(parents=True)
for name, value in {"owner": "ui-test", "orchestrator": "claude_code", "research_profile": "chemistry"}.items():
    (marker / name).write_text(value)
(work / "started_prompt.md").write_text("Browser fixture only")
item = history.start_iteration(work, kind="initial", user_message="Browser fixture", settings={})
records = []
active = True
activity = ResearchActivity()


def append(event):
    records.append(history.append_event(work, item["id"], {"session_id": sid, **event}))


def emit(message):
    for event in activity.consume(message):
        append(event)


def notice(uuid, text):
    return TaskNotificationMessage(subtype="task_notification", data={}, task_id="writer-task",
        tool_use_id="writer-call", status="completed", summary=text, output_file="",
        uuid=uuid, session_id="fixture-sdk")


def pdf(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as doc:
        doc.new_page().insert_text((40, 40), text)
        doc.save(path)


append({"agent_name": "manager", "event_type": "agent_start"})
emit(AssistantMessage(model="fixture", content=[ToolUseBlock(id="writer-call", name="Agent",
    input={"subagent_type": "writer", "description": "Проверка статьи", "run_in_background": True})]))
emit(UserMessage(content=[ToolResultBlock(tool_use_id="writer-call", content="Async agent launched successfully.")],
    tool_use_result={"status": "async_launched", "agentId": "writer-task"}))
emit(notice("limit", "NOTE: this agent stopped at its 100-turn limit before finishing."))
pdf(work / "figures/graph.pdf", "A graph, not the article")
pdf(work / "paper_build_test/test.pdf", "Font test, not the article")
app = create_app(workspace_root=workspace)


@app.get("/agents/session/{session_id}")
def metadata(session_id: str):
    return {"session_id": session_id, "active": active, "can_resume": not active,
            "status": "running" if active else "complete", "research_profile": "chemistry",
            "model_name": "sonnet", "compute_mode": "Local", "approvals": {"sources": False, "plan": False}}


@app.get("/agents/question/{session_id}")
def question(session_id: str):
    return Response(status_code=204)


@app.get("/agents/events/{session_id}")
def events(session_id: str):
    async def stream():
        sent = 0
        while active or sent < len(records):
            while sent < len(records):
                yield json.dumps(records[sent]) + "\n"
                sent += 1
            await asyncio.sleep(.03)
    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.get("/{path:path}")
def frontend(path: str):
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
    time.sleep(.03)

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("UI_TEST_CHROMIUM"), headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1000})
        page.add_init_script("localStorage.setItem('holosophus.ui.clientId','ui-test')")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"{base}/sessions/{sid}/run")
        tab = page.locator('button[data-view-key="writer-call"]')
        expect(tab).to_have_count(1)
        tab.click()
        panel = page.locator('[data-view="writer-call"]')
        expect(panel.locator(".activity-view-heading")).to_contain_text("лимит")
        emit(AssistantMessage(model="fixture", content=[ToolUseBlock(id="send", name="SendMessage",
            input={"to": "writer-task", "message": "Continue"})]))
        emit(UserMessage(content=[ToolResultBlock(tool_use_id="send",
            content='{"success":true,"resumedAgentId":"writer-task"}')]))
        emit(AssistantMessage(model="fixture", parent_tool_use_id="writer-call",
            content=[TextBlock(text="Продолжение после лимита видно в той же вкладке.")]))
        expect(panel.locator(".activity-view-heading")).to_contain_text("работает в фоне")
        expect(panel.get_by_text("Продолжение после лимита видно в той же вкладке.")).to_be_visible()
        expect(tab).to_have_attribute("aria-selected", "true")
        expect(tab).to_have_count(1)
        page.screenshot(path=str(workspace / "resumed-writer.png"))
        page.reload()
        page.locator('button[data-view-key="writer-call"]').click()
        expect(panel.get_by_text("Продолжение после лимита видно в той же вкладке.")).to_have_count(1)
        expect(panel.locator(".activity-view-heading")).to_contain_text("работает в фоне")
        emit(notice("complete", "Статья подготовлена"))
        append({"agent_name": "manager", "event_type": "agent_end"})
        (work / item["output_path"]).write_text("Browser fixture complete")
        history.finish_iteration(work, item["id"], status="complete", result_path=item["output_path"])
        active = False
        expect(panel.locator(".activity-view-heading")).to_contain_text("завершён")
        page.goto(f"{base}/sessions/{sid}/paper")
        expect(page.locator(".tabs").get_by_role("button", name="Статья", exact=True)).to_have_count(0)
        expect(page.locator("iframe.paper-inline-pdf")).to_have_count(0)
        pdf(work / "paper.pdf", "Actual article")
        page.reload()
        iframe = page.locator("iframe.paper-inline-pdf")
        expect(iframe).to_have_attribute("title", "paper.pdf")
        old_src = iframe.get_attribute("src")
        page.screenshot(path=str(workspace / "canonical-paper.png"))
        (work / "paper.pdf").unlink()
        pdf(work / "paper.pdf", "Revised actual article")
        page.reload()
        expect(iframe).to_have_attribute("title", "paper.pdf")
        assert iframe.get_attribute("src") != old_src
        review = {"summary": "Fixture", "quality": {"strengths": ["Проверены данные"], "weaknesses": [], "score": 4},
            "clarity": {"strengths": [], "weaknesses": ["Нужны единицы"], "score": 3},
            "overall": {"score": 5, "reasoning": "Fixture"}, "confidence": {"score": 4},
            "questions": ["Уточнить условия измерения"], "limitations": ["Небольшая выборка"],
            "format_issues": ["Подпись рисунка"]}
        (work / "review.json").write_text(json.dumps(review, ensure_ascii=False))
        page.goto(f"{base}/sessions/{sid}/review")
        for text in ("Проверены данные", "Нужны единицы", "Уточнить условия измерения", "Небольшая выборка", "Подпись рисунка"):
            expect(page.get_by_text(text, exact=True)).to_be_visible()
        expect(page.locator(".tab-header").filter(has=page.get_by_role("heading", name="Рецензия", exact=True))).to_contain_text("5")
        page.screenshot(path=str(workspace / "nested-review.png"))
        assert not errors, errors
        browser.close()
        print(json.dumps({"ok": True, "artifacts": str(workspace), "model_calls": 0,
            "checks": ["live_resume_same_tab", "reload_without_duplicates", "final_status", "no_arbitrary_pdf", "canonical_pdf_reload", "native_review_schema"]}))
finally:
    active = False
    server.should_exit = True
    thread.join(timeout=5)
