"""Browser check of per-invocation tabs; fixture events, no backend or LLM."""
import json
import os
from pathlib import Path
from playwright.sync_api import expect, sync_playwright

output = Path(os.environ.get("UI_PREVIEW_EVIDENCE", "/tmp/ai-researcher-activity-preview"))
output.mkdir(parents=True, exist_ok=True)
with sync_playwright() as pw:
    browser = pw.chromium.launch(executable_path=os.environ.get("UI_TEST_CHROMIUM"), headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.add_init_script("localStorage.setItem('holosophus.ui.lang', 'ru')")
    errors, api_requests = [], []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def block_api(route):
        api_requests.append(route.request.url)
        route.abort()

    page.route("**/agents/**", block_api)
    page.route("**/ui/**", block_api)
    page.goto("http://127.0.0.1:5174/tests/activity-preview.html")
    manager_tab = page.get_by_role("tab", name="Менеджер", exact=True)
    expect(manager_tab).to_have_attribute("aria-selected", "true", timeout=3000)
    manager = page.locator('[data-view="manager"]')
    first = page.locator('[data-view="experiment-1"]')
    expect(first).to_be_hidden()
    expect(manager.locator('[data-activity-id="pubchem-1"]')).to_have_count(0)
    tool = manager.locator('[data-activity-id="read-input"]')
    assert not tool.evaluate("el => el.open")
    tool.locator("summary").click()
    page.screenshot(path=str(output / "tabs-manager.png"), full_page=True)
    card = manager.locator('[data-activity-id="experiment-1"]')
    card.get_by_role("button").click()
    expect(first).to_be_visible()
    expect(manager).to_be_hidden()
    chemical = first.locator('[data-activity-id="pubchem-1"]')
    assert not chemical.evaluate("el => el.open")
    chemical.locator("summary").click()
    expect(chemical.get_by_text("Ответ инструмента")).to_be_visible()

    page.get_by_role("button", name="Показать всё").click()
    expect(first).to_be_visible()
    assert page.get_by_role("tab").evaluate_all("tabs => tabs.map(t => t.dataset.viewKey)") == [
        "manager", "experiment-1", "experiment-2"
    ]
    expect(page.locator('[data-view="experiment-2"]')).to_be_hidden()
    failed = first.locator('[data-activity-id="rdkit-first"]')
    assert not failed.evaluate("el => el.open")
    expect(failed.get_by_text("ModuleNotFoundError: No module named 'rdkit'", exact=True)).to_be_visible()
    page.screenshot(path=str(output / "tabs-agent.png"), full_page=True)

    chemical.evaluate("el => el.scrollIntoView({block: 'center'})")
    previous_y = page.evaluate("scrollY")
    manager_tab.evaluate("el => el.click()")
    expect(manager).to_be_visible()
    assert tool.evaluate("el => el.open")
    card.get_by_role("button").evaluate("el => el.click()")
    expect(first).to_be_visible()
    assert chemical.evaluate("el => el.open")
    restored_y = page.evaluate("scrollY")
    assert abs(restored_y - previous_y) < 2, (previous_y, restored_y)

    page.get_by_role("button", name="Сначала", exact=True).click()
    for _ in range(13):
        page.get_by_role("button", name="Следующее событие").click()
    expect(manager_tab).to_have_attribute("aria-selected", "true")
    reading = page.locator('[data-activity-id="event-11"]')
    reading.evaluate("el => el.scrollIntoView({block: 'center'})")
    before = reading.bounding_box()["y"]
    for _ in range(5):
        page.get_by_role("button", name="Следующее событие").evaluate("el => el.click()")
        page.wait_for_timeout(30)
    after = reading.bounding_box()["y"]
    assert abs(after - before) < 2, (before, after)
    expect(manager_tab).to_have_attribute("aria-selected", "true")
    page.get_by_role("button", name="Воспроизвести", exact=True).evaluate("el => el.click()")
    expect(page.get_by_role("button", name="Пауза", exact=True)).to_be_visible()
    page.get_by_role("button", name="Пауза", exact=True).evaluate("el => el.click()")
    page.get_by_role("button", name="Показать всё").evaluate("el => el.click()")
    page.set_viewport_size({"width": 390, "height": 844})
    page.evaluate("scrollTo(0, 0)")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    manager.locator('[data-activity-id="experiment-2"] button').evaluate("el => el.click()")
    selected_tab = page.locator('[role="tab"][aria-selected="true"]')
    expect(selected_tab).to_have_attribute("data-view-key", "experiment-2")
    bounds = selected_tab.bounding_box()
    strip = page.get_by_role("tablist").bounding_box()
    assert bounds["x"] >= strip["x"] - 1
    assert bounds["x"] + bounds["width"] <= strip["x"] + strip["width"] + 1
    manager_tab.evaluate("el => el.click()")
    page.evaluate("scrollTo(0, 0)")
    page.screenshot(path=str(output / "tabs-mobile.png"), full_page=True)
    assert not errors, errors
    assert not api_requests, api_requests
    result = {"page_errors": errors, "api_requests": api_requests,
              "manager_shift_px": after - before, "restored_scroll_difference_px": restored_y - previous_y,
              "checks": ["manager isolation", "tabs append on the right", "manual navigation",
                         "tool states survive navigation", "visible errors", "scroll restoration",
                         "background growth does not shift manager", "playback", "mobile width"]}
    (output / "tabs-check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))
    browser.close()
