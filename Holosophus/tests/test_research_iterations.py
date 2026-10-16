"""Durable iteration history, independent of SDK and browser state."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "holosophos"))


@pytest.fixture
def history():
    from holosophos import research_iterations

    return research_iterations


def start(history, workspace, text="Initial", *, kind="initial", **kwargs):
    return history.start_iteration(
        workspace,
        kind=kind,
        user_message=text,
        settings={"research_profile": "general", "model_name": "sonnet"},
        **kwargs,
    )


def test_three_iterations_keep_messages_snapshots_and_paths(history, tmp_path):
    for number, text in enumerate(["Initial", "Round values", "Shorten"], 1):
        item = start(
            history, tmp_path, text, kind="initial" if number == 1 else "feedback"
        )
        assert item["id"] == f"{number:04}"
        assert item["number"] == number
        assert item["output_path"] == f"results/{number:04}/result.md"
        assert (tmp_path / item["output_path"]).parent.is_dir()
        assert not (tmp_path / item["output_path"]).exists()
        history.update_iteration(
            tmp_path, item["id"], status="running", sdk_session_id="same-sdk"
        )
        history.append_event(
            tmp_path,
            item["id"],
            {
                "event_type": "output",
                "agent_name": "manager",
                "content": text,
            },
        )
        (tmp_path / "result.md").write_text(f"Document {number}")
        history.finish_iteration(
            tmp_path,
            item["id"],
            status="complete",
            answer=f"Done {number}",
            result_path="result.md",
        )
    listing = history.list_iterations(tmp_path)
    assert [item["user_message"] for item in listing["iterations"]] == [
        "Initial",
        "Round values",
        "Shorten",
    ]
    assert listing["active_iteration_id"] is None
    assert listing["latest_result_iteration_id"] == "0003"
    for number in range(1, 4):
        detail = history.read_iteration(tmp_path, f"{number:04}")
        assert detail["result_content"] == f"Document {number}"
        assert detail["answer"] == f"Done {number}"
        assert detail["sdk_session_id"] == "same-sdk"
        assert detail["events"][0]["received_at"]
        assert detail["events"][0]["iteration_id"] == f"{number:04}"
        assert detail["events"][0]["sequence"] == 1
    # A subsequent workdir edit cannot alter published history.
    (tmp_path / "result.md").write_text("Partial next work")
    assert history.read_iteration(tmp_path, "0003")["result_content"] == "Document 3"


def test_active_admission_and_resume_do_not_allocate_another_iteration(
    history, tmp_path
):
    item = start(history, tmp_path)
    with pytest.raises(ValueError, match="active iteration"):
        start(history, tmp_path, "duplicate", kind="feedback")
    history.update_iteration(tmp_path, item["id"], sdk_session_id="resumed-sdk")
    history.update_iteration(tmp_path, item["id"], status="running")
    assert len(history.list_iterations(tmp_path)["iterations"]) == 1


@pytest.mark.parametrize("status", ["error", "cancelled"])
def test_failed_iteration_preserves_last_result_and_does_not_reuse_number(
    history, tmp_path, status
):
    first = start(history, tmp_path)
    (tmp_path / "result.md").write_text("Previous result")
    history.finish_iteration(
        tmp_path, first["id"], status="complete", result_path="result.md"
    )
    second = start(history, tmp_path, "Revise", kind="feedback")
    (tmp_path / "result.md").write_text("Incomplete replacement")
    history.finish_iteration(
        tmp_path,
        second["id"],
        status=status,
        answer="Did not finish",
        result_path="result.md",
    )
    assert (
        history.list_iterations(tmp_path)["latest_result_iteration_id"] == first["id"]
    )
    assert history.read_iteration(tmp_path, second["id"])["result"] is None
    assert start(history, tmp_path, "Retry", kind="feedback")["id"] == "0003"


def test_legacy_history_is_readable_and_frozen_before_feedback(history, tmp_path):
    (tmp_path / ".holosophus_ui").mkdir()
    (tmp_path / "started_prompt.md").write_text("Old task")
    (tmp_path / "result.md").write_text("Old result")
    old_live = {"status": "complete", "transcriptByAgent": {"manager": "Old message"}}
    (tmp_path / ".holosophus_ui/live_state.json").write_text(json.dumps(old_live))
    listing = history.list_iterations(tmp_path)
    assert [item["id"] for item in listing["iterations"]] == ["legacy"]
    assert not (tmp_path / ".holosophus_ui/iterations").exists()
    assert history.read_iteration(tmp_path, "legacy")["legacy_live_state"] == old_live
    item = start(history, tmp_path, "New feedback", kind="feedback")
    assert item["previous_result_iteration_id"] == "legacy"
    (tmp_path / "result.md").write_text("Changed")
    (tmp_path / ".holosophus_ui/live_state.json").write_text("{}")
    old = history.read_iteration(tmp_path, "legacy")
    assert old["result_content"] == "Old result"
    assert old["legacy_live_state"] == old_live
    assert old["number"] is None


def test_recover_only_when_caller_has_confirmed_previous_runner_is_gone(
    history, tmp_path
):
    start(history, tmp_path)
    item = start(history, tmp_path, "Continue", kind="feedback", recover_abandoned=True)
    assert item["id"] == "0002"
    old = history.read_iteration(tmp_path, "0001")
    assert old["status"] == "error"
    assert "no longer active" in old["error"]


def test_events_paginate_and_completed_history_cannot_be_rewritten(history, tmp_path):
    item = start(history, tmp_path)
    for text in ["First.", "Second.", "Third."]:
        history.append_event(
            tmp_path, item["id"], {"event_type": "output", "content": text}
        )
    page = history.read_iteration(tmp_path, item["id"], after=0, limit=2)
    assert [e["content"] for e in page["events"]] == ["First.", "Second."]
    assert page["has_more"] and page["next_after"] == 2
    assert (
        history.read_iteration(tmp_path, item["id"], after=2)["events"][0]["content"]
        == "Third."
    )
    history.finish_iteration(tmp_path, item["id"], status="complete", answer="Original")
    history.finish_iteration(tmp_path, item["id"], status="cancelled", answer="Late")
    assert history.read_iteration(tmp_path, item["id"])["answer"] == "Original"
    with pytest.raises(ValueError, match="finished"):
        history.append_event(tmp_path, item["id"], {"content": "late"})


@pytest.mark.parametrize(
    "component", [".holosophus_ui", ".holosophus_ui/iterations", "results"]
)
def test_storage_rejects_symlinks_without_writing_outside_session(
    history, tmp_path, component
):
    outside = tmp_path / "outside"
    outside.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    target = work / component
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        start(history, work)
    assert not list(outside.iterdir())


def test_snapshot_is_not_published_if_index_commit_fails(
    history, tmp_path, monkeypatch
):
    item = start(history, tmp_path)
    (tmp_path / "result.md").write_text("New document")
    original = history._atomic_write

    def fail_index(path, data):
        if path.name == "index.json":
            raise OSError("disk write failed")
        return original(path, data)

    monkeypatch.setattr(history, "_atomic_write", fail_index)
    with pytest.raises(OSError):
        history.finish_iteration(
            tmp_path, item["id"], status="complete", result_path="result.md"
        )
    assert history.list_iterations(tmp_path)["latest_result_iteration_id"] is None
    assert history.read_iteration(tmp_path, item["id"])["result_content"] is None


def test_event_after_failed_index_write_discards_uncommitted_tail(
    history, tmp_path, monkeypatch
):
    item = start(history, tmp_path)
    original = history._atomic_write

    def fail_index(path, data):
        if path.name == "index.json":
            raise OSError("disk write failed")
        return original(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(history, "_atomic_write", fail_index)
        with pytest.raises(OSError):
            history.append_event(tmp_path, item["id"], {"content": "uncommitted"})
    history.append_event(tmp_path, item["id"], {"content": "committed"})
    events = history.read_iteration(tmp_path, item["id"])["events"]
    assert [(event["sequence"], event["content"]) for event in events] == [
        (1, "committed")
    ]
