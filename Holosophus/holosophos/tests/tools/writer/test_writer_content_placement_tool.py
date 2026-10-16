"""Tests for deterministic Writer LaTeX artifact-placement analysis."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from holosophos.tools.writer.content_placement import (
    POLICY_PATH,
    analyze_content_placement,
)


def _write_tex(path: Path, source: str) -> Path:
    path.write_text(source.lstrip(), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("section", "artifact", "classification", "artifact_type"),
    [
        ("Abstract", "\\begin{table}\n\\end{table}", "forbidden", "table"),
        ("Introduction", "\\begin{equation}\nx = y\n\\end{equation}", "discouraged", "equation"),
        ("Method", "\\begin{algorithm}\n\\end{algorithm}", "allowed", "algorithm"),
        ("Results", "\\begin{figure}\n\\end{figure}", "allowed", "figure"),
        ("Appendix", "\\begin{theorem}\nP.\n\\end{theorem}", "allowed", "theorem"),
        ("Appendix", "\\begin{proof}\nDone.\n\\end{proof}", "allowed", "proof"),
        (
            "Related Work",
            "\\begin{lstlisting}\nx = 1\n\\end{lstlisting}",
            "forbidden",
            "code_listing",
        ),
    ],
)
def test_classifies_artifacts_by_section(
    tmp_path: Path,
    section: str,
    artifact: str,
    classification: str,
    artifact_type: str,
) -> None:
    tex_path = _write_tex(tmp_path / "report.tex", f"\\section{{{section}}}\n{artifact}\n")

    report = analyze_content_placement(str(tex_path))

    finding = report["findings"][0]
    assert report["tool_name"] == "content_placement"
    assert report["policy_name"] == "a_star_ml_ai_nlp_content_placement_default"
    assert finding["artifact_type"] == artifact_type
    assert finding["classification"] == classification
    assert finding["location"] == {"file": str(tex_path), "line": 2}


def test_expands_include_and_ignores_comments_macros_and_listing_contents(
    tmp_path: Path,
) -> None:
    child = _write_tex(
        tmp_path / "child.tex",
        "\\subsection{Details}\n\\begin{table}\n\\end{table}\n",
    )
    root = _write_tex(
        tmp_path / "report.tex",
        r"""
\newcommand{\example}{\begin{table}\end{table}}
\section{Experimental Setup}
% \begin{figure}\end{figure}
\begin{lstlisting}
\begin{table}\end{table}
\end{lstlisting}
\input{child}
""",
    )

    report = analyze_content_placement(str(root))

    assert report["source_files"] == [str(root), str(child)]
    assert [item["artifact_type"] for item in report["findings"]] == [
        "code_listing",
        "table",
    ]
    assert report["findings"][0]["classification"] == "allowed"
    assert report["findings"][1]["section"]["canonical"] == "experiments"
    assert report["findings"][1]["location"] == {"file": str(child), "line": 2}


def test_reports_unknown_and_malformed_context_without_raising(tmp_path: Path) -> None:
    tex_path = _write_tex(
        tmp_path / "report.tex",
        "\\section{Novel Narrative}\n\\begin{figure}\n\\input{missing}\n",
    )

    report = analyze_content_placement(str(tex_path))

    assert report["findings"][0]["classification"] == "unknown_section"
    assert report["findings"][0]["severity"] == "warning"
    assert "include_missing: missing.tex" in report["diagnostics"]
    assert "unclosed_environment: figure" in report["diagnostics"]


def test_deduplicates_nested_visuals_and_detects_display_math(tmp_path: Path) -> None:
    tex_path = _write_tex(
        tmp_path / "report.tex",
        "\\section{Method}\n\\begin{figure}\n\\begin{tikzpicture}\\end{tikzpicture}\n"
        "\\end{figure}\n\\[\nx = y\n\\]\n",
    )

    report = analyze_content_placement(str(tex_path))

    assert [item["artifact_type"] for item in report["findings"]] == [
        "figure",
        "equation",
    ]
    assert report["summary"]["allowed"] == 2


def test_exports_content_placement_analyzer() -> None:
    from holosophos.tools.writer import analyze_content_placement as exported

    assert exported is analyze_content_placement


def test_ignores_multiline_macro_definition_body(tmp_path: Path) -> None:
    tex_path = _write_tex(
        tmp_path / "report.tex",
        "\\newcommand{\\example}[1]{\n\\begin{table}\n#1\n\\end{table}\n}\n" "\\section{Method}\n",
    )

    assert analyze_content_placement(str(tex_path))["findings"] == []


def test_uses_custom_policy_environment_aliases(tmp_path: Path) -> None:
    tex_path = _write_tex(
        tmp_path / "report.tex",
        "\\section{Method}\n\\begin{mytheorem}P\\end{mytheorem}\n",
    )
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    policy["artifact_aliases"]["environments"]["mytheorem"] = "theorem"

    report = analyze_content_placement(str(tex_path), policy=policy)

    assert report["findings"][0]["artifact_type"] == "theorem"
    assert report["findings"][0]["classification"] == "allowed"


def test_rejects_includes_outside_source_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.tex"
    outside.write_text("\\section{Results}\n\\begin{figure}\\end{figure}\n", encoding="utf-8")
    session = tmp_path / "session"
    session.mkdir()
    root = _write_tex(session / "paper.tex", "\\input{../../outside}\n")

    report = analyze_content_placement(str(root), source_root=str(tmp_path))

    assert report["findings"] == []
    assert any(
        item.startswith("include_outside_workspace:")
        for item in report["diagnostics"]
    )


@pytest.mark.parametrize("heading", [
    "Введение и обзор литературы", "Introduction and Related Work",
    "Introduction and Literature Review", "Introduction & Background",
])
def test_combined_introduction_does_not_inherit_abstract(tmp_path, heading):
    path = _write_tex(tmp_path / "paper.tex", (
        "\\begin{abstract}\nSummary.\n\\end{abstract}\n"
        f"\\section{{{heading}}}\n\\begin{{figure}}\\end{{figure}}\n"
    ))
    finding = analyze_content_placement(str(path))["findings"][0]
    assert finding["section"]["canonical"] == "introduction"
    assert finding["classification"] == "allowed"


def test_unknown_sections_and_siblings_use_only_their_actual_parent(tmp_path):
    path = _write_tex(tmp_path / "paper.tex", r"""
\section{Abstract}
\section{Study setting}
\begin{table}\end{table}
\subsection{Cohort}
\begin{table}\end{table}
\section{Method}
\subsection{Results}
\subsubsection{Details}
\begin{equation}x=y\end{equation}
\subsection{Procedure details}
\begin{equation}x=y\end{equation}
\section{Supplementary observations}
\begin{table}\end{table}
""")
    findings = analyze_content_placement(str(path))["findings"]
    assert [f["section"]["canonical"] for f in findings] == [
        None, None, "results", "method", None,
    ]
    assert [f["classification"] for f in findings] == [
        "unknown_section", "unknown_section", "discouraged", "allowed", "unknown_section",
    ]
    assert all(f["severity"] != "error" for f in findings)


def test_abstract_environment_ends_before_unsectioned_content(tmp_path):
    path = _write_tex(tmp_path / "paper.tex", (
        "\\begin{abstract}\nSummary.\n\\end{abstract}\n"
        "\\begin{table}\\end{table}\n"
    ))
    assert analyze_content_placement(str(path))["findings"][0]["classification"] == "unknown_section"


@pytest.mark.parametrize(("source", "classification"), [
    (r"\begin{abstract}Summary.\end{abstract}" + "\n" + r"\begin{table}\end{table}", "unknown_section"),
    ("\\begin{abstract}\n" + r"\begin{equation}x=y\end{equation}\end{abstract}", "forbidden"),
    ("\\begin{abstract}\n" + r"\[x=y\]\end{abstract}", "forbidden"),
    (r"\begin{abstract}Summary.\end{abstract}\includegraphics{plot}", "unknown_section"),
])
def test_abstract_boundaries_follow_environment_token_order(tmp_path, source, classification):
    path = _write_tex(tmp_path / "paper.tex", source)
    assert analyze_content_placement(str(path))["findings"][0]["classification"] == classification


def test_heading_after_abstract_on_same_line_retains_its_children(tmp_path):
    path = _write_tex(tmp_path / "paper.tex", (
        "\\begin{abstract}\nSummary.\n"
        "\\end{abstract}\\section{Introduction}\n"
        "\\begin{figure}\\end{figure}\n"
        "\\subsection{Motivation}\n\\begin{figure}\\end{figure}\n"
    ))
    findings = analyze_content_placement(str(path))["findings"]
    assert [f["section"]["canonical"] for f in findings] == ["introduction", "introduction"]
    assert all(f["classification"] == "allowed" for f in findings)
