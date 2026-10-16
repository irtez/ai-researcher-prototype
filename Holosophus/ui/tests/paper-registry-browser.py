"""Registered article states through the real API and built UI; no model calls."""
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

import uvicorn
from fastapi.responses import FileResponse, JSONResponse, Response
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "holosophos"), str(ROOT / "codearkt")]
from holosophos.ui_api import create_app
from research_paper_registry import PaperRegistry, file_sha256

workspace = Path(tempfile.mkdtemp(prefix="paper-registry-browser-"))
registry = PaperRegistry(workspace)


def session(name, files):
    work = workspace / name
    files = {".holosophus_ui/owner": "paper-test",
             ".holosophus_ui/orchestrator": "claude_code",
             "started_prompt.md": "Проверка документа", **files}
    for relative, content in files.items():
        path = work / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return work


article = session("registered", {
    "results/статья.pdf": "%PDF-1.4 fixture", "results/статья.tex": "Source",
    "paper.pdf": "Old paper", "review.json": '{"overall": {"score": 6}}',
    "review.md": "# Проверенная рецензия\nМетодика описана.",
})
registry.register(article.name, "check-1", "results/статья.tex", "results/статья.pdf")
registry.record_review(article.name, "results/статья.pdf", "review.json", "review.md",
                       expected_check_run_id="check-1", pdf_sha256=file_sha256(article / "results/статья.pdf"))
missing = session("missing", {"paper.pdf": "Old paper", "review.md": "Unassociated review"})
registry.register(missing.name, "check-2", "results/new.tex", "results/new.pdf")
invalid = session("invalid", {})
registry.register(invalid.name, "check-3")
(workspace / ".paper_registry" / f"{hashlib.sha256(b'invalid').hexdigest()}.json").write_text("{invalid")
session("legacy", {"paper/paper.pdf": "%PDF-1.4 fixture", "paper/review.md": "# Старая рецензия"})
session("csv", {"mle/results/data.csv": "x,y\n1,2", "figures/chart.png": "image"})
app = create_app(workspace_root=workspace)


@app.get("/agents/session/{session_id}")
def metadata(session_id: str):
    return {"session_id": session_id, "active": False, "can_resume": False, "status": "complete"}


@app.get("/agents/question/{session_id}")
def question(session_id: str):
    return Response(status_code=204)


@app.api_route("/agents/{path:path}", methods=["GET", "POST"])
def no_model(path: str):
    return JSONResponse({"detail": "No model in this check"}, status_code=404)


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
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.add_init_script("localStorage.setItem('holosophus.ui.clientId','paper-test')")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"{base}/sessions/registered/paper")
        expect(page.locator("iframe.paper-inline-pdf")).to_have_attribute("title", "results/статья.pdf")
        expect(page.get_by_text("Проверка статьи выполняется", exact=False)).to_be_visible()
        registry.update_status(article.name, "check-1", "ready", checked_fingerprints={
            key: file_sha256(article / f"results/статья.{key}") for key in ("tex", "pdf")
        })
        page.reload()
        expect(page.get_by_text("Проверка статьи завершена.", exact=True)).to_be_visible()
        (article / "results/статья.tex").write_text("Revised source after completed checks")
        page.reload()
        expect(page.get_by_text("После проверки статья была изменена.", exact=False)).to_be_visible()
        expect(page.get_by_text("Проверка статьи завершена.", exact=True)).to_have_count(0)
        expect(page.locator("iframe.paper-inline-pdf")).to_have_attribute("title", "results/статья.pdf")
        assert registry.read(article.name)["status"] == "ready"
        page.screenshot(path=str(workspace / "changed-after-check.png"))
        page.goto(f"{base}/sessions/registered/review")
        expect(page.get_by_text("Оценка 6", exact=False)).to_be_visible()
        (article / "review.json").write_text("{invalid")
        page.reload()
        expect(page.get_by_role("heading", name="Проверенная рецензия", exact=True)).to_be_visible()
        page.goto(f"{base}/sessions/missing/paper")
        expect(page.locator(".tabs").get_by_role("button", name="Статья", exact=True)).to_be_visible()
        expect(page.get_by_text("PDF статьи ещё не сформирован", exact=False)).to_be_visible()
        expect(page.get_by_text("Ожидаемый файл: results/new.pdf", exact=True)).to_be_visible()
        expect(page.locator("iframe.paper-inline-pdf")).to_have_count(0)
        expect(page.locator(".tabs").get_by_role("button", name="Рецензия", exact=True)).to_have_count(0)
        page.screenshot(path=str(workspace / "missing.png"))
        page.goto(f"{base}/sessions/invalid/paper")
        expect(page.get_by_text("Не удалось прочитать сведения о статье", exact=False)).to_be_visible()
        expect(page.locator("iframe.paper-inline-pdf")).to_have_count(0)
        page.goto(f"{base}/sessions/legacy/paper")
        expect(page.locator("iframe.paper-inline-pdf")).to_have_attribute("title", "paper/paper.pdf")
        page.goto(f"{base}/sessions/legacy/review")
        expect(page.get_by_role("heading", name="Старая рецензия", exact=True)).to_be_visible()
        page.goto(f"{base}/sessions/csv/paper")
        expect(page.locator(".tabs").get_by_role("button", name="Статья", exact=True)).to_have_count(0)
        assert not errors, errors
        browser.close()
    print(json.dumps({"ok": True, "model_calls": 0, "artifacts": str(workspace),
                      "checks": ["registered", "changed_after_check", "missing", "invalid", "review_fallback", "legacy", "csv"]}))
finally:
    server.should_exit = True
    thread.join(timeout=5)
