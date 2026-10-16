"""Workspace path helpers for Writer MCP tool adapters."""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


@dataclass(frozen=True)
class ResolvedWorkspacePath:
    absolute: Path
    relative: str
    fingerprint: str


def _fingerprint(path: Path) -> str:
    if not path.is_file():
        return "missing"

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_workspace_file(
    workspace_dir: str | Path,
    relative_path: str,
    expected_suffix: str,
) -> ResolvedWorkspacePath:
    logical = PurePosixPath(relative_path)
    if not relative_path.strip() or logical.is_absolute() or ".." in logical.parts:
        raise ValueError("path must be a non-empty workspace-relative path")
    if logical.suffix.lower() != expected_suffix:
        raise ValueError(f"path must end with {expected_suffix}")

    root = Path(workspace_dir).resolve()
    candidate = (root / Path(*logical.parts)).resolve(strict=False)
    if not candidate.is_relative_to(root):
        raise ValueError("path resolves outside workspace")
    if candidate.exists() and not candidate.is_file():
        raise ValueError("path is not a file")

    return ResolvedWorkspacePath(candidate, logical.as_posix(), _fingerprint(candidate))


def normalize_report_paths(report: dict[str, Any], workspace_dir: str | Path) -> dict[str, Any]:
    root = Path(workspace_dir).resolve()
    normalized = copy.deepcopy(report)

    def display(value: str) -> str:
        path = Path(value).resolve(strict=False)
        if not path.is_relative_to(root):
            return "outside-workspace"
        return path.relative_to(root).as_posix()

    if isinstance(normalized.get("source_files"), list):
        normalized["source_files"] = [display(item) for item in normalized["source_files"]]

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "file" and isinstance(child, str):
                    value[key] = display(child)
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(normalized)
    return normalized
