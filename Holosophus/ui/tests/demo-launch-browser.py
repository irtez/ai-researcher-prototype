"""Demo launch form + actual upload API, with a local stub manager and no LLM."""

import csv
import io
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
from fastapi.responses import FileResponse, JSONResponse, Response
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "holosophos"), str(ROOT / "codearkt")]
from holosophos.ui_api import create_app  # noqa: E402


with tempfile.TemporaryDirectory(prefix="demo-launch-") as directory:
    workspace = Path(directory)
    app = create_app(workspace_root=workspace)
    launches = []
    errors = []

    @app.post("/agents/manager")
    async def manager(request: Request):
        payload = await request.json()
        launches.append(payload)
        return JSONResponse({"detail": "Проверочный запрос принят без запуска модели"}, status_code=400)

    @app.get("/agents/{path:path}")
    def unused_agents(path: str):
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
            context = browser.new_context(viewport={"width": 1440, "height": 1100})
            context.add_init_script("localStorage.setItem('holosophus.ui.lang','en')")
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base_url)
            expect(page.get_by_role("heading", name="Новая исследовательская задача")).to_be_visible()
            expect(page.locator("html")).to_have_attribute("lang", "ru")
            expect(page.get_by_role("button", name="Язык", exact=True)).to_have_count(0)
            expect(page.locator(".profile-selector")).to_contain_text("Компьютерные науки")
            examples = page.get_by_role("region", name="Примеры заданий")
            expect(examples.get_by_role("button", name="Использовать пример", exact=True)).to_have_count(4)
            page.get_by_role("button", name="Скрыть примеры", exact=True).click()
            page.reload()
            expect(page.get_by_role("button", name="Показать примеры", exact=True)).to_be_visible()
            expect(examples.get_by_role("button", name="Использовать пример", exact=True)).to_have_count(0)
            page.get_by_role("button", name="Показать примеры", exact=True).click()
            page.reload()
            expect(examples.get_by_role("button", name="Использовать пример", exact=True)).to_have_count(4)

            # A user draft and its attachments must survive a cancelled replacement.
            page.locator("textarea").first.fill("Моё исходное задание")
            page.locator('input[type="file"]').set_input_files(
                {"name": "own.csv", "mimeType": "text/csv", "buffer": b"x\n1\n"}
            )
            first = examples.get_by_role("button", name="Использовать пример", exact=True).first
            page.once("dialog", lambda dialog: dialog.dismiss())
            first.click()
            expect(page.locator("textarea").first).to_have_value("Моё исходное задание")
            expect(page.locator(".input-file-list")).to_contain_text("own.csv")

            page.locator(".run-settings-menu > .settings-trigger").click()
            expect(page.get_by_role("combobox", name="Режим запуска", exact=True)).to_have_count(0)
            page.get_by_role("combobox", name="Модель", exact=True).select_option("sonnet")
            page.get_by_role("combobox", name="Уровень рассуждений", exact=True).select_option("low")
            page.get_by_role("combobox", name="Вычисления", exact=True).select_option("GPU")
            page.locator(".settings-backdrop").click(position={"x": 5, "y": 5})

            for index, (profile, attachments) in enumerate([
                ("chemistry", ["inputs/calibration.csv", "inputs/samples.csv", "inputs/quality_control.csv"]),
                ("chemistry", []),
                ("general", []),
                ("chemistry", []),
            ]):
                if index:
                    page.get_by_role("button", name="Новая задача", exact=True).click()
                    page.locator("textarea").first.fill("Черновик перед выбором примера")
                    page.locator(".run-settings-menu > .settings-trigger").click()
                    page.get_by_role("combobox", name="Модель", exact=True).select_option("sonnet")
                    page.get_by_role("combobox", name="Уровень рассуждений", exact=True).select_option("low")
                    page.get_by_role("combobox", name="Вычисления", exact=True).select_option("GPU")
                    page.locator(".settings-backdrop").click(position={"x": 5, "y": 5})
                page.once("dialog", lambda dialog: dialog.accept())
                examples.get_by_role("button", name="Использовать пример", exact=True).nth(index).click()
                assert len(page.locator("textarea").first.input_value()) > 200
                assert len(launches) == index, "Applying an example submitted a task"
                page.locator(".run-settings-menu > .settings-trigger").click()
                expect(page.get_by_role("combobox", name="Модель", exact=True)).to_have_value("sonnet")
                expect(page.get_by_role("combobox", name="Уровень рассуждений", exact=True)).to_have_value("low")
                expect(page.get_by_role("combobox", name="Вычисления", exact=True)).to_have_value("GPU")
                page.locator(".settings-backdrop").click(position={"x": 5, "y": 5})
                with page.expect_response("**/agents/manager") as response_info:
                    page.get_by_role("button", name="Старт", exact=True).click()
                response = response_info.value
                assert response.status == 400, response.status
                assert response.json() == {"detail": "Проверочный запрос принят без запуска модели"}
                expect(page.get_by_role("button", name="Новая задача", exact=True)).to_be_enabled()
                assert launches[-1]["research_profile"] == profile
                assert launches[-1].get("input_files", []) == attachments
                actual = {
                    path: (workspace / launches[-1]["session_id"] / path).read_text()
                    for path in attachments
                }
                if index == 0:
                    calibration = list(csv.DictReader(io.StringIO(actual["inputs/calibration.csv"])))
                    samples = list(csv.DictReader(io.StringIO(actual["inputs/samples.csv"])))
                    controls = list(csv.DictReader(io.StringIO(actual["inputs/quality_control.csv"])))
                    assert len(calibration) == 18 and len(samples) == 9 and len(controls) == 6
                    assert {float(row["concentration_mg_l"]) for row in calibration} == {0, 2, 4, 6, 8, 10}
                    assert {(row["sample_id"], int(row["dilution_factor"])) for row in samples} == {("A", 2), ("B", 5), ("C", 1)}
                    assert {(row["control_id"], float(row["reference_concentration_mg_l"])) for row in controls} == {("QC_low", 3), ("QC_high", 7)}
                    for values in (calibration, samples, controls):
                        assert {int(row["replicate"]) for row in values} == {1, 2, 3}
                        assert all(float(row["absorbance"]) > 0 for row in values)
                assert launches[-1]["compute_mode"] == "GPU"
                assert launches[-1]["model_name"] == "sonnet"
                assert launches[-1]["effort"] == "low"

            page.get_by_role("button", name="Новая задача", exact=True).click()
            # A chemistry example can switch runtimes, but compute stays compatible.
            page.locator(".profile-selector").get_by_role("button", name="Компьютерные науки", exact=True).click()
            page.locator(".run-settings-menu > .settings-trigger").click()
            page.get_by_role("combobox", name="Оркестратор", exact=True).select_option("codearkt")
            page.get_by_role("combobox", name="Вычисления", exact=True).select_option("GPU")
            page.locator(".settings-backdrop").click(position={"x": 5, "y": 5})
            examples.get_by_role("button", name="Использовать пример", exact=True).first.click()
            page.locator(".run-settings-menu > .settings-trigger").click()
            expect(page.get_by_role("combobox", name="Оркестратор", exact=True)).to_have_value("claude_code")
            expect(page.get_by_role("combobox", name="Вычисления", exact=True)).to_have_value("GPU")
            assert len(launches) == 4
            page.locator(".settings-backdrop").click(position={"x": 5, "y": 5})
            page.reload()
            page.set_viewport_size({"width": 1100, "height": 1080})
            page.screenshot(path="/tmp/demo-launch-ui.png", full_page=True)
            # localStorage can be unavailable: the form must still be usable.
            restricted = browser.new_context(viewport={"width": 390, "height": 844})
            restricted.add_init_script("""
                const originalGet = Storage.prototype.getItem;
                const originalSet = Storage.prototype.setItem;
                Storage.prototype.getItem = function(key) {
                    if (key === 'holosophus.ui.examplesCollapsed') throw new Error('blocked');
                    return originalGet.call(this, key);
                };
                Storage.prototype.setItem = function(key, value) {
                    if (key === 'holosophus.ui.examplesCollapsed') throw new Error('blocked');
                    return originalSet.call(this, key, value);
                };
            """)
            small = restricted.new_page()
            small.on("pageerror", lambda error: errors.append(str(error)))
            small.goto(base_url)
            small.get_by_role("button", name="Скрыть примеры", exact=True).click()
            small.get_by_role("button", name="Показать примеры", exact=True).click()
            expect(small.get_by_role("button", name="Использовать пример", exact=True)).to_have_count(4)
            assert small.evaluate("document.documentElement.scrollWidth <= innerWidth")
            small.screenshot(path="/tmp/demo-launch-mobile.png", full_page=True)
            assert not errors, errors
            print(json.dumps({"demo_launch": "PASS", "examples": 4, "real_uploads": True,
                              "replacement_cancelled_safely": True, "visibility_persisted": True,
                              "russian_forced": True, "model_requests": 0}))
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
