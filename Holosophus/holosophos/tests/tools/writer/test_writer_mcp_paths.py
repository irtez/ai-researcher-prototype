"""Tests for Writer MCP workspace path helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from holosophos.tools.writer.mcp_paths import (
    normalize_report_paths,
    resolve_workspace_file,
)


def test_resolves_and_hashes_workspace_file(tmp_path: Path) -> None:
    paper = tmp_path / "session" / "paper.tex"
    paper.parent.mkdir()
    paper.write_text("\\section{Introduction}\n", encoding="utf-8")

    resolved = resolve_workspace_file(tmp_path, "session/paper.tex", ".tex")

    assert resolved.absolute == paper.resolve()
    assert resolved.relative == "session/paper.tex"
    assert len(resolved.fingerprint) == 64


@pytest.mark.parametrize("value", ["/etc/passwd", "../paper.tex", "", "."])
def test_rejects_invalid_workspace_paths(tmp_path: Path, value: str) -> None:
    with pytest.raises(ValueError):
        resolve_workspace_file(tmp_path, value, ".tex")


def test_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.tex"
    outside.write_text("secret", encoding="utf-8")
    (tmp_path / "paper.tex").symlink_to(outside)

    with pytest.raises(ValueError, match="outside workspace"):
        resolve_workspace_file(tmp_path, "paper.tex", ".tex")


def test_missing_file_stays_inside_workspace(tmp_path: Path) -> None:
    resolved = resolve_workspace_file(tmp_path, "session/missing.pdf", ".pdf")

    assert resolved.fingerprint == "missing"


def test_normalizes_nested_file_fields(tmp_path: Path) -> None:
    paper = tmp_path / "session" / "paper.tex"
    paper.parent.mkdir()
    paper.write_text("x", encoding="utf-8")
    report = {
        "source_files": [str(paper)],
        "findings": [{"location": {"file": str(paper), "line": 1}}],
    }

    normalized = normalize_report_paths(report, tmp_path)

    assert normalized["source_files"] == ["session/paper.tex"]
    assert normalized["findings"][0]["location"]["file"] == "session/paper.tex"
