"""Feedback uploads preserve old versions and cannot race iteration admission."""

import io

import pytest
from fastapi.testclient import TestClient

from holosophos import research_iterations as history, research_session
from holosophos.ui_api import create_app


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.delenv("HOLO_AUTH_USERS", raising=False)
    monkeypatch.delenv("HOLO_AUTH_SECRET", raising=False)
    work = tmp_path / "session"
    research_session.check_session_owner(work, "owner", claim=True)
    (work / ".holosophus_ui/orchestrator").write_text("claude_code")
    (work / ".holosophus_ctl").mkdir()
    (work / ".holosophus_ctl/session.json").write_text('{"session_id":"saved"}')
    (work / "inputs").mkdir()
    (work / "inputs/data.csv").write_bytes(b"original")
    item = history.start_iteration(
        work, kind="initial", user_message="Task", settings={}
    )
    history.finish_iteration(work, item["id"], status="complete")
    return work, TestClient(create_app(workspace_root=tmp_path))


def upload(client, *, key="a" * 32, content=b"new", owner="owner", name="data.csv"):
    return client.post(
        f"/ui/sessions/session/inputs?feedback_upload_id={key}",
        headers={"X-Holo-Client": owner},
        files=[("files", (name, content, "text/csv"))],
    )


def test_versions_retries_and_iteration_binding(setup):
    work, client = setup
    first = upload(client)
    assert first.status_code == 200, first.text
    assert upload(client).json() == first.json()
    path = first.json()["files"][0]["path"]
    assert (work / path).read_bytes() == b"new"
    assert (work / "inputs/data.csv").read_bytes() == b"original"
    assert len(list((work / "inputs").iterdir())) == 2
    assert upload(client, content=b"changed").status_code == 409
    second = upload(client, key="b" * 32, content=b"another")
    assert second.status_code == 200
    assert second.json()["files"][0]["path"] != path
    item = history.start_iteration(
        work,
        kind="feedback",
        user_message="Use new data",
        settings={"input_files": [path], "feedback_upload_id": "a" * 32},
    )
    assert item["settings"]["input_attachments"] == first.json()["files"]
    assert upload(client, key="c" * 32).status_code == 409
    history.finish_iteration(work, item["id"], status="complete")
    with pytest.raises(ValueError, match="already belong"):
        history.start_iteration(
            work, kind="feedback", user_message="Duplicate", settings=item["settings"]
        )
    assert len(history._index(work)["iterations"]) == 2


@pytest.mark.parametrize(
    "kwargs,code",
    [
        ({"owner": "other"}, 404),
        ({"key": "../bad"}, 400),
        ({"name": "../data.csv"}, 400),
        ({"name": "bad.exe"}, 400),
    ],
)
def test_rejected_uploads_leave_old_files(setup, kwargs, code):
    work, client = setup
    assert upload(client, **kwargs).status_code == code
    assert list((work / "inputs").iterdir()) == [work / "inputs/data.csv"]


def test_failed_batch_and_changed_file_do_not_publish(setup, monkeypatch):
    work, client = setup
    monkeypatch.setattr(research_session, "MAX_INPUT_BYTES", 3)
    response = client.post(
        "/ui/sessions/session/inputs?feedback_upload_id=" + "a" * 32,
        headers={"X-Holo-Client": "owner"},
        files=[("files", ("one.csv", b"123")), ("files", ("two.csv", b"1234"))],
    )
    assert response.status_code == 413
    assert len(list((work / "inputs").iterdir())) == 1
    saved = upload(client).json()["files"][0]
    (work / saved["path"]).write_text("tampered")
    with pytest.raises(ValueError, match="changed"):
        history.start_iteration(
            work,
            kind="feedback",
            user_message="Use file",
            settings={"input_files": [saved["path"]], "feedback_upload_id": "a" * 32},
        )


def test_admission_during_upload_prevents_publication(setup):
    from holosophos.feedback_inputs import store_batch
    from fastapi import HTTPException, UploadFile

    work, _ = setup

    class Race(io.BytesIO):
        started = False

        def read(self, size):
            if not self.started:
                self.started = True
                history.start_iteration(
                    work, kind="feedback", user_message="Other tab", settings={}
                )
            return super().read(size)

    with pytest.raises(HTTPException) as error:
        store_batch(
            work,
            "owner",
            "a" * 32,
            [UploadFile(file=Race(b"new"), filename="data.csv")],
            ["data.csv"],
        )
    assert error.value.status_code == 409
    assert len(list((work / "inputs").iterdir())) == 1


def test_symlink_inputs_are_rejected(setup, tmp_path):
    work, client = setup
    (work / "inputs/data.csv").unlink()
    (work / "inputs").rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (work / "inputs").symlink_to(outside, target_is_directory=True)
    assert upload(client).status_code == 400
    assert not list(outside.iterdir())
