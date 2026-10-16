"""Offline UI check: real history API, synthetic runner metadata, no model."""

import os
import socket
import sys
from pathlib import Path
import tempfile
import threading
import time
import json
from fastapi.responses import FileResponse, JSONResponse, Response
import uvicorn
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "holosophos"), str(ROOT / "codearkt"), str(ROOT / "scripts")]
from holosophos import research_iterations as history  # noqa: E402
from cc_activity import ResearchActivity
from claude_agent_sdk.types import AssistantMessage, TextBlock, ToolUseBlock, ToolResultBlock, UserMessage, TaskNotificationMessage
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
(work / ".holosophus_ctl").mkdir()
(work / ".holosophus_ctl/internal-control.json").write_text('{"status":"pending"}')


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


first = history.start_iteration(
    work,
    kind="initial",
    user_message="Рассчитать молекулярную массу этанола.",
    settings={},
)
plan(first, "# Исходный план\nРассчитать массу и сохранить результат.")
event(first, "agent_start")
event(first, "output", text="Проверяю исходные данные.")
event(first, "agent_start", "mle_solver")
event(first, "agent_end", "mle_solver")
event(first, "output", text="Расчёт завершён.")
finish(
    first,
    "# Исходный результат\n\nЭтанол: **46.069 г/моль**.\n\n[Данные](../../mle/data.csv)",
)
second = history.start_iteration(
    work,
    kind="feedback",
    user_message="Округлить до двух знаков и добавить пояснение.",
    settings={},
)
plan(
    second, "# План доработки\nОкруглить готовые значения и поручить writer пояснение."
)
event(second, "session_running")
event(second, "agent_start")
event(second, "output", text="Готовлю пояснение.")
event(second, "agent_start", "writer")

activity = ResearchActivity()
def rich(message):
    for record in activity.consume(message):
        history.append_event(work, second["id"], {**record, "session_id": work.name})

def agent_message(text, owner=None):
    return AssistantMessage(model="fixture", parent_tool_use_id=owner, content=[TextBlock(text=text)])

rich(AssistantMessage(model="fixture", content=[ToolUseBlock(id="calc-1", name="Agent", input={
    "subagent_type": "mle-solver", "description": "Первый расчёт", "prompt": "Проверь массу этанола", "run_in_background": True})]))
rich(agent_message("Сохранённое сообщение экспериментатора.", "calc-1"))
rich(AssistantMessage(model="fixture", parent_tool_use_id="calc-1", content=[ToolUseBlock(id="tool-1", name="mcp__chem__calculate_mass", input={"smiles": "CCO"})]))
rich(UserMessage(parent_tool_use_id="calc-1", content=[ToolResultBlock(tool_use_id="tool-1", content="Fixture permission denied", is_error=True)]))
for number in range(12):
    rich(agent_message(f"Шаг {number}: проверка сохранённых данных. " * 8, "calc-1"))
rich(AssistantMessage(model="fixture", content=[ToolUseBlock(id="calc-2", name="Agent", input={
    "subagent_type": "mle-solver", "description": "Независимая проверка", "prompt": "Сверь результат"})]))
rich(agent_message("Менеджер продолжает независимую работу."))

pending = {
    "qid": "pending-plan",
    "stage": "plan",
    "question": "Проверить план текущей доработки",
    "context": "# План текущей доработки\nНебольшое пояснение.",
    "asked_at": time.time(),
    "auto_approve_at": None,
}
long_summary = ("Проверены исходные измерения и единицы величин. " * 65) + "\nКонец полного пояснения."
markdown_summary = (
    "Краткий итог доработки.\n\n"
    "- **Изменение:** округление до `0.01`.\n"
    "- **Результат:** использованы [готовые данные](../../../mle/data.csv).\n\n"
    "Повторного расчёта не было."
)
(work / first["experiment_summary_directory"] / "calculation.json").write_text(json.dumps([
    {"phase": "Расчёт молекулярной массы", "summary": long_summary, "artifacts": ["mle/results/metrics.json"]}
], ensure_ascii=False))
(work / second["experiment_summary_directory"] / "rounding.json").write_text(json.dumps([
    {"phase": "Округление готовых результатов", "summary": markdown_summary, "artifacts": ["mle/data.csv"]}
], ensure_ascii=False))
active = True
app = create_app(workspace_root=workspace)


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
        expect(selector).to_have_value("0002")
        expect(page.locator(".capacity-pill")).to_have_count(0)
        expect(page.locator(".top-meta-strip")).not_to_contain_text("полная статья")
        expect(page.locator(".top-meta-strip")).to_contain_text("исследование")
        artifacts = page.request.get(
            base_url + "/ui/sessions/ui-history-demo/artifacts",
            headers={"X-Holo-Client": "ui-test"},
        ).json()["artifacts"]
        # Summary alone must expose the tab, even before code/metrics are available.
        assert {a["type"] for a in artifacts if a["tab"] == "experiments"} == {"experiment_summary"}
        expect(page.get_by_role("button", name="Эксперименты", exact=True)).to_be_visible()
        (work / "mle/results/metrics.json").write_text('{"mass":46.069}')
        (work / "mle/log.jsonl").write_text('{"phase":"old","summary":"Старый журнал"}\n')
        page.reload()
        assert all(".holosophus_ctl" not in item["path"] for item in artifacts)
        expect(page.locator(".workbench")).not_to_contain_text("internal-control.json")
        expect(page.locator('[data-stage="paper"]')).to_have_attribute(
            "data-status", "running"
        )
        expect(page.locator(".iteration-result")).to_contain_text("46.069")
        expect(page.get_by_role("button", name="Литература", exact=True)).to_have_count(0)
        expect(page.locator(".tabs button").first).to_have_text("Детали запуска")
        expect(page.locator(".iteration-documents .history-document")).to_have_count(2)
        expect(page.locator(".iteration-documents")).to_contain_text("Без изменений из «Исходная задача»")
        expect(page.locator(".iteration-documents")).not_to_contain_text("plans/0001/")
        page.get_by_role("button", name="Эксперименты", exact=True).click()
        expect(page.locator(".iteration-history")).to_be_hidden()
        expect(page.get_by_role("heading", name="Эксперименты", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Согласовать", exact=True)).to_be_visible()
        expect(selector).to_have_value("0002")
        summaries = page.locator(".experiment-summaries")
        expect(summaries).to_contain_text("Сводка экспериментатора")
        expect(summaries.locator(".experiment-summary-iteration")).to_have_count(2)
        expect(summaries.locator('[data-iteration="0001"] h3')).to_have_text("Исходная задача")
        expect(summaries.locator('[data-iteration="0002"] h3')).to_have_text("Доработка 1")
        description = summaries.locator(".experiment-summary-text").first
        assert " ".join(description.text_content().split()) == " ".join(long_summary.split())
        formatted = summaries.locator('[data-iteration="0002"] .experiment-summary-text')
        expect(formatted.locator("ul > li")).to_have_count(2)
        expect(formatted.locator("strong")).to_have_text(["Изменение:", "Результат:"])
        expect(formatted.locator("code")).to_have_text("0.01")
        expect(formatted.locator("p")).to_have_text(["Краткий итог доработки.", "Повторного расчёта не было."])
        data_link = formatted.get_by_role("link", name="готовые данные")
        expect(data_link).to_have_attribute("href", "/ui/artifacts/ui-history-demo/raw?path=mle%2Fdata.csv")
        assert "ethanol,46.069" in page.request.get(base_url + data_link.get_attribute("href"), headers={"X-Holo-Client": "ui-test"}).text()
        assert summaries.locator(".experiment-summary-step").first.bounding_box()["width"] <= 960
        for width in (1600, 390):
            page.set_viewport_size({"width": width, "height": 1000})
            for text in (description, formatted):
                assert text.evaluate("el => el.scrollHeight <= el.clientHeight + 1 && el.scrollWidth <= el.clientWidth + 1")
                assert text.evaluate("el => getComputedStyle(el).maxHeight") == "none"
        page.set_viewport_size({"width": 1600, "height": 1000})
        summaries.get_by_role("button", name="mle/results/metrics.json", exact=True).click()
        expect(page.get_by_title("Закрыть предпросмотр")).to_be_visible()
        page.get_by_title("Закрыть предпросмотр").click()
        assert not page.locator(".experiment-legacy-log").evaluate("el => el.open")
        summaries.scroll_into_view_if_needed()
        page.screenshot(path=str(workspace / "experiment-summaries.png"), full_page=True)
        page.locator(".session-row").first.click()
        expect(page).to_have_url(base_url + "/sessions/ui-history-demo/run")
        expect(page.locator(".iteration-history")).to_have_count(1)
        expect(
            page.get_by_role("button", name="Согласовать", exact=True)
        ).to_be_visible()
        tabs = page.locator(".activity-tabs [role=tab]")
        assert tabs.evaluate_all("xs => xs.map(x => x.dataset.viewKey)") == ["manager", "legacy:writer", "calc-1", "calc-2"]
        manager = page.locator('[data-view="manager"]')
        first_agent = page.locator('[data-view="calc-1"]')
        expect(manager).not_to_contain_text("Сохранённое сообщение экспериментатора.")
        expect(page.get_by_text("Непривязанные результаты", exact=True)).to_have_count(0)
        manager.locator('[data-activity-id="calc-1"] button').click()
        expect(first_agent).to_be_visible()
        expect(page.locator(".iteration-history")).to_be_hidden()
        tool = first_agent.locator('[data-activity-id="tool-1"]')
        assert not tool.evaluate("el => el.open")
        expect(tool.locator(".activity-tool-error")).to_be_visible()
        tool.locator("summary").click()
        expect(tool.locator("pre").first).to_contain_text('"smiles": "CCO"')
        first_agent.locator(".activity-message").nth(8).evaluate("el => el.scrollIntoView({block:'center'})")
        scroll_before = page.evaluate("({window:scrollY, panel:document.querySelector('.workbench').scrollTop})")
        page.locator('[role=tab][data-view-key=manager]').evaluate("el => el.click()")
        expect(manager).to_be_visible()
        manager.locator('[data-activity-id="calc-1"] button').evaluate("el => el.click()")
        assert tool.evaluate("el => el.open")
        scroll_after = page.evaluate("({window:scrollY, panel:document.querySelector('.workbench').scrollTop})")
        assert all(abs(scroll_before[key] - scroll_after[key]) < 2 for key in scroll_before), (scroll_before, scroll_after)
        rich(TaskNotificationMessage(subtype="task_notification", data={}, task_id="calc-2", tool_use_id="calc-2", status="completed", summary="Проверка готова.", output_file="", uuid="end-2", session_id="fixture"))
        expect(page.locator('[role=tab][data-view-key=calc-2] .activity-status-complete')).to_be_visible(timeout=10000)
        expect(page.locator('[role=tab][data-view-key=calc-1]')).to_have_attribute("aria-selected", "true")
        assert tabs.evaluate_all("xs => xs.map(x => x.dataset.viewKey)") == ["manager", "legacy:writer", "calc-1", "calc-2"]
        first_agent.locator(".activity-message").nth(8).evaluate("el => el.scrollIntoView({block:'center'})")
        page.wait_for_timeout(100)
        stage_scroll = page.evaluate("scrollY")
        assert abs(page.locator(".activity-tabs").bounding_box()["y"]) < 2
        expect(page.locator(".pending-notice")).to_contain_text("Ожидает согласования: план")
        page.locator(".pending-notice button").click()
        expect(page.locator(".approval-card")).to_be_focused()
        expect(page.locator(".pending-notice")).to_have_count(0)
        first_agent.locator(".activity-message").nth(8).evaluate("el => el.scrollIntoView({block:'center'})")
        pending = None
        expect(page.locator(".approval-card")).to_have_count(0, timeout=10000)
        expect(page.locator(".pending-notice")).to_have_count(0)
        reading_y = first_agent.locator(".activity-message").nth(8).bounding_box()["y"]
        pending = {"qid": "second-pending", "stage": "sources", "question": "Новый запрос источников",
                   "context": "# Источники\nСохранённые данные.", "asked_at": time.time(), "auto_approve_at": None}
        expect(page.locator(".pending-notice")).to_contain_text("Ожидает согласования: источники", timeout=10000)
        expect(page.locator('[role=tab][data-view-key=calc-1]')).to_have_attribute("aria-selected", "true")
        assert abs(first_agent.locator(".activity-message").nth(8).bounding_box()["y"] - reading_y) < 2
        stage_scroll = page.evaluate("scrollY")
        page.get_by_role("button", name="Эксперименты", exact=True).evaluate("el => el.click()")
        expect(first_agent).to_be_hidden()
        page.get_by_role("button", name="Детали запуска", exact=True).evaluate("el => el.click()")
        expect(first_agent).to_be_visible()
        assert abs(page.evaluate("scrollY") - stage_scroll) < 2
        assert tool.evaluate("el => el.open")
        page.screenshot(path=str(workspace / "agent-activity.png"), full_page=True)
        page.set_viewport_size({"width":390, "height":844})
        page.locator('[role=tab][data-view-key=calc-2]').evaluate("el => el.click()")
        expect(page.locator('[data-view=calc-2]')).to_contain_text("Проверка готова.")
        selected_bounds = page.locator('[role=tab][aria-selected=true]').bounding_box()
        strip_bounds = page.locator('.activity-tabs').bounding_box()
        assert selected_bounds['x'] >= strip_bounds['x'] - 1
        assert selected_bounds['x'] + selected_bounds['width'] <= strip_bounds['x'] + strip_bounds['width'] + 1
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(workspace / "activity-mobile.png"), full_page=True)
        page.set_viewport_size({"width":1600, "height":1000})
        page.locator('[role=tab][data-view-key=manager]').evaluate("el => el.click()")
        selector.select_option("0001")
        expect(page.locator('[data-stage="paper"]')).to_have_attribute(
            "data-status", "not_run"
        )
        expect(page.locator(".activity-panel[data-view=manager] .activity-message")).to_have_count(2)
        expect(page.locator(".activity-panel[data-view=manager] .activity-message").nth(0)).to_contain_text(
            "Проверяю исходные данные."
        )
        expect(page.locator(".activity-panel[data-view=manager] .activity-message").nth(1)).to_contain_text(
            "Расчёт завершён."
        )
        assert page.locator(".activity-panel[data-view=manager] .activity-message time").first.inner_text()
        expect(page.locator(".iteration-result a")).to_have_attribute(
            "href", "/ui/artifacts/ui-history-demo/raw?path=mle%2Fdata.csv"
        )
        with page.expect_download() as download:
            page.locator(".iteration-result a").click()
        assert "ethanol,46.069" in Path(download.value.path()).read_text()
        page.reload()
        expect(selector).to_have_value("0001")
        expect(
            page.get_by_role("button", name="Согласовать", exact=True)
        ).to_be_visible()
        expect(page.locator('[data-stage="paper"]')).to_have_attribute(
            "data-status", "not_run"
        )
        page.locator(".history-context > summary").click()
        page.locator('.iteration-documents [data-document-stage="plan"] > summary').click()
        expect(page.locator(".history-context")).to_contain_text(
            "Рассчитать массу и сохранить результат."
        )
        expect(page.locator(".iteration-documents .history-document")).to_have_count(2)
        expect(page.locator(".history-approval-log")).not_to_have_attribute("open", "")
        for summary in page.locator(".history-approval-log .history-document > summary").all():
            assert summary.text_content().count("вручную") == 1
        page.screenshot(path=str(workspace / "history.png"), full_page=True)
        page.locator(".history-answer").evaluate("el => el.scrollIntoView({block:'start'})")
        expect(page.locator(".pending-notice")).to_be_visible()
        expect(page.locator(".pending-notice")).to_contain_text("источники")
        page.locator(".pending-notice button").click()
        expect(page.locator(".approval-card")).to_be_focused()
        expect(selector).to_have_value("0001")
        expect(page.locator(".pending-notice")).to_have_count(0)
        selector.select_option("0002")
        expect(page.locator(".iteration-documents")).not_to_contain_text("plans/0001/")
        event(second, "agent_end", "writer")
        event(second, "output", text="Пояснение добавлено.")
        finish(
            second,
            "# Доработанный результат\n\nЭтанол: **46.07 г/моль**.\n\n[Данные](../../mle/data.csv)",
        )
        active = False
        pending = None
        expect(page.locator(".iteration-result")).to_contain_text(
            "46.07", timeout=12000
        )
        expect(page.locator('[data-stage="paper"]')).to_have_attribute(
            "data-status", "complete"
        )
        expect(page.locator(".feedback-toggle")).to_be_visible(timeout=10000)
        expect(page.locator(".feedback-editor")).to_have_count(0)
        page.locator(".feedback-toggle").click()
        draft = page.locator(".feedback-editor textarea")
        draft.fill("Сохранить текст замечания при сворачивании")
        page.get_by_label("Согласовать выбранные источники", exact=True).check()
        page.get_by_label("Согласовать план исследования", exact=True).uncheck()
        page.get_by_label("Согласовать автоматически через", exact=True).check()
        page.get_by_label("Время до автоматического согласования", exact=True).fill(
            "17"
        )
        page.locator(".feedback-toggle").click()
        expect(page.locator(".feedback-editor")).to_have_count(0)
        pending = {
            "qid": "new-pending",
            "stage": "plan",
            "question": "Новый вопрос",
            "context": "# План",
            "asked_at": time.time(),
            "auto_approve_at": None,
        }
        selector.select_option("0001")
        expect(
            page.get_by_role("button", name="Согласовать", exact=True)
        ).to_be_visible(timeout=10000)
        expect(page.locator(".feedback-toggle")).to_have_count(0)
        pending = None
        expect(page.locator(".feedback-toggle")).to_be_visible(timeout=10000)
        page.locator(".feedback-toggle").click()
        expect(draft).to_have_value("Сохранить текст замечания при сворачивании")
        expect(
            page.get_by_label("Согласовать выбранные источники", exact=True)
        ).to_be_checked()
        expect(
            page.get_by_label("Согласовать план исследования", exact=True)
        ).not_to_be_checked()
        expect(
            page.get_by_label("Время до автоматического согласования", exact=True)
        ).to_have_value("17")
        page.locator(".feedback-toggle").click()
        selector.select_option("0002")
        third = history.start_iteration(
            work, kind="feedback", user_message="Повторить расчёт.", settings={}
        )
        event(third, "agent_start")
        event(third, "agent_start", "mle_solver")
        plan(third, "# План неудачной попытки\nПовторить расчёт.")
        history.finish_iteration(
            work, third["id"], status="error", error="Синтетическая ошибка вычисления"
        )
        expect(selector.locator("option")).to_have_count(3, timeout=10000)
        selector.select_option("0003")
        expect(page.locator(".iteration-history")).to_contain_text(
            "Синтетическая ошибка вычисления"
        )
        expect(page.locator('[data-stage="experiments"]')).to_have_attribute(
            "data-status", "error"
        )
        expect(page.locator(".iteration-result")).to_contain_text("46.07")
        expect(page.locator(".iteration-result")).not_to_contain_text("46.069")
        expect(page.locator(".top-bar .run-indicator")).to_contain_text(
            "ошибка", timeout=10000
        )
        page.screenshot(path=str(workspace / "failed.png"), full_page=True)
        fourth = history.start_iteration(
            work, kind="feedback", user_message="Ещё одна попытка.", settings={}
        )
        event(fourth, "agent_start")
        history.finish_iteration(work, fourth["id"], status="cancelled")
        expect(selector.locator("option")).to_have_count(4, timeout=10000)
        selector.select_option("0004")
        expect(page.locator(".iteration-status")).to_contain_text("отменено")
        expect(page.locator(".iteration-result")).to_contain_text("46.07")
        expect(page.locator('[data-stage="prompt"]')).to_have_attribute(
            "data-status", "cancelled"
        )
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(workspace / "mobile.png"), full_page=True)
        overflow = page.evaluate(
            "document.documentElement.scrollWidth > window.innerWidth"
        )
        assert not capacity_requests, capacity_requests
        assert not page_errors, page_errors
        # The old backend still supports capacity; choosing it explicitly enables polling.
        launch = browser.new_page(viewport={"width": 1400, "height": 900})
        launch_requests = []
        launch.on(
            "request",
            lambda request: (
                launch_requests.append(request.url)
                if request.url.endswith("/capacity")
                else None
            ),
        )
        launch.goto(base_url)
        launch.get_by_title("Настройки запуска", exact=True).click()
        expect(launch.locator(".capacity-pill")).to_have_count(0)
        assert not launch_requests
        launch.get_by_label("Оркестратор", exact=True).select_option("codearkt")
        expect(launch.locator(".capacity-pill")).to_be_visible()
        assert launch_requests
        launch.get_by_label("Оркестратор", exact=True).select_option("claude_code")
        expect(launch.locator(".capacity-pill")).to_have_count(0)
        request_count = len(launch_requests)
        launch.wait_for_timeout(4500)
        assert len(launch_requests) == request_count
        launch.close()
        assert not overflow
        print(
            json.dumps(
                {
                    "browser": "PASS",
                    "profile": profile,
                    "page_errors": page_errors,
                    "mobile_horizontal_overflow": overflow,
                    "workspace": str(workspace),
                },
                ensure_ascii=False,
            )
        )
        browser.close()
finally:
    server.should_exit = True
    thread.join(timeout=5)
