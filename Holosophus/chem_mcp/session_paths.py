"""Resolve chemistry tool inputs inside one research session workspace."""

from __future__ import annotations

import os
import re
from pathlib import Path


_SESSION_ID_PATTERN = re.compile(r"[^\W_][\w.-]{0,199}")


def session_directory(session_id: str) -> Path:
    """Return a validated ``WORKSPACE_DIR/<session_id>`` directory."""
    if not isinstance(session_id, str) or not _SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError(
            "session_id must be 1-200 Unicode letters, digits, dots, underscores, "
            "or hyphens and must start with a letter or digit."
        )

    workspace = Path(os.environ.get("WORKSPACE_DIR", "/workdir")).expanduser().resolve()
    requested_session_dir = workspace / session_id
    session_dir = requested_session_dir.resolve()
    if session_dir.parent != workspace:
        raise ValueError("session_id must resolve to a direct child of WORKSPACE_DIR.")
    if session_dir != requested_session_dir:
        raise ValueError("session_id must not resolve through a symbolic link.")
    return session_dir


def resolve_session_file(file_path: str | Path, session_id: str) -> Path:
    """Resolve a relative file path and keep it inside the selected session."""
    if not isinstance(file_path, (str, Path)) or not str(file_path).strip():
        raise ValueError("file_path must be a non-empty relative path.")

    relative_path = Path(str(file_path).strip())
    if relative_path.is_absolute():
        raise ValueError("file_path must be relative to the current session directory.")

    session_dir = session_directory(session_id)
    candidate = (session_dir / relative_path).resolve()
    try:
        candidate.relative_to(session_dir)
    except ValueError as error:
        raise ValueError("file_path must resolve inside the current session directory.") from error

    if not candidate.is_file():
        raise FileNotFoundError(
            f"Session file '{relative_path.as_posix()}' was not found for session '{session_id}'."
        )
    return candidate


def session_inputs_directory(session_id: str) -> Path:
    """Return the real inputs directory, rejecting symlink redirection."""
    session_dir = session_directory(session_id)
    requested_inputs_dir = session_dir / "inputs"
    inputs_dir = requested_inputs_dir.resolve()
    if inputs_dir != requested_inputs_dir:
        raise ValueError("The session inputs directory must not be a symbolic link.")
    return inputs_dir


__all__ = ["resolve_session_file", "session_directory", "session_inputs_directory"]
