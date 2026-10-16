"""Built UI -> real upload/CodeArkt HTTP endpoints -> fixed-response agent. No LLM."""
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from types import SimpleNamespace

import uvicorn
from fastapi.responses import FileResponse
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "holosophos"), str(ROOT / "codearkt")]
from codearkt.server import get_agent_app
from codearkt.event_bus import AgentEventBus, EventType
from holosophos.codearkt_inputs import prepare_input_files
from holosophos.model_catalog import resolve_codearkt
from holosophos.ui_api import create_app

for key in ("HOLO_AUTH_USERS", "HOLO_AUTH_SECRET"):
    os.environ.pop(key, None)
os.environ["OPENROUTER_API_KEY"] = "offline-test-key"

with tempfile.TemporaryDirectory(prefix="codearkt-inputs-browser-") as directory:
    os.environ["WORKSPACE_DIR"] = directory
    received, errors = [], []
    class Agent:
        name = "manager"
        description = "Offline fixture"
        llm = SimpleNamespace(config_resolver=resolve_codearkt)
        input_files_preparer = staticmethod(prepare_input_files)
        def get_all_agents(self):
            return [self]
        async def ainvoke(self, **kwargs):
            sid = kwargs["session_id"]
            received.append(kwargs["messages"][-1].content)
            assert (Path(directory) / sid / "inputs/data.csv").read_bytes() == b"x\n2\n4\n"
            assert (Path(directory) / sid / "inputs/plot.png").read_bytes() == b"image fixture"
            for kind in (EventType.AGENT_START, EventType.AGENT_END):
                await kwargs["event_bus"].publish_event(sid, "manager", kind)
            return "Fixture completed"
    app = create_app(Path(directory))
    app.mount("/agents", get_agent_app(Agent(), "localhost", 9999, AgentEventBus()))
    @app.get("/{path:path}")
    def frontend(path: str):
        file = ROOT / "ui/dist" / path
        return FileResponse(file if file.is_file() else ROOT / "ui/dist/index.html")
    listener = socket.socket(); listener.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started: break
            time.sleep(.05)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(locale="ru-RU")
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url)
            page.locator('input[type="file"]').set_input_files([
                {"name": "data.csv", "mimeType": "text/csv", "buffer": b"x\n2\n4\n"},
                {"name": "plot.png", "mimeType": "image/png", "buffer": b"image fixture"},
            ])
            page.locator(".run-settings-menu > .settings-trigger").click()
            page.get_by_role("combobox", name="Оркестратор", exact=True).select_option("codearkt")
            page.get_by_role("combobox", name="Вычисления", exact=True).select_option("Local")
            page.locator(".settings-backdrop").click(position={"x":5,"y":5})
            expect(page.locator(".input-file-list")).to_contain_text("data.csv")
            expect(page.locator(".input-file-list")).to_contain_text("plot.png")
            page.locator("textarea").first.fill("Read the attached files")
            page.get_by_role("button", name="Старт", exact=True).click()
            for _ in range(100):
                if received: break
                page.wait_for_timeout(50)
            assert len(received) == 1, received
            assert "inputs/data.csv" in received[0] and "inputs/plot.png" in received[0]
            assert "native Read" not in received[0]
            assert not errors, errors
            browser.close()
        print("PASS: real UI upload -> CodeArkt endpoint -> attachment context; switching runtime preserves both files; no LLM")
    finally:
        server.should_exit=True
        thread.join(timeout=5)
