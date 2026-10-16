"""Real API + built UI regression for plot placement and Markdown images, no LLM."""
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
from holosophos import research_iterations as history
from holosophos.ui_api import create_app

workspace = Path(tempfile.mkdtemp(prefix="experiment-images-"))
work = workspace / "csv-fixture"
marker = work / ".holosophus_ui"
marker.mkdir(parents=True)
(marker / "owner").write_text("image-test")
(marker / "orchestrator").write_text("claude_code")
(work / "started_prompt.md").write_text("Проверка графиков")
item = history.start_iteration(work, kind="initial", user_message="CSV example", settings={})
plot = work / "mle/results/график калибровки.svg"
plot.parent.mkdir(parents=True)
plot.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="1000"><rect width="1400" height="1000" fill="teal"/></svg>')
# These files must not create an article tab either.
for name in ("inputs/article.pdf", "figures/chart.png", "draft/paper.tex", "references.bib"):
    path = work / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("fixture")
content = ('# Результат\n\n![График калибровки](../../mle/results/график%20калибровки.svg)\n\n'
           '[Таблица](../../mle/results/values.csv)\n\n'
           '![Нет файла](../../mle/results/missing.png)\n\n'
           '![Недопустимый адрес](javascript:alert%281%29)\n\n'
           '`![Это код](unrequested.png)`\n')
(work / "mle/results/values.csv").write_text("x,y\n1,2\n")
(work / item["output_path"]).write_text(content)
history.finish_iteration(work, item["id"], status="complete", result_path=item["output_path"])
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

failures = []
checks = []
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("UI_TEST_CHROMIUM"), headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.add_init_script("localStorage.setItem('holosophus.ui.clientId','image-test')")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def check(name, action):
            try:
                action()
                checks.append(name)
            except AssertionError as error:
                failures.append(f"{name}: {error}")

        def no_article():
            page.goto(f"{base}/sessions/csv-fixture/run")
            expect(page.get_by_role("heading", name="Результат", exact=True).first).to_be_visible()
            expect(page.locator(".tabs").get_by_role("button", name="Статья", exact=True)).to_have_count(0)
            # A stale bookmarked /paper URL must not restore a phantom tab.
            page.goto(f"{base}/sessions/csv-fixture/paper")
            expect(page.get_by_role("heading", name="Результат", exact=True).first).to_be_visible()
            expect(page.locator(".tabs").get_by_role("button", name="Статья", exact=True)).to_have_count(0)

        def inline_image():
            page.goto(f"{base}/sessions/csv-fixture/run")
            image = page.locator('.workbench .markdown-preview img[alt="График калибровки"]').first
            expect(image).to_be_visible()
            expect(image).to_have_js_property("naturalWidth", 1400)
            assert image.evaluate("el => el.clientWidth <= el.closest('.markdown-preview').clientWidth")
            page.set_viewport_size({"width": 390, "height": 900})
            assert image.evaluate("el => el.clientWidth <= el.closest('.markdown-preview').clientWidth")
            page.set_viewport_size({"width": 1440, "height": 1000})
            with page.expect_popup() as popup:
                image.click()
            opened = popup.value
            opened.wait_for_load_state()
            assert "raw?path=mle%2Fresults%2F" in opened.url
            assert opened.request.get(opened.url).status == 200
            opened.close()
            expect(page.locator('.markdown-preview a').filter(has_text="Таблица").first).to_have_attribute("href", "/ui/artifacts/csv-fixture/raw?path=mle%2Fresults%2Fvalues.csv")
            expect(page.locator('img[alt="Недопустимый адрес"]')).to_have_count(0)
            expect(page.locator('img[src*="unrequested"]')).to_have_count(0)
            expect(page.get_by_role("link", name="Нет файла", exact=True)).to_be_visible()
            page.screenshot(path=str(workspace / "inline.png"))
            page.locator('.workbench .artifact-documents').get_by_title(item["output_path"], exact=True).click()
            modal_image = page.locator('.preview-modal img[alt="График калибровки"]')
            expect(modal_image).to_be_visible()
            expect(modal_image).to_have_js_property("naturalWidth", 1400)
            page.get_by_title("Закрыть предпросмотр").click()

        def experiment_gallery():
            page.goto(f"{base}/sessions/csv-fixture/experiments")
            thumb = page.locator('.figure-thumb').filter(has=page.locator('img[alt="график калибровки.svg"]'))
            expect(thumb).to_be_visible()
            thumb.click()
            expect(page.locator('.preview-modal .image-preview')).to_have_js_property("naturalWidth", 1400)
            page.get_by_title("Закрыть предпросмотр").click()
            page.screenshot(path=str(workspace / "experiments.png"))

        def canonical_article():
            (work / "paper.tex").write_text("Article source")
            page.goto(f"{base}/sessions/csv-fixture/run")
            expect(page.locator(".tabs").get_by_role("button", name="Статья", exact=True)).to_be_visible()
            page.locator(".tabs").get_by_role("button", name="Статья", exact=True).click()
            expect(page.get_by_text("PDF статьи ещё не сформирован.", exact=False)).to_be_visible()
            (work / "paper.tex").unlink()
            (work / "paper.pdf").write_bytes(b"%PDF-1.4 fixture")
            page.reload()
            expect(page.locator(".tabs").get_by_role("button", name="Статья", exact=True)).to_be_visible()
            expect(page.locator("iframe.paper-inline-pdf")).to_have_attribute("title", "paper.pdf")

        for name, action in [("no_phantom_article", no_article), ("markdown_images", inline_image),
                             ("experiment_gallery", experiment_gallery), ("canonical_article", canonical_article)]:
            check(name, action)
        assert not errors, errors
        browser.close()
    report = {"ok": not failures, "checks": checks, "failures": failures, "artifacts": str(workspace), "model_calls": 0}
    print(json.dumps(report, ensure_ascii=False))
    assert not failures, "\n".join(failures)
finally:
    server.should_exit = True
    thread.join(timeout=5)
