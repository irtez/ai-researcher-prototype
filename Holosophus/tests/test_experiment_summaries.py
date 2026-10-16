"""Experiment explanations are model-authored documents, not runtime logs."""

import json
import pytest

from holosophos import research_iterations as history
from holosophos.ui_api import build_experiment_summary, classify_artifact_path
from result_contract import ResultContract


def summary_artifact(work, path, content):
    target = work / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    classification = classify_artifact_path(path)
    return {"path": path, "type": classification.type}


def test_iteration_context_assigns_summary_directory_and_preserves_previous(tmp_path):
    first = history.start_iteration(tmp_path, kind="initial", user_message="Fit", settings={})
    run = ResultContract(tmp_path, first["id"])
    assert "EXPERIMENT_SUMMARY_DIRECTORY: mle/summaries/0001" in run.query("Fit", mode="initial")
    directory = tmp_path / "mle/summaries/0001"
    assert directory.is_dir()
    (directory / "fit.json").write_text("[]")
    history.finish_iteration(tmp_path, first["id"], status="cancelled")
    second = history.start_iteration(tmp_path, kind="feedback", user_message="Dilution", settings={})
    run = ResultContract(tmp_path, second["id"])
    for mode in ("feedback", "resume"):
        assert "EXPERIMENT_SUMMARY_DIRECTORY: mle/summaries/0002" in run.query("Dilution", mode=mode)
    assert (directory / "fit.json").read_text() == "[]"


def test_summaries_preserve_full_text_and_iteration_order(tmp_path):
    explanation = "Длинное объяснение расчёта.\n" * 180
    markdown = "- **Изменение:** разбавление `5×`.\n- **Результат:** 17 мг/л.\n\nПовторный расчёт не требовался."
    first = summary_artifact(tmp_path, "mle/summaries/0001/calibration.json", json.dumps([
        {"phase": "Калибровка", "summary": explanation, "artifacts": ["mle/code/fit.py"]}
    ]))
    second = summary_artifact(tmp_path, "mle/summaries/0002/dilution.json", json.dumps([
        {"phase": "Разбавление", "summary": markdown, "artifacts": []}
    ]))
    assert first["type"] == "experiment_summary"
    response = build_experiment_summary(tmp_path, [second, first])
    assert response["metrics_tables"] == []
    groups = response["summaries"]
    assert [group["iteration_id"] for group in groups] == ["0001", "0002"]
    assert groups[0]["steps"][0]["summary"] == explanation
    assert groups[0]["steps"][0]["artifacts"] == ["mle/code/fit.py"]
    assert groups[1]["source_path"] == second["path"]
    assert groups[1]["steps"][0]["summary"] == markdown


@pytest.mark.parametrize("content", ["{broken", '{}', '[]', '[{"phase": "Fit"}]', '[{"phase": 2, "summary": "x"}]', '[{"phase":"Fit","summary":"Done","artifacts":[2]}]'])
def test_invalid_summary_is_reported_without_breaking_results(tmp_path, content):
    bad = summary_artifact(tmp_path, "mle/summaries/0001/bad.json", content)
    good = summary_artifact(tmp_path, "mle/results/results.json", '{"mass": 46.07}')
    response = build_experiment_summary(tmp_path, [bad, good])
    assert response["summaries"][0]["error"]
    assert response["summaries"][0]["source_path"] == bad["path"]
    assert response["metrics_tables"][0]["rows"]


def test_legacy_log_stays_available_separately(tmp_path):
    legacy = summary_artifact(tmp_path, "mle/log.jsonl", json.dumps({"phase": "read", "summary": "Old entry"}))
    response = build_experiment_summary(tmp_path, [legacy])
    assert response["summaries"] == []
    assert response["log_phases"][0]["summary"] == "Old entry"


def test_multiple_assignments_and_bad_encoding_remain_separate(tmp_path):
    artifacts = [summary_artifact(tmp_path, f"mle/summaries/0001/{name}.json", json.dumps([
        {"phase": name, "summary": "Done", "artifacts": []}
    ])) for name in ("calibration", "validation")]
    bad = summary_artifact(tmp_path, "mle/summaries/0001/bad.json", "")
    (tmp_path / bad["path"]).write_bytes(b"\xff")
    groups = build_experiment_summary(tmp_path, [*artifacts, bad])["summaries"]
    assert len(groups) == 3
    assert groups[0]["error"] == "invalid_encoding"
    assert [group["steps"][0]["phase"] for group in groups[1:]] == ["calibration", "validation"]


def test_oversized_summary_reports_error_instead_of_truncating(tmp_path):
    from holosophos.ui_api import JSON_PREVIEW_LIMIT_BYTES
    artifact = summary_artifact(tmp_path, "mle/summaries/0001/large.json", json.dumps([
        {"phase": "Stage", "summary": "x" * JSON_PREVIEW_LIMIT_BYTES, "artifacts": []}
    ]))
    group = build_experiment_summary(tmp_path, [artifact])["summaries"][0]
    assert group["error"] == "too_large"
    assert group["steps"] == []
