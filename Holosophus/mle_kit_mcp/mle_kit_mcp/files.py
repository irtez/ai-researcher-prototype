import re
from pathlib import Path
from typing import Optional

from mle_kit_mcp.settings import settings


def normalize_session_id(session_id: str) -> str:
    """Validate a single directory component without changing its identity.

    Older UI sessions contain Unicode. Sanitizing them creates a second tree
    invisible to the runner. Existing sanitized directories remain untouched.
    """
    if not re.fullmatch(r"[^\W_][\w.-]{0,199}", session_id) or session_id == "figures":
        raise ValueError("Invalid session id")
    return session_id


def get_workspace_dir(session_id: Optional[str] = None) -> Path:
    assert settings.WORKSPACE_DIR is not None, "Please set the WORKSPACE_DIR environment variable"
    directory = Path(settings.WORKSPACE_DIR).resolve()
    if session_id:
        root = directory
        directory = root / normalize_session_id(session_id)
        if directory.is_symlink() or directory.resolve().parent != root:
            raise ValueError("Invalid session directory")
    if not directory.exists():
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def resolve_session_path(session_id: str, value: str | None = None) -> Path:
    root = get_workspace_dir(session_id)
    path = Path(value or ".")
    # The executor always mounts the shared workspace at /workdir, even when
    # the MCP server itself uses a different filesystem mount point.
    if path.is_absolute() and path.is_relative_to("/workdir"):
        path = get_workspace_dir() / path.relative_to("/workdir")
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not path.is_relative_to(root):
        raise ValueError("Path must belong to the current session")
    return path


def get_host_workspace_dir() -> Path:
    directory_str = settings.HOST_WORKSPACE_DIR or settings.WORKSPACE_DIR
    assert (
        directory_str is not None
    ), "Please set the WORKSPACE_DIR or HOST_WORKSPACE_DIR environment variable"
    directory = Path(directory_str)
    if not directory.exists():
        directory.mkdir(parents=True, exist_ok=True)
    return directory
