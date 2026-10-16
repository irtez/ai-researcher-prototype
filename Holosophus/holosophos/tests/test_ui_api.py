import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from holosophos.settings import settings
from holosophos.ui_api import create_app
from research_paper_registry import PaperRegistry, file_sha256


def test_experiment_plots_stay_with_results_and_do_not_imply_a_paper(tmp_path: Path):
    work = _make_session(tmp_path, "plots", owner="alice")
    plots = {"mle/results/calibration.png": "image", "mle/figures/residuals.svg": "image",
             "mle/results/calibration.pdf": "pdf"}
    for name in plots:
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    client = TestClient(create_app(workspace_root=tmp_path))
    headers = {"X-Holo-Client": "alice"}
    base = "/ui/sessions/plots"
    artifacts = client.get(base + "/artifacts", headers=headers).json()["artifacts"]
    by_path = {a["path"]: a for a in artifacts}
    for path, preview in plots.items():
        assert by_path[path]["tab"] == "experiments"
        assert by_path[path]["preview_kind"] == preview
        assert client.get("/ui/artifacts/plots/raw", params={"path": path}, headers=headers).content == b"fixture"
    assert client.get(base + "/summary", headers=headers).json()["paper"]["figures"] == []
    stages = {s["id"]: s for s in client.get(base, headers=headers).json()["stages"]}
    assert stages["experiments"]["artifact_count"] == 3
    assert stages["paper"]["status"] == "pending"


def test_paper_summary_uses_only_root_paper_pdf_and_keeps_other_files(tmp_path: Path):
    work = _make_session(tmp_path, "paper-selection", owner="alice")
    other_pdfs = {"graph.pdf", "test.pdf", "draft/paper.pdf", "inputs/article.pdf"}
    for name in other_pdfs:
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.4 test")
    client = TestClient(create_app(workspace_root=tmp_path))
    headers = {"X-Holo-Client": "alice"}
    base = "/ui/sessions/paper-selection"
    assert client.get(base + "/summary", headers=headers).json()["paper"]["pdfs"] == []
    (work / "paper.pdf").write_bytes(b"%PDF-1.4 paper")
    assert client.get(base + "/summary", headers=headers).json()["paper"]["pdfs"] == ["paper.pdf"]
    paths = {item["path"] for item in client.get(base + "/artifacts", headers=headers).json()["artifacts"]}
    assert other_pdfs | {"paper.pdf"} <= paths


def _register_ui_paper(
    workspace: Path, session_id: str, *, tex_path="results/article.tex",
    pdf_path="results/article.pdf", review=None,
):
    registry = PaperRegistry(workspace)
    registry.register(session_id, "check-1", tex_path, pdf_path)
    if review:
        registry.record_review(
            session_id, pdf_path, review["json_path"], review["markdown_path"],
            expected_check_run_id="check-1", pdf_sha256=file_sha256(workspace / session_id / pdf_path),
        )
    return workspace / ".paper_registry" / f"{hashlib.sha256(session_id.encode()).hexdigest()}.json"


def test_changed_paper_notice_keeps_pdf_review_and_run_state_available(tmp_path):
    work = _make_session(tmp_path, "changed-paper", owner="alice")
    for name in ("paper.tex", "paper.pdf", "review.md"):
        (work / name).write_text("Checked document")
    (work / "review.json").write_text('{"overall": {"score": 5}}')
    registry = PaperRegistry(tmp_path)
    registry.register(work.name, "check-1")
    registry.record_review(work.name, "paper.pdf", "review.json", "review.md",
                          expected_check_run_id="check-1", pdf_sha256=file_sha256(work / "paper.pdf"))
    registry.update_status(work.name, "check-1", "ready", checked_fingerprints={
        name: file_sha256(work / f"paper.{name}") for name in ("tex", "pdf")
    })
    client = TestClient(create_app(workspace_root=tmp_path))
    headers = {"X-Holo-Client": "alice"}
    base = f"/ui/sessions/{work.name}"
    before = client.get(base, headers=headers).json()
    first = client.get(base + "/summary", headers=headers).json()
    assert first["paper"]["document"]["changed_since_check"] is False
    (work / "paper.pdf").write_bytes(b"%PDF-1.4 revised")
    after = client.get(base + "/summary", headers=headers).json()
    assert after["paper"]["document"]["changed_since_check"] is True
    assert after["paper"]["document"]["status"] == "ready"
    assert after["paper"]["pdfs"] == ["paper.pdf"]
    assert after["review"] == first["review"]
    assert client.get(base, headers=headers).json()["status"] == before["status"]
    assert client.get(f"/ui/artifacts/{work.name}/raw", params={"path": "paper.pdf"}, headers=headers).content == b"%PDF-1.4 revised"
    assert registry.read(work.name)["status"] == "ready"


def test_registered_paper_overrides_legacy_paths_and_experiment_classification(tmp_path: Path):
    work = _make_session(tmp_path, "registered-paper", owner="alice")
    for name in ["paper.pdf", "mle/results/статья.pdf", "mle/results/статья.tex"]:
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture")
    _register_ui_paper(tmp_path, work.name, tex_path="mle/results/статья.tex", pdf_path="mle/results/статья.pdf")
    client = TestClient(create_app(workspace_root=tmp_path))
    headers = {"X-Holo-Client": "alice"}
    base = f"/ui/sessions/{work.name}"
    summary = client.get(base + "/summary", headers=headers).json()
    assert summary["paper"]["pdfs"] == ["mle/results/статья.pdf"]
    assert summary["paper"]["document"]["pdf_available"] is True
    files = {item["path"]: item for item in client.get(base + "/artifacts", headers=headers).json()["artifacts"]}
    assert files["mle/results/статья.pdf"]["tab"] == "paper"
    assert files["mle/results/статья.tex"]["type"] == "latex_source"
    detail = client.get(base, headers=headers).json()
    assert detail["summary"]["latest_pdf"] == "mle/results/статья.pdf"
    assert client.get(f"/ui/artifacts/{work.name}/raw", params={"path": "mle/results/статья.pdf"}, headers=headers).text == "fixture"


def test_missing_registered_paper_is_local_and_does_not_promote_legacy_review(tmp_path: Path):
    work = _make_session(tmp_path, "missing-paper", owner="alice")
    (work / "paper.pdf").write_text("old paper")
    (work / "review.json").write_text('{"overall": {"score": 10}}')
    (work / "review.md").write_text("Old review")
    _register_ui_paper(tmp_path, work.name)
    client = TestClient(create_app(workspace_root=tmp_path))
    headers = {"X-Holo-Client": "alice"}
    base = f"/ui/sessions/{work.name}"
    summary = client.get(base + "/summary", headers=headers).json()
    assert summary["paper"]["pdfs"] == []
    assert summary["paper"]["document"] == {
        "registered": True, "status": "checking", "tex_path": "results/article.tex",
        "pdf_path": "results/article.pdf", "pdf_available": False, "error": None,
        "changed_since_check": None,
    }
    assert summary["review"]["overall_score"] is None
    assert summary["review"]["markdown_review"] is None
    assert summary["review"]["review_artifacts"] == []
    files = client.get(base + "/artifacts", headers=headers).json()["artifacts"]
    assert {file["path"] for file in files} >= {"paper.pdf", "review.json", "review.md"}
    assert all(file["tab"] != "review" for file in files)
    assert client.get(base, headers=headers).json()["status"] == "complete"


def test_registered_review_uses_associated_paths_and_keeps_markdown_when_json_is_invalid(tmp_path: Path):
    work = _make_session(tmp_path, "registered-review", owner="alice")
    for name, text in {
        "results/article.pdf": "fixture", "results/article.tex": "source",
        "reviews/article.json": '{"overall": {"score": 4}}',
        "reviews/article.md": "# Registered review", "review.json": '{"overall": {"score": 10}}',
    }.items():
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _register_ui_paper(tmp_path, work.name, review={
        "json_path": "reviews/article.json", "markdown_path": "reviews/article.md",
    })
    client = TestClient(create_app(workspace_root=tmp_path))
    headers = {"X-Holo-Client": "alice"}
    base = f"/ui/sessions/{work.name}"
    summary = client.get(base + "/summary", headers=headers).json()
    assert summary["review"]["overall_score"] == 4
    assert set(summary["review"]["review_artifacts"]) == {"reviews/article.json", "reviews/article.md"}
    # A new revision of the same article keeps the feedback used to revise it.
    (work / "results/article.pdf").write_text("Revised PDF")
    PaperRegistry(tmp_path).register(work.name, "revision", "results/article.tex", "results/article.pdf")
    assert client.get(base + "/summary", headers=headers).json()["review"]["overall_score"] == 4
    (work / "reviews/article.json").write_text("{invalid")
    review = client.get(base + "/summary", headers=headers).json()["review"]
    assert review["overall_score"] is None
    assert review["review_json"] is None
    assert review["markdown_review"]["path"] == "reviews/article.md"
    assert review["markdown_review"]["content"] == "# Registered review"
    (work / "reviews/article.json").unlink()
    assert client.get(base + "/summary", headers=headers).json()["review"]["markdown_review"]["content"] == "# Registered review"


@pytest.mark.parametrize("invalid", ["corrupt", "traversal", "symlink"])
def test_invalid_paper_registration_is_a_local_document_error(tmp_path: Path, invalid: str):
    work = _make_session(tmp_path, "invalid-paper", owner="alice")
    (work / "paper.pdf").write_text("old paper")
    (work / "review.json").write_text('{"overall_score": 10}')
    outside = tmp_path / "outside.pdf"
    outside.write_text("OUTSIDE_DOCUMENT_MARKER")
    record_path = _register_ui_paper(tmp_path, work.name)
    if invalid == "corrupt":
        record_path.write_text("{invalid")
    elif invalid == "traversal":
        record = json.loads(record_path.read_text())
        record["pdf_path"] = "../outside.pdf"
        record_path.write_text(json.dumps(record))
    else:
        (work / "results").mkdir()
        (work / "results/article.pdf").symlink_to(outside)
    client = TestClient(create_app(workspace_root=tmp_path))
    headers = {"X-Holo-Client": "alice"}
    base = f"/ui/sessions/{work.name}"
    response = client.get(base + "/summary", headers=headers)
    assert response.status_code == 200
    assert "OUTSIDE_DOCUMENT_MARKER" not in response.text
    paper = response.json()["paper"]
    assert paper["document"]["status"] == "error"
    assert paper["document"]["error"]
    assert paper["pdfs"] == []
    assert response.json()["review"]["overall_score"] is None
    assert client.get(base, headers=headers).json()["status"] == "complete"


def test_native_review_summary_preserves_scores_and_distinct_feedback(tmp_path: Path):
    from holosophos.ui_api import build_review_summary

    native = {
        "quality": {"strengths": ["Controls present"], "weaknesses": ["Small sample"], "score": 3},
        "clarity": {"strengths": ["Readable"], "weaknesses": ["Missing units"]},
        "significance": {"strengths": ["Useful method"], "weaknesses": []},
        "originality": {"strengths": [], "weaknesses": ["Known approach"]},
        "overall": {"score": 6, "reasoning": "Borderline"},
        "confidence": {"score": 4, "reasoning": "Checked methods"},
        "questions": ["Can this transfer to another assay?"],
        "limitations": ["One laboratory"],
        "format_issues": ["Figure caption missing"],
    }
    (tmp_path / "review.json").write_text(json.dumps(native))
    summary = build_review_summary(tmp_path, [])
    assert summary["overall_score"] == 6
    assert summary["confidence"] == 4
    assert summary["strengths"] == ["Controls present", "Readable", "Useful method"]
    assert summary["weaknesses"] == ["Small sample", "Missing units", "Known approach"]
    assert summary["questions"] == ["Can this transfer to another assay?"]
    assert summary["limitations"] == ["One laboratory"]
    assert summary["format_issues"] == ["Figure caption missing"]
    assert summary["required_fixes"] == []
    assert summary["recommendation"] is None
    assert summary["review_json"] == native


def test_paper_directory_exposes_article_and_markdown_review_without_inventing_scores(tmp_path: Path):
    work = _make_session(tmp_path, "nested-paper", owner="alice")
    for name, content in {
        "paper/paper.pdf": "%PDF-fixture",
        "paper/paper.tex": "Article source",
        "paper/references.bib": "Bibliography",
        "paper/review.md": "# Рецензия\n\nПроверены методика и результаты.",
        "inputs/review.md": "An input, not this paper's review",
        "draft/review.json": '{"overall_score": 100}',
    }.items():
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    client = TestClient(create_app(workspace_root=tmp_path))
    headers = {"X-Holo-Client": "alice"}
    base = "/ui/sessions/nested-paper"
    summary = client.get(base + "/summary", headers=headers).json()
    assert summary["paper"]["pdfs"] == ["paper/paper.pdf"]
    assert summary["paper"]["references"] == ["paper/references.bib"]
    assert summary["review"]["review_artifacts"] == ["paper/review.md"]
    assert summary["review"]["overall_score"] is None
    assert summary["review"]["markdown_review"]["content"].startswith("# Рецензия")
    assert summary["review"]["markdown_review"]["path"] == "paper/review.md"
    assert client.get("/ui/artifacts/nested-paper/raw", params={"path": "paper/paper.pdf"}, headers=headers).content == b"%PDF-fixture"
    (work / "paper/review.json").write_text('{"overall": {"score": 4}}')
    assert client.get(base + "/summary", headers=headers).json()["review"]["overall_score"] == 4
    (work / "paper.pdf").write_bytes(b"%PDF-root")
    (work / "review.json").write_text('{"overall": {"score": 5}}')
    summary = client.get(base + "/summary", headers=headers).json()
    assert summary["paper"]["pdfs"] == ["paper.pdf"]
    assert summary["review"]["overall_score"] == 5


def test_review_summary_does_not_follow_markdown_links_outside_session(tmp_path: Path):
    work = _make_session(tmp_path, "review-link", owner="alice")
    secret = tmp_path / "outside.md"
    secret.write_text("OUTSIDE_SESSION_MARKER")
    (work / "review.md").symlink_to(secret)
    client = TestClient(create_app(workspace_root=tmp_path))
    response = client.get("/ui/sessions/review-link/summary", headers={"X-Holo-Client": "alice"})
    assert response.status_code == 200
    assert "OUTSIDE_SESSION_MARKER" not in response.text
    assert response.json()["review"]["markdown_review"] is None


def test_invalid_review_json_retains_readable_markdown_fallback(tmp_path: Path):
    work = _make_session(tmp_path, "review-fallback", owner="alice")
    (work / "review.json").write_text("{invalid")
    (work / "review.md").write_text("# Сохранённая рецензия")
    client = TestClient(create_app(workspace_root=tmp_path))
    review = client.get("/ui/sessions/review-fallback/summary", headers={"X-Holo-Client": "alice"}).json()["review"]
    assert review["review_json"] is None
    assert review["markdown_review"]["content"] == "# Сохранённая рецензия"


def test_review_summary_retains_legacy_fields_without_fabricating_scores(tmp_path: Path):
    from holosophos.ui_api import build_review_summary

    (tmp_path / "review.json").write_text(json.dumps({
        "overall_score": 7, "confidence": "high", "decision": "accept",
        "strong_points": ["Reproducible"], "concerns": ["Narrow scope"],
        "required_changes": ["Document seed"],
    }))
    summary = build_review_summary(tmp_path, [])
    assert summary["overall_score"] == 7
    assert summary["confidence"] == "high"
    assert summary["recommendation"] == "accept"
    assert summary["strengths"] == ["Reproducible"]
    assert summary["weaknesses"] == ["Narrow scope"]
    assert summary["required_fixes"] == ["Document seed"]
    (tmp_path / "review.json").write_text(json.dumps({
        "quality": {"score": 4}, "overall": {"reasoning": "No rating"},
        "confidence": {"reasoning": "Uncertain"},
    }))
    summary = build_review_summary(tmp_path, [])
    assert summary["overall_score"] is None
    assert summary["confidence"] is None


def test_original_request_is_not_a_literature_source(tmp_path: Path):
    work = _make_session(tmp_path, "no-literature", owner="alice")
    client = TestClient(create_app(workspace_root=tmp_path))
    headers = {"X-Holo-Client": "alice"}
    base = "/ui/sessions/no-literature"
    brief = client.get(base + "/summary", headers=headers).json()["brief"]
    assert brief["source_paths"] == []
    prompt = next(a for a in client.get(base + "/artifacts", headers=headers).json()["artifacts"] if a["type"] == "prompt")
    assert prompt["tab"] == "run"
    (work / "librarian_results.md").write_text("# Actual literature summary")
    brief = client.get(base + "/summary", headers=headers).json()["brief"]
    assert brief["source_paths"] == ["librarian_results.md"]


def test_control_files_are_excluded_from_artifacts_counts_and_pins(tmp_path: Path):
    work = _make_session(tmp_path, "clean-view", owner="alice")
    for relative, text in {
        "plans/0001/actions.md": "# План",
        "mle/results/metrics.json": '{"mass": 46.07}',
        ".holosophus_ctl/approvals/plan.md": "internal snapshot",
        ".holosophus_ctl/review.json": '{"score": 10}',
        ".holosophus_ctl/paper.pdf": "internal file",
        ".holosophus_ui/iterations/0001/plans/actions.md": "# Сохранённый план",
    }.items():
        path = work / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    client = TestClient(create_app(workspace_root=tmp_path))
    headers = {"X-Holo-Client": "alice"}
    base = "/ui/sessions/clean-view"
    visible = client.get(base + "/artifacts", headers=headers).json()["artifacts"]
    assert {item["path"] for item in visible} == {
        "started_prompt.md",
        "plans/0001/actions.md",
        "mle/results/metrics.json",
    }
    listing = client.get("/ui/sessions", headers=headers).json()["sessions"][0]
    assert sum(listing["artifact_counts"].values()) == len(visible)
    detail = client.get(base, headers=headers).json()
    assert all(
        ".holosophus_ctl" not in item["path"] for item in detail["pinned_artifacts"]
    )
    assert client.get(base + "/summary", headers=headers).json()["run"][
        "workspace_file_count"
    ] == len(visible)
    # Hiding internal files from discovery does not break links to saved plans.
    for path in [
        "plans/0001/actions.md",
        ".holosophus_ui/iterations/0001/plans/actions.md",
    ]:
        response = client.get(
            "/ui/artifacts/clean-view/raw", params={"path": path}, headers=headers
        )
        assert response.status_code == 200
        assert "план" in response.text.lower()


def test_installed_libraries_are_hidden_but_research_files_remain(tmp_path: Path):
    from holosophos.ui_api import list_artifacts

    installed = {
        ".local/lib/python3.11/site-packages/rdkit/README.md",
        ".local/bin/f2py", ".venv/pyvenv.cfg",
        "scratch/env/lib/python3.12/site-packages/numpy/__init__.py",
        "node_modules/some-package/README.md",
    }
    research = {
        "results/0001/result.md", "mle/code/analyze.py",
        "custom/RECORD", "custom/data.unknown", ".custom/notes.md",
    }
    for name in installed | research:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("test")
    visible = list_artifacts(tmp_path)
    assert {item["path"] for item in visible} == research
    diagnostic = list_artifacts(tmp_path, include_ignored=True)
    assert {item["path"] for item in diagnostic if item["ignored"]} == installed
    assert all((tmp_path / name).exists() for name in installed)


@pytest.mark.parametrize("mode", ["research", "debug", "smoke"])
def test_intermediate_or_input_files_do_not_imply_a_full_paper(
    tmp_path: Path, mode: str
):
    work = _make_session(tmp_path, f"{mode}__topic__model__001", owner="alice")
    for name in [
        "calculation.log",
        "metrics.json",
        "figure.png",
        "input.pdf",
        "paper.tex",
        "literature_brief.md",
    ]:
        (work / name).write_text("{}")
    client = TestClient(create_app(workspace_root=tmp_path))
    response = client.get(
        f"/ui/sessions/{work.name}", headers={"X-Holo-Client": "alice"}
    )
    assert response.status_code == 200
    assert response.json()["task_type"] == mode


def test_iteration_history_api_is_owner_scoped_and_keeps_output_boundaries(
    tmp_path: Path,
):
    from holosophos import research_iterations as history

    work = _make_session(tmp_path, "history", owner="alice", orchestrator="claude_code")
    item = history.start_iteration(
        work, kind="initial", user_message="Task", settings={}
    )
    for text in ["First.", "Second."]:
        history.append_event(
            work, item["id"], {"event_type": "output", "content": text}
        )
    (work / "result.md").write_text("Full result")
    history.finish_iteration(
        work, item["id"], status="complete", answer="Done", result_path="result.md"
    )
    client = TestClient(create_app(workspace_root=tmp_path))
    headers = {"X-Holo-Client": "alice"}
    listing = client.get("/ui/sessions/history/iterations", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["latest_result_iteration_id"] == "0001"
    page = client.get("/ui/sessions/history/iterations/0001?limit=1", headers=headers)
    assert page.status_code == 200
    assert page.json()["events"][0]["content"] == "First."
    assert page.json()["has_more"] is True
    assert page.json()["result_content"] == "Full result"
    assert page.json()["answer"] == "Done"
    following = client.get(
        "/ui/sessions/history/iterations/0001?after=1", headers=headers
    )
    assert [e["content"] for e in following.json()["events"]] == ["Second."]
    for path in [
        "/ui/sessions/history/iterations",
        "/ui/sessions/history/iterations/0001",
    ]:
        assert client.get(path, headers={"X-Holo-Client": "bob"}).status_code == 404
        assert client.get(path).status_code == 404
    assert (
        client.get(
            "/ui/sessions/history/iterations/missing", headers=headers
        ).status_code
        == 400
    )
    assert (
        client.get("/ui/sessions/history/iterations/9999", headers=headers).status_code
        == 404
    )
    visible = client.get("/ui/sessions/history/artifacts", headers=headers).json()[
        "artifacts"
    ]
    assert all(not item["path"].startswith(".holosophus_ui/") for item in visible)


def test_legacy_iteration_api_does_not_invent_history_or_create_files(tmp_path: Path):
    work = _make_session(tmp_path, "old", owner="alice")
    (work / "result.md").write_text("Old result")
    client = TestClient(create_app(workspace_root=tmp_path))
    headers = {"X-Holo-Client": "alice"}
    listing = client.get("/ui/sessions/old/iterations", headers=headers)
    assert listing.status_code == 200
    assert listing.json()["iterations"][0]["kind"] == "legacy"
    detail = client.get("/ui/sessions/old/iterations/legacy", headers=headers).json()
    assert detail["result_content"] == "Old result"
    assert detail["events"] == [] and detail["created_at"] is None
    assert not (work / ".holosophus_ui/iterations").exists()


@pytest.mark.parametrize("cached_status", ["complete", "running", "waiting_rate_limit"])
def test_browser_reset_preserves_legacy_before_runner_preflight(
    tmp_path: Path, cached_status
):
    from holosophos import research_iterations as history, research_session

    work = _make_session(tmp_path, "old", owner="alice", orchestrator="claude_code")
    (work / "result.md").write_text("Old result")
    client = TestClient(create_app(workspace_root=tmp_path))
    headers = {"X-Holo-Client": "alice"}
    research_session.save_run_status(work, "complete")
    previous = {
        "status": cached_status,
        "transcriptByAgent": {"manager": "Original message"},
    }
    for state in [previous, {"status": "running", "transcriptByAgent": {}}]:
        response = client.put(
            "/ui/sessions/old/live-state", json=state, headers=headers
        )
        assert response.status_code == 200, response.text
    # No numbered iteration is allocated by the browser's reset.
    assert [i["id"] for i in history.list_iterations(work)["iterations"]] == ["legacy"]
    history.start_iteration(work, kind="feedback", user_message="Fix", settings={})
    (work / "result.md").write_text("New draft")
    history.preserve_legacy(work)  # Repeated resets cannot rewrite the frozen record.
    detail = client.get("/ui/sessions/old/iterations/legacy", headers=headers).json()
    assert detail["legacy_live_state"] == previous
    assert detail["result_content"] == "Old result"


def _make_session(
    workspace: Path,
    name: str,
    owner: str | None = None,
    *,
    orchestrator: str | None = None,
) -> Path:
    session_dir = workspace / name
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "started_prompt.md").write_text("# topic", encoding="utf-8")
    if owner is not None:
        # Write the owner marker via its wire contract (the same path the
        # codearkt session-creation path writes), keeping the test decoupled
        # from codearkt — exactly as ui-api reads it at runtime.
        marker = session_dir / ".holosophus_ui" / "owner"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(owner, encoding="utf-8")
    if orchestrator is not None:
        # The CC runner writes this sibling marker; its absence means codearkt.
        marker = session_dir / ".holosophus_ui" / "orchestrator"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(orchestrator, encoding="utf-8")
    return session_dir


def _session_ids(payload: dict[str, Any]) -> set[str]:
    sessions = payload["sessions"]
    assert isinstance(sessions, list)
    return {str(item["session_id"]) for item in sessions}


def test_owner_sees_only_their_sessions(tmp_path: Path) -> None:
    _make_session(tmp_path, "research__a_topic__model__001", owner="client-A")
    _make_session(tmp_path, "research__b_topic__model__001", owner="client-B")

    client = TestClient(create_app(workspace_root=str(tmp_path)))

    resp_a = client.get("/ui/sessions", headers={"X-Holo-Client": "client-A"})
    assert resp_a.status_code == 200
    assert _session_ids(resp_a.json()) == {"research__a_topic__model__001"}

    resp_b = client.get("/ui/sessions", headers={"X-Holo-Client": "client-B"})
    assert resp_b.status_code == 200
    assert _session_ids(resp_b.json()) == {"research__b_topic__model__001"}


def test_no_header_returns_empty_list(tmp_path: Path) -> None:
    _make_session(tmp_path, "research__a_topic__model__001", owner="client-A")

    client = TestClient(create_app(workspace_root=str(tmp_path)))
    resp = client.get("/ui/sessions")
    assert resp.status_code == 200
    assert _session_ids(resp.json()) == set()


def test_legacy_sessions_hidden_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "UI_SHOW_LEGACY_SESSIONS", False)
    _make_session(tmp_path, "research__a_topic__model__001", owner="client-A")
    _make_session(tmp_path, "research__legacy__model__001", owner=None)

    client = TestClient(create_app(workspace_root=str(tmp_path)))
    resp = client.get("/ui/sessions", headers={"X-Holo-Client": "client-A"})
    assert resp.status_code == 200
    assert _session_ids(resp.json()) == {"research__a_topic__model__001"}


def test_legacy_sessions_shown_when_flag_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "UI_SHOW_LEGACY_SESSIONS", True)
    _make_session(tmp_path, "research__a_topic__model__001", owner="client-A")
    _make_session(tmp_path, "research__legacy__model__001", owner=None)
    _make_session(tmp_path, "research__b_topic__model__001", owner="client-B")

    client = TestClient(create_app(workspace_root=str(tmp_path)))
    resp = client.get("/ui/sessions", headers={"X-Holo-Client": "client-A"})
    assert resp.status_code == 200
    # Own session + legacy session, but never another owner's session.
    assert _session_ids(resp.json()) == {
        "research__a_topic__model__001",
        "research__legacy__model__001",
    }


def test_reserved_workdir_dirs_are_not_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Even with legacy on, the shared paperbanana OUTPUT_DIR (workdir/figures)
    # and dot-dirs are workdir scaffolding, not sessions, and must never list.
    monkeypatch.setattr(settings, "UI_SHOW_LEGACY_SESSIONS", True)
    _make_session(tmp_path, "research__a_topic__model__001", owner="client-A")
    (tmp_path / "figures" / "paperbanana").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".session_cache").mkdir(parents=True, exist_ok=True)

    client = TestClient(create_app(workspace_root=str(tmp_path)))
    resp = client.get("/ui/sessions", headers={"X-Holo-Client": "client-A"})
    assert resp.status_code == 200
    assert _session_ids(resp.json()) == {"research__a_topic__model__001"}


# Detail-endpoint isolation: the owner filter is not merely list-deep — knowing
# a session id must not let a different browser client read its detail.
DETAIL_READ_PATHS = [
    "/ui/sessions/{sid}",
    "/ui/sessions/{sid}/artifacts",
    "/ui/sessions/{sid}/summary",
    "/ui/sessions/{sid}/manifest",
]


def test_session_detail_surfaces_orchestrator_marker(tmp_path: Path) -> None:
    # A Claude-Code session carries the orchestrator marker; its detail surfaces
    # it so the UI can badge the run.
    cc_sid = "research__cc_topic__cc_model__001"
    _make_session(tmp_path, cc_sid, owner="client-A", orchestrator="claude_code")
    # A session without the marker is the default CodeArkt path.
    ca_sid = "research__ca_topic__model__001"
    _make_session(tmp_path, ca_sid, owner="client-A")

    client = TestClient(create_app(workspace_root=str(tmp_path)))

    cc_detail = client.get(
        f"/ui/sessions/{cc_sid}", headers={"X-Holo-Client": "client-A"}
    )
    assert cc_detail.status_code == 200
    assert cc_detail.json()["orchestrator"] == "claude_code"

    ca_detail = client.get(
        f"/ui/sessions/{ca_sid}", headers={"X-Holo-Client": "client-A"}
    )
    assert ca_detail.status_code == 200
    assert ca_detail.json()["orchestrator"] == "codearkt"


def test_cc_session_owner_isolation(tmp_path: Path) -> None:
    # The CC-created session uses the same owner mechanism: the owner sees it in
    # the list and reaches its detail; a different client gets a 404. Owner
    # isolation is orchestrator-agnostic (unchanged by the CC marker).
    cc_sid = "research__cc_topic__cc_model__001"
    _make_session(tmp_path, cc_sid, owner="client-A", orchestrator="claude_code")

    client = TestClient(create_app(workspace_root=str(tmp_path)))

    listed = client.get("/ui/sessions", headers={"X-Holo-Client": "client-A"})
    assert listed.status_code == 200
    assert cc_sid in _session_ids(listed.json())

    for template in DETAIL_READ_PATHS:
        path = template.format(sid=cc_sid)
        assert (
            client.get(path, headers={"X-Holo-Client": "client-A"}).status_code == 200
        )
        assert (
            client.get(path, headers={"X-Holo-Client": "client-B"}).status_code == 404
        )


def test_detail_endpoints_scoped_to_owner(tmp_path: Path) -> None:
    sid = "research__a_topic__model__001"
    _make_session(tmp_path, sid, owner="client-A")
    client = TestClient(create_app(workspace_root=str(tmp_path)))

    for template in DETAIL_READ_PATHS:
        path = template.format(sid=sid)
        # Owner reaches its own session.
        assert (
            client.get(path, headers={"X-Holo-Client": "client-A"}).status_code == 200
        )
        # A different client gets a 404 indistinguishable from a missing session.
        assert (
            client.get(path, headers={"X-Holo-Client": "client-B"}).status_code == 404
        )
        # No identity at all is also refused.
        assert client.get(path).status_code == 404


def test_live_state_scoped_to_owner(tmp_path: Path) -> None:
    sid = "research__a_topic__model__001"
    _make_session(tmp_path, sid, owner="client-A")
    client = TestClient(create_app(workspace_root=str(tmp_path)))

    state = {"status": "running", "events": []}
    # Owner writes and reads back its own live-state.
    assert (
        client.put(
            f"/ui/sessions/{sid}/live-state",
            json=state,
            headers={"X-Holo-Client": "client-A"},
        ).status_code
        == 200
    )
    assert (
        client.get(
            f"/ui/sessions/{sid}/live-state", headers={"X-Holo-Client": "client-A"}
        ).status_code
        == 200
    )
    # Another client can neither overwrite nor read the owner's live-state.
    assert (
        client.put(
            f"/ui/sessions/{sid}/live-state",
            json={"status": "hijacked"},
            headers={"X-Holo-Client": "client-B"},
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/ui/sessions/{sid}/live-state", headers={"X-Holo-Client": "client-B"}
        ).status_code
        == 404
    )


def test_legacy_detail_hidden_by_default_shown_when_flag_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = "research__legacy__model__001"
    _make_session(tmp_path, sid, owner=None)  # untagged legacy session

    monkeypatch.setattr(settings, "UI_SHOW_LEGACY_SESSIONS", False)
    client = TestClient(create_app(workspace_root=str(tmp_path)))
    # Legacy detail is hidden from everyone while the flag is off.
    assert (
        client.get(f"/ui/sessions/{sid}", headers={"X-Holo-Client": "any"}).status_code
        == 404
    )

    monkeypatch.setattr(settings, "UI_SHOW_LEGACY_SESSIONS", True)
    client_on = TestClient(create_app(workspace_root=str(tmp_path)))
    # With the flag on, legacy detail is readable (mirrors the list behavior).
    assert (
        client_on.get(
            f"/ui/sessions/{sid}", headers={"X-Holo-Client": "any"}
        ).status_code
        == 200
    )
