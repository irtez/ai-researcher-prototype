"""Small shared contracts for research sessions (no runtime/LLM dependencies)."""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

RESEARCH_PROFILES = ("general", "chemistry")
CC_DEFAULT_MODEL = "opus"
CC_MODEL_CHOICES = ("opus", "sonnet", "haiku")
INPUT_SUFFIXES = {
    ".csv",
    ".xls",
    ".xlsx",
    ".docx",
    ".json",
    ".jsonl",
    ".png",
    ".jpg",
    ".jpeg",
    ".pdf",
}
MAX_INPUT_BYTES = 20 * 1024 * 1024
# Retain Unicode IDs for saved sessions and explicit CLI workspaces.
_SESSION_ID = re.compile(r"[^\W_][\w.-]{0,199}\Z")


def new_session_id() -> str:
    return "research_" + uuid.uuid4().hex


def title_is_pending(session_dir: Path) -> bool:
    try:
        started = float(read_marker(session_dir, "title_attempted") or "done")
    except ValueError:
        return False
    return 0 <= time.time() - started < 60


def initialize_display_metadata(session_dir: Path, prompt: str) -> None:
    """Write-once display metadata, independent of the folder/SDK identity."""
    values = {
        "title": " ".join(prompt.split())[:120] or "Research task",
        "mode": "research",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    for name, value in values.items():
        path = _marker(session_dir, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as stream:
                stream.write(value)
        except FileExistsError:
            pass


def session_directory(workspace_root: Path, session_id: str) -> Path:
    if not _SESSION_ID.fullmatch(session_id) or session_id == "figures":
        raise ValueError("Invalid session id")
    root = Path(workspace_root).resolve()
    candidate = root / session_id
    if candidate.is_symlink() or candidate.resolve().parent != root:
        raise ValueError("Invalid session directory")
    if candidate.exists() and not candidate.is_dir():
        raise ValueError("Invalid session directory")
    return candidate


def _marker(session_dir: Path, name: str) -> Path:
    root = Path(session_dir).resolve()
    metadata = root / ".holosophus_ui"
    path = metadata / name
    if metadata.is_symlink() or path.is_symlink():
        raise ValueError("Invalid session metadata")
    return path


def read_marker(session_dir: Path, name: str) -> str | None:
    path = _marker(session_dir, name)
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except FileNotFoundError:
        return None


def has_started(session_dir: Path) -> bool:
    return (
        (session_dir / "started_prompt.md").exists()
        or (session_dir / ".holosophus_ctl/session.json").exists()
        or read_marker(session_dir, "orchestrator") is not None
        or read_marker(session_dir, "research_profile") is not None
    )


def read_research_profile(session_dir: Path) -> str:
    profile = read_marker(session_dir, "research_profile") or "general"
    if profile not in RESEARCH_PROFILES:
        raise ValueError("Invalid saved research profile")
    return profile


def read_ui_research_profile(session_dir: Path) -> str | None:
    """Return the displayed profile without classifying a prepared upload.

    Started sessions from before profile markers existed remain ``general``.
    An upload-only directory has no marker and has not started, so ``None``
    preserves the caller's ability to choose chemistry on the first run.
    """
    if read_marker(session_dir, "research_profile") is not None:
        return read_research_profile(session_dir)
    return "general" if has_started(session_dir) else None


def resolve_research_profile(session_dir: Path, requested: str | None = None) -> str:
    if requested is not None and requested not in RESEARCH_PROFILES:
        raise ValueError("Unknown research profile")
    saved = read_research_profile(session_dir)
    if has_started(session_dir):
        if requested is not None and requested != saved:
            raise ValueError("The research profile is fixed for this session")
        return saved
    return requested or "general"


def save_research_profile(session_dir: Path, profile: str) -> None:
    profile = resolve_research_profile(session_dir, profile)
    marker = _marker(session_dir, "research_profile")
    marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        with marker.open("x", encoding="utf-8") as stream:
            stream.write(profile)
    except FileExistsError:
        if read_research_profile(session_dir) != profile:
            raise ValueError("The research profile is fixed for this session") from None


def save_session_model(session_dir: Path, model: str) -> None:
    marker = _marker(session_dir, "model")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(model.strip(), encoding="utf-8")


def read_session_model(session_dir: Path) -> str | None:
    return read_marker(session_dir, "model")


def read_session_settings(session_dir: Path) -> dict:
    """Per-session launch settings, separate from the immutable SDK identity."""
    raw = read_marker(session_dir, "settings.json")
    if raw is None:
        return {}
    try:
        data = json.loads(raw)
    except ValueError as error:
        raise ValueError("Invalid saved session settings") from error
    if not isinstance(data, dict):
        raise ValueError("Invalid saved session settings")
    return data


def save_session_settings(session_dir: Path, settings: dict) -> None:
    marker = _marker(session_dir, "settings.json")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")


def save_run_status(session_dir: Path, status: str) -> None:
    marker = _marker(session_dir, "run_status")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(status, encoding="utf-8")


def check_session_owner(
    session_dir: Path, owner: str | None, *, claim: bool = False
) -> None:
    owner = (owner or "").strip()
    saved = read_marker(session_dir, "owner")
    if saved is not None and saved != owner:
        raise PermissionError("Session not found")
    if claim and owner and saved is None:
        marker = _marker(session_dir, "owner")
        marker.parent.mkdir(parents=True, exist_ok=True)
        try:
            with marker.open("x", encoding="utf-8") as stream:
                stream.write(owner)
        except FileExistsError:
            check_session_owner(session_dir, owner)


def input_paths(session_dir: Path, paths: list[str]) -> list[str]:
    root = Path(session_dir).resolve()
    result = []
    for value in paths:
        path = PurePosixPath(value)
        if (
            "\\" in value
            or PureWindowsPath(value).drive
            or path.is_absolute()
            or len(path.parts) != 2
            or path.parts[0] != "inputs"
            or ".." in path.parts
            or path.suffix.lower() not in INPUT_SUFFIXES
        ):
            raise ValueError("Invalid input file path")
        full = root / path
        if (
            full.is_symlink()
            or not full.resolve().is_relative_to(root / "inputs")
            or not full.is_file()
        ):
            raise ValueError("Input file is missing or outside this session")
        if value not in result:
            result.append(value)
    return result


def with_input_files(question: str, paths: list[str], *, native_read: bool = True) -> str:
    if not paths:
        return question
    names = "\n".join(f"- {name}" for name in paths)
    tool_guidance = (
        "Images can be read with native Read or a suitable available tool. "
        if native_read
        else "Use the available file and image tools to inspect their contents. "
    )
    return (
        f"{question}\n\nAttached input files (relative to this session workspace):\n{names}\n"
        "Read the relevant original attachments and use their contents to answer the task. "
        "Pass their paths to the responsible specialists. "
        f"{tool_guidance}If a file cannot be read, report that explicitly."
    )
