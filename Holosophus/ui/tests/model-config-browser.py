"""Built UI + real public catalog, intercepted launch requests; no model/backend."""

import functools
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from urllib.parse import urlsplit
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import yaml
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "holosophos"))
from holosophos import model_catalog


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


with tempfile.TemporaryDirectory(prefix="models-ui-") as directory:
    data = yaml.safe_load((ROOT / "config/models.yaml").read_text())
    data["models"]["laboratory"]["enabled"] = True
    data["models"]["laboratory"]["generation"]["effort"] = {
        "choices": ["low", "high"],
        "default": "high",
    }
    config = Path(directory) / "models.yaml"
    config.write_text(yaml.safe_dump(data))
    os.environ["AI_RESEARCHER_MODELS_CONFIG"] = str(config)
    public = {
        **model_catalog.public_config(),
        "agents": [],
        "pipelines": {},
        "auth_required": False,
    }
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(ROOT / "ui/dist"))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    launches = []
    errors = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for profile, label, compute in [
                ("general", "Компьютерные науки", "Local"),
                ("chemistry", "Химия", "GPU"),
            ]:
                context = browser.new_context(
                    locale="ru-RU", viewport={"width": 1440, "height": 1000}
                )
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))

                def ui_route(route):
                    path = urlsplit(route.request.url).path
                    if path.endswith("/config"):
                        route.fulfill(json=public)
                    elif path == "/ui/sessions":
                        route.fulfill(json={"sessions": []})
                    else:
                        route.fulfill(
                            status=404, json={"detail": "No synthetic session"}
                        )

                page.route("**/ui/**", ui_route)

                def agents_route(route):
                    if route.request.method == "POST" and route.request.url.endswith(
                        "/manager"
                    ):
                        launches.append(route.request.post_data_json)
                        route.fulfill(
                            status=400, json={"detail": "Synthetic acceptance only"}
                        )
                    else:
                        route.fulfill(status=204)

                page.route("**/agents/**", agents_route)
                page.goto(f"http://127.0.0.1:{server.server_port}/")
                page.locator(".profile-selector").get_by_role(
                    "button", name=label, exact=True
                ).click()
                page.locator(".run-settings-menu > .settings-trigger").click()
                model = page.get_by_role("combobox", name="Модель", exact=True)
                model.select_option("sonnet")
                page.get_by_role("combobox", name="Уровень рассуждений", exact=True).select_option(
                    "low"
                )
                model.select_option("haiku")
                expect(
                    page.get_by_role("combobox", name="Уровень рассуждений", exact=True)
                ).to_have_count(0)
                model.select_option("laboratory")
                expect(
                    page.get_by_role("combobox", name="Уровень рассуждений", exact=True)
                ).to_have_value("low")
                page.get_by_role("combobox", name="Уровень рассуждений", exact=True).select_option(
                    "high"
                )
                executor = page.get_by_role("combobox", name="Вычисления", exact=True)
                expect(executor).to_be_enabled()
                executor.select_option(compute)
                page.screenshot(path=f"/tmp/model-config-{profile}.png")
                page.locator(".settings-backdrop").click(position={"x": 5, "y": 5})
                page.locator("textarea").fill("Synthetic launch")
                with page.expect_request("**/agents/manager"):
                    page.get_by_role("button", name="Старт", exact=True).click()
                page.wait_for_timeout(200)
                assert launches[-1]["model_name"] == "laboratory"
                assert launches[-1]["effort"] == "high"
                assert launches[-1]["research_profile"] == profile
                assert launches[-1]["compute_mode"] == compute
                assert "base_url" not in json.dumps(launches[-1])
                context.close()
            browser.close()
        assert not errors, errors
        print(
            json.dumps(
                {
                    "browser": "passed",
                    "profiles": 2,
                    "launches": launches,
                    "model_requests": 0,
                },
                ensure_ascii=False,
            )
        )
    finally:
        server.shutdown()
