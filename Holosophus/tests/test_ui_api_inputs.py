"""Offline HTTP contract tests for UI input uploads and session metadata."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "codearkt"))
sys.path.insert(0, str(PROJECT_DIR / "holosophos"))

from codearkt import auth  # noqa: E402
from holosophos import research_session  # noqa: E402
from holosophos.ui_api import create_app  # noqa: E402


@pytest.fixture(autouse=True)
def auth_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOLO_AUTH_USERS", raising=False)
    monkeypatch.delenv("HOLO_AUTH_SECRET", raising=False)


def test_uuid_session_uses_display_metadata_and_legacy_ids_still_work(tmp_path):
    from holosophos.ui_api import session_payload, list_sessions
    session = tmp_path / research_session.new_session_id()
    session.mkdir()
    research_session.initialize_display_metadata(session, "Сравни этанол и кислоту")
    research_session.save_session_model(session, "sonnet")
    research_session.check_session_owner(session, "owner", claim=True)
    first = session_payload(session)
    assert first["title"] == "Сравни этанол и кислоту"
    assert first["mode"] == "research" and first["model_slug"] == "sonnet"
    (session / ".holosophus_ui/title").write_text("Сравнение молекулярных масс")
    changed = session_payload(session)
    assert changed["title"] == "Сравнение молекулярных масс"
    assert changed["session_id"] == first["session_id"]
    assert changed["created_at"] == first["created_at"]
    found = list_sessions(tmp_path, limit=50, owner="owner", query="молекулярных", mode_filter="research")
    assert [item["session_id"] for item in found] == [session.name]
    legacy = tmp_path / "research__старое_название__sonnet__2026"
    legacy.mkdir()
    assert session_payload(legacy)["title"] == "старое название"


def _upload(
    client: TestClient,
    session_id: str,
    files: list[tuple[str, bytes, str]],
    *,
    owner: str = "browser-owner",
    authorization: str | None = None,
):
    headers = {"X-Holo-Client": owner}
    if authorization is not None:
        headers["Authorization"] = authorization
    return client.post(
        f"/ui/sessions/{session_id}/inputs",
        headers=headers,
        files=[("files", item) for item in files],
    )


@pytest.mark.parametrize(
    "session_id", ["research__molecules__opus__001", "research__молекулы__opus__001"]
)
def test_upload_stores_multiple_inputs_and_existing_file_api_can_read_them(
    tmp_path: Path,
    session_id: str,
) -> None:
    client = TestClient(create_app(workspace_root=tmp_path))

    response = _upload(
        client,
        session_id,
        [
            ("molecules.csv", b"smiles\nCCO\n", "text/csv"),
            ("chart.PNG", b"image", "image/png"),
        ],
    )

    assert response.status_code == 200
    assert response.json() == {
        "files": [
            {"name": "molecules.csv", "path": "inputs/molecules.csv", "size": 11},
            {"name": "chart.PNG", "path": "inputs/chart.PNG", "size": 5},
        ]
    }
    session = tmp_path / session_id
    assert (session / ".holosophus_ui/owner").read_text(
        encoding="utf-8"
    ) == "browser-owner"
    assert not (session / ".holosophus_ui/research_profile").exists()
    raw = client.get(
        f"/ui/artifacts/{session_id}/raw",
        params={"path": "inputs/molecules.csv"},
        headers={"X-Holo-Client": "browser-owner"},
    )
    assert raw.status_code == 200
    assert raw.content == b"smiles\nCCO\n"


@pytest.mark.parametrize(
    "session_id,filename,status_code",
    [
        ("../escape", "data.csv", 404),
        (".hidden", "data.csv", 400),
        ("valid-session", "../data.csv", 400),
        ("valid-session", "nested/data.csv", 400),
        ("valid-session", "data.exe", 400),
    ],
)
def test_upload_rejects_session_and_filename_boundaries_without_input_files(
    tmp_path: Path, session_id: str, filename: str, status_code: int
) -> None:
    client = TestClient(create_app(workspace_root=tmp_path))

    response = _upload(
        client, session_id, [(filename, b"content", "application/octet-stream")]
    )

    assert response.status_code == status_code
    assert not any(path.is_file() for path in tmp_path.rglob("inputs/*"))


def test_invalid_file_late_in_batch_leaves_no_partial_inputs(tmp_path: Path) -> None:
    client = TestClient(create_app(workspace_root=tmp_path))

    response = _upload(
        client,
        "prepared",
        [
            ("valid.csv", b"valid", "text/csv"),
            ("invalid.exe", b"invalid", "application/octet-stream"),
        ],
    )

    assert response.status_code == 400
    assert not (tmp_path / "prepared" / "inputs" / "valid.csv").exists()


def test_oversized_file_late_in_batch_leaves_no_partial_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(research_session, "MAX_INPUT_BYTES", 4)
    client = TestClient(create_app(workspace_root=tmp_path))

    response = _upload(
        client,
        "prepared",
        [
            ("valid.csv", b"four", "text/csv"),
            ("large.json", b"12345", "application/json"),
        ],
    )

    assert response.status_code == 413
    assert not (tmp_path / "prepared" / "inputs" / "valid.csv").exists()
    assert not (tmp_path / "prepared" / "inputs" / "large.json").exists()


def test_upload_never_overwrites_and_rolls_back_other_batch_files(
    tmp_path: Path,
) -> None:
    session = tmp_path / "prepared"
    inputs = session / "inputs"
    inputs.mkdir(parents=True)
    (inputs / "existing.csv").write_bytes(b"original")
    research_session.check_session_owner(session, "browser-owner", claim=True)
    client = TestClient(create_app(workspace_root=tmp_path))

    response = _upload(
        client,
        "prepared",
        [
            ("new.json", b"new", "application/json"),
            ("existing.csv", b"replacement", "text/csv"),
        ],
    )

    assert response.status_code == 409
    assert (inputs / "existing.csv").read_bytes() == b"original"
    assert not (inputs / "new.json").exists()


def test_upload_rejects_started_or_foreign_session(tmp_path: Path) -> None:
    started = tmp_path / "started"
    research_session.check_session_owner(started, "browser-owner", claim=True)
    (started / "started_prompt.md").write_text("running", encoding="utf-8")
    foreign = tmp_path / "foreign"
    research_session.check_session_owner(foreign, "another-owner", claim=True)
    client = TestClient(create_app(workspace_root=tmp_path))

    assert (
        _upload(client, "started", [("data.csv", b"x", "text/csv")]).status_code == 409
    )
    assert (
        _upload(client, "foreign", [("data.csv", b"x", "text/csv")]).status_code == 404
    )
    assert not (started / "inputs").exists()
    assert not (foreign / "inputs").exists()


def test_auth_username_owns_upload_even_with_forged_client_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOLO_AUTH_USERS", "alice:secret")
    monkeypatch.setenv("HOLO_AUTH_SECRET", "test-signing-secret")
    token = auth.mint_token("alice")
    client = TestClient(create_app(workspace_root=tmp_path))

    response = _upload(
        client,
        "authenticated",
        [("data.json", b"{}", "application/json")],
        owner="mallory",
        authorization=f"Bearer {token}",
    )

    assert response.status_code == 200
    assert (tmp_path / "authenticated/.holosophus_ui/owner").read_text() == "alice"


def test_upload_requires_valid_auth_token_when_auth_is_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOLO_AUTH_USERS", "alice:secret")
    monkeypatch.setenv("HOLO_AUTH_SECRET", "test-signing-secret")
    client = TestClient(create_app(workspace_root=tmp_path))

    response = _upload(
        client,
        "authenticated",
        [("data.json", b"{}", "application/json")],
        owner="alice",
    )

    assert response.status_code == 401
    assert not (tmp_path / "authenticated").exists()


def test_config_and_session_metadata_use_shared_cc_models_and_saved_markers(
    tmp_path: Path,
) -> None:
    session = tmp_path / "research__topic__legacy-model__001"
    session.mkdir()
    research_session.check_session_owner(session, "browser-owner", claim=True)
    research_session.save_research_profile(session, "chemistry")
    research_session.save_session_model(session, "sonnet")
    client = TestClient(create_app(workspace_root=tmp_path))

    config = client.get("/ui/config").json()
    listing = client.get(
        "/ui/sessions", headers={"X-Holo-Client": "browser-owner"}
    ).json()
    detail = client.get(
        "/ui/sessions/research__topic__legacy-model__001",
        headers={"X-Holo-Client": "browser-owner"},
    ).json()

    assert config["claude_code_default_model"] == "opus"
    assert {"opus", "sonnet", "haiku"} <= set(config["claude_code_model_choices"])
    assert listing["sessions"][0]["model_slug"] == "sonnet"
    assert listing["sessions"][0]["research_profile"] == "chemistry"
    assert detail["model_slug"] == "sonnet"
    assert detail["research_profile"] == "chemistry"


def test_prepared_session_has_no_profile_but_started_legacy_session_is_general(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(workspace_root=tmp_path))
    assert (
        _upload(client, "prepared", [("data.csv", b"x", "text/csv")]).status_code == 200
    )
    legacy = tmp_path / "research__legacy__old-model__001"
    legacy.mkdir()
    research_session.check_session_owner(legacy, "browser-owner", claim=True)
    (legacy / "started_prompt.md").write_text("started", encoding="utf-8")

    sessions = client.get(
        "/ui/sessions", headers={"X-Holo-Client": "browser-owner"}
    ).json()["sessions"]
    by_id = {item["session_id"]: item for item in sessions}

    assert "research_profile" not in by_id["prepared"]
    assert by_id["research__legacy__old-model__001"]["research_profile"] == "general"
    assert by_id["research__legacy__old-model__001"]["model_slug"] == "old-model"
