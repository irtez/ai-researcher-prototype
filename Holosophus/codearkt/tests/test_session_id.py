import os
from pathlib import Path

import pytest

from codearkt import session_id as session_id_module


def test_same_prompt_keeps_topic_across_models(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workdir = tmp_path / "workdir"
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(session_id_module, "_find_workdir", lambda: workdir)

    calls = {"count": 0}

    def fake_summary(_: str) -> str:
        calls["count"] += 1
        # If cache does not work, the second call would produce another topic.
        return f"topic_variant_{calls['count']}"

    monkeypatch.setattr(session_id_module, "_summarize_topic_with_llm", fake_summary)

    prompt = (
        "Давай проведем исследование по агентному бенчмаркингу автоматизированной "
        "квантзации на edge-устройствах."
    )

    sid_gemini = session_id_module.generate_session_id(
        prompt,
        mode="research",
        explicit_model_name="google/gemini-3-flash-preview",
    )
    sid_deepseek = session_id_module.generate_session_id(
        prompt,
        mode="research",
        explicit_model_name="deepseek/deepseek-v3.2",
    )

    assert sid_gemini == "research__topic_variant_1__gemini_3_flash_preview__001"
    assert sid_deepseek == "research__topic_variant_1__deepseek_v3_2__001"
    assert calls["count"] == 1

    cache_file = workdir / ".session_topic_cache.json"
    assert cache_file.exists()


def test_ensure_session_scaffold_bridges_paperbanana(tmp_path: Path) -> None:
    workspace = tmp_path / "workdir"
    session_dir = workspace / "research__topic__model__001"
    session_dir.mkdir(parents=True, exist_ok=True)

    session_id_module.ensure_session_scaffold(session_dir)

    session_pb = session_dir / "figures" / "paperbanana"
    pb_global = workspace / "figures" / "paperbanana"

    # A symlink (not a real dir) bridging the per-session path to the shared dir.
    assert session_pb.is_symlink()
    # The shared OUTPUT_DIR must exist and the link must resolve onto it.
    assert pb_global.is_dir()
    assert session_pb.resolve() == pb_global.resolve()
    # RELATIVE target so host (make smoke) and container (/workdir) resolve alike.
    assert not os.path.isabs(os.readlink(session_pb))


def test_ensure_session_scaffold_is_idempotent(tmp_path: Path) -> None:
    workspace = tmp_path / "workdir"
    session_dir = workspace / "research__topic__model__001"
    session_dir.mkdir(parents=True, exist_ok=True)

    session_id_module.ensure_session_scaffold(session_dir)
    session_pb = session_dir / "figures" / "paperbanana"
    first_target = os.readlink(session_pb)

    # Second call must neither raise nor replace the existing link.
    session_id_module.ensure_session_scaffold(session_dir)
    assert session_pb.is_symlink()
    assert os.readlink(session_pb) == first_target


def test_generate_session_id_creates_figures_bridge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workdir = tmp_path / "workdir"
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(session_id_module, "_find_workdir", lambda: workdir)
    monkeypatch.setattr(
        session_id_module, "_summarize_topic_with_llm", lambda _: "linear_attention"
    )

    sid = session_id_module.generate_session_id(
        "research on linear attention",
        mode="research",
        explicit_model_name="deepseek/deepseek-v3.2",
    )

    session_pb = workdir / sid / "figures" / "paperbanana"
    assert session_pb.is_symlink()
    assert session_pb.resolve() == (workdir / "figures" / "paperbanana").resolve()


def test_owner_marker_round_trip(tmp_path: Path) -> None:
    session_dir = tmp_path / "workdir" / "research__topic__model__001"
    session_dir.mkdir(parents=True, exist_ok=True)

    # An untagged (legacy) session reads back as None.
    assert session_id_module.read_owner_marker(session_dir) is None

    session_id_module.write_owner_marker(session_dir, "client-A")
    assert session_id_module.read_owner_marker(session_dir) == "client-A"

    # Marker lives under the ignored .holosophus_ui dir (never an artifact).
    marker = session_dir / ".holosophus_ui" / "owner"
    assert marker.is_file()


def test_write_owner_marker_ignores_empty_client(tmp_path: Path) -> None:
    session_dir = tmp_path / "workdir" / "research__topic__model__002"
    session_dir.mkdir(parents=True, exist_ok=True)

    session_id_module.write_owner_marker(session_dir, None)
    session_id_module.write_owner_marker(session_dir, "")
    session_id_module.write_owner_marker(session_dir, "   ")
    assert session_id_module.read_owner_marker(session_dir) is None


def test_write_owner_marker_is_write_once(tmp_path: Path) -> None:
    session_dir = tmp_path / "workdir" / "research__topic__model__003"
    session_dir.mkdir(parents=True, exist_ok=True)

    session_id_module.write_owner_marker(session_dir, "client-A")
    # A second browser must not silently take over an already-owned session.
    session_id_module.write_owner_marker(session_dir, "client-B")
    assert session_id_module.read_owner_marker(session_dir) == "client-A"
