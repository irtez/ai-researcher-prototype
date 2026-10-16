"""Browser check of the shared file browser against the actual local API; no LLM."""
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
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "holosophos"), str(ROOT / "codearkt")]
from holosophos.ui_api import create_app  # noqa: E402

workspace = Path(tempfile.mkdtemp(prefix="artifact-browser-"))
for profile in ("general", "chemistry"):
    work = workspace / profile
    for relative, content in {
        ".holosophus_ui/owner": "file-test",
        ".holosophus_ui/orchestrator": "claude_code",
        ".holosophus_ui/research_profile": profile,
        ".local/lib/python3.11/site-packages/rdkit/README.md": "Library readme",
        "started_prompt.md": "Синтетическая задача",
        "results/0001/result.md": "# Проверенный результат\nТекст документа.",
        "mle/code/calculate.py": "# not executed",
        "mle/results/data.json": json.dumps({"value": 5}),
        "custom/nested/notes.markdown": "# Рабочая заметка",
    }.items():
        path = work / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    large = work / "large"
    large.mkdir()
    for number in range(1000):
        (large / f"data-{number:04d}.xyz").write_text("research data")

app = create_app(workspace_root=workspace)


@app.get("/agents/session/{session_id}")
def metadata(session_id: str):
    return {"session_id": session_id, "research_profile": session_id, "model_name": "opus",
            "compute_mode": "Local", "active": False, "can_resume": False, "status": "complete"}


@app.get("/agents/question/{session_id}")
def question(session_id: str):
    return Response(status_code=204)


@app.api_route("/agents/{rest:path}", methods=["GET", "POST"])
def no_model(rest: str):
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
    time.sleep(0.05)
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=os.environ.get("UI_TEST_CHROMIUM"), headless=True)
        for profile in ("general", "chemistry"):
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.add_init_script("localStorage.setItem('holosophus.ui.clientId','file-test')")
            page.goto(base + f"/sessions/{profile}/run")
            panel = page.locator(".workbench .artifact-browser")
            expect(panel).to_be_visible()
            expect(panel).not_to_contain_text("Library readme")
            expect(panel.locator(".artifact-documents")).to_contain_text("result.md")
            expect(panel.locator(".artifact-file")).to_have_count(4)  # three quick documents + root prompt
            folder = panel.get_by_title("large", exact=True)
            expect(folder).to_have_attribute("aria-expanded", "false")
            folder.click()
            expect(panel.locator(".artifact-file")).to_have_count(44)
            panel.get_by_role("button", name="Показать ещё 40 (осталось 960)").click()
            expect(panel.locator(".artifact-file")).to_have_count(84)
            assert panel.locator(".artifact-tree-scroll").evaluate("el => el.clientHeight <= 406 && el.scrollHeight > el.clientHeight")
            folder.click()
            expect(panel.locator(".artifact-file")).to_have_count(4)
            search = panel.get_by_role("textbox")
            search.fill("data-0999")
            expect(panel.locator(".artifact-file")).to_have_count(1)
            expect(panel).to_contain_text("large/data-0999.xyz")
            panel.locator(".artifact-file").click()
            expect(page.locator(".preview-modal")).to_contain_text("large/data-0999.xyz")
            assert page.request.get(base + f"/ui/artifacts/{profile}/raw?path=large/data-0999.xyz",
                                    headers={"X-Holo-Client": "file-test"}).text() == "research data"
            page.get_by_title("Закрыть предпросмотр").click()
            search.fill("missing-file")
            expect(panel).to_contain_text("По этому запросу файлов нет")
            search.fill("")
            panel.locator(".artifact-documents").get_by_title("results/0001/result.md", exact=True).click()
            expect(page.locator(".preview-modal")).to_contain_text("Проверенный результат")
            page.get_by_title("Закрыть предпросмотр").click()
            panel.scroll_into_view_if_needed()
            page.screenshot(path=str(workspace / f"{profile}.png"))
            # The drawer uses the same bounded tree; no flat thousand-row list.
            page.locator(".artifact-strip .command-button").click()
            drawer = page.locator(".drawer-body")
            expect(drawer.locator(".artifact-browser")).to_be_visible()
            drawer.get_by_role("textbox").fill("data-0999")
            expect(drawer.locator(".artifact-file")).to_have_count(1)
            drawer.get_by_role("textbox").fill("")
            page.locator(".artifact-strip .command-button").click()
            page.get_by_role("button", name="Эксперименты", exact=True).click()
            expect(page.locator(".workbench .artifact-browser:visible")).to_contain_text("Эксперименты")
            for width in (1440, 390):
                page.set_viewport_size({"width": width, "height": 1000})
                assert page.locator(".workbench .artifact-browser:visible").evaluate("el => el.scrollWidth <= el.clientWidth + 1")
            assert not errors, errors
            page.close()
        browser.close()
    print(f"Artifact browser passed for general and chemistry; screenshots: {workspace}")
finally:
    server.should_exit = True
    thread.join(timeout=5)
