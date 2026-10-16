"""Profile state and input boundaries shared by the UI and Claude runner."""

import sys
from pathlib import Path

import pytest


def test_session_directory_accepts_existing_ui_russian_slug(tmp_path):
    from holosophos.research_session import session_directory

    session_id = "research__проверка_таблица__sonnet__20260907003245"
    assert session_directory(tmp_path, session_id) == tmp_path / session_id


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "holosophos"))
from holosophos import research_session as state


def test_uploaded_session_can_start_in_chemistry_then_cannot_change(tmp_path):
    session = tmp_path / "session"
    (session / "inputs").mkdir(parents=True)
    assert state.resolve_research_profile(session, "chemistry") == "chemistry"
    state.save_research_profile(session, "chemistry")
    assert state.resolve_research_profile(session) == "chemistry"
    with pytest.raises(ValueError, match="profile"):
        state.resolve_research_profile(session, "general")


def test_legacy_started_session_remains_general(tmp_path):
    (tmp_path / "started_prompt.md").write_text("original task")
    assert state.resolve_research_profile(tmp_path) == "general"
    with pytest.raises(ValueError, match="profile"):
        state.resolve_research_profile(tmp_path, "chemistry")


@pytest.mark.parametrize(
    "sid", ["../escape", "/tmp/escape", "a/b", "a\\b", "figures", ".hidden", "a%2fb"]
)
def test_session_path_rejects_non_session_ids(tmp_path, sid):
    with pytest.raises(ValueError):
        state.session_directory(tmp_path, sid)


def test_session_owner_cannot_be_changed(tmp_path):
    state.check_session_owner(tmp_path, "alice", claim=True)
    with pytest.raises(PermissionError):
        state.check_session_owner(tmp_path, "bob", claim=True)
    assert (tmp_path / ".holosophus_ui/owner").read_text() == "alice"


def test_attachment_requires_existing_file_in_session_inputs(tmp_path):
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs/data.csv").write_text("smiles\nCCO\n")
    assert state.input_paths(tmp_path, ["inputs/data.csv"]) == ["inputs/data.csv"]
    for path in [
        "../data.csv",
        "/tmp/data.csv",
        "other/data.csv",
        "inputs/missing.csv",
    ]:
        with pytest.raises(ValueError):
            state.input_paths(tmp_path, [path])


def test_symlink_attachment_cannot_escape_session(tmp_path):
    session = tmp_path / "session"
    (session / "inputs").mkdir(parents=True)
    (tmp_path / "secret.png").write_bytes(b"secret")
    (session / "inputs/picture.png").symlink_to(tmp_path / "secret.png")
    with pytest.raises(ValueError):
        state.input_paths(session, ["inputs/picture.png"])
