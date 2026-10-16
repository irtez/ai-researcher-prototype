from pathlib import Path

from academia_mcp.tools.verify_references import (
    _extract_arxiv_id,
    _extract_cite_keys,
    _titles_match,
    verify_references,
)

TEX = r"""
\documentclass{article}
\begin{document}
Transformers \cite{vaswani2017} changed NLP. BERT \citep{devlin2019} too.
% a commented \cite{ghost} must be ignored
A bundled cite \cite{vaswani2017, devlin2019} and a missing one \cite{nosuchkey}.
\bibliography{references}
\end{document}
"""

BIB = r"""
@article{vaswani2017,
  title = {Attention Is All You Need},
  author = {Vaswani, Ashish and others},
  eprint = {1706.03762},
  year = {2017}
}
@article{devlin2019,
  title = {{BERT}: Pre-training of Deep Bidirectional Transformers},
  author = {Devlin, Jacob and others},
  eprint = {1810.04805},
  year = {2019}
}
@article{orphanref,
  title = {An Uncited Paper},
  author = {Lonely, B.},
  year = {2020}
}
"""


def test_extract_cite_keys_ignores_comments() -> None:
    keys = _extract_cite_keys(TEX)
    assert keys == ["vaswani2017", "devlin2019", "nosuchkey"]
    assert "ghost" not in keys


def test_extract_arxiv_id_from_eprint() -> None:
    entry = "@article{x, title = {T}, eprint = {1706.03762}, year = {2017}}"
    assert _extract_arxiv_id(entry) == "1706.03762"


def test_titles_match_handles_braces_and_case() -> None:
    assert _titles_match("Attention Is All You Need", "Attention is All you Need")
    assert not _titles_match(
        "Attention Is All You Need", "A Completely Different Paper"
    )


def test_verify_references_structural(tmp_path: Path) -> None:
    tex_path = tmp_path / "paper.tex"
    bib_path = tmp_path / "references.bib"
    tex_path.write_text(TEX, encoding="utf-8")
    bib_path.write_text(BIB, encoding="utf-8")

    result = verify_references(str(tex_path), verify_existence=False)

    assert result.cite_keys == 3
    assert result.bib_entries == 3
    assert result.resolved == 2
    assert result.missing_in_bib == ["nosuchkey"]
    assert result.orphan_in_bib == ["orphanref"]
    assert result.ok is False
    assert any(issue.kind == "missing_in_bib" for issue in result.issues)
    assert any(issue.kind == "orphan_in_bib" for issue in result.issues)


def test_verify_references_clean(tmp_path: Path) -> None:
    tex = r"\cite{vaswani2017}\cite{devlin2019}"
    bib = "\n".join(BIB.strip().split("@article{orphanref")[0:1])
    tex_path = tmp_path / "clean.tex"
    bib_path = tmp_path / "references.bib"
    tex_path.write_text(tex, encoding="utf-8")
    bib_path.write_text(bib, encoding="utf-8")

    result = verify_references(str(tex_path), verify_existence=False)

    assert result.missing_in_bib == []
    assert result.orphan_in_bib == []
    assert result.resolved == 2
    assert result.ok is True


def test_lookup_failures_are_partial_not_verified(tmp_path, monkeypatch):
    import importlib

    module = importlib.import_module("academia_mcp.tools.verify_references")
    tex = tmp_path / "paper.tex"
    tex.write_text(r"\cite{a,b,c}")
    (tmp_path / "references.bib").write_text(
        "\n".join(f"@article{{{key}, title={{{key}}}}}" for key in "abc")
    )
    monkeypatch.setattr(
        module,
        "_lookup_title",
        lambda arxiv, title: (title, None) if title == "a" else (None, "lookup_failed"),
    )
    result = verify_references(str(tex))
    assert result.ok and result.resolved == 3
    assert getattr(result, "existence_confirmed", -1) == 1
    assert getattr(result, "existence_unavailable", -1) == 2
    assert getattr(result, "existence_status", None) == "partial"
    assert "3 entries verified" not in result.summary


def test_offline_and_capped_verification_do_not_claim_full_check(tmp_path, monkeypatch):
    import importlib

    module = importlib.import_module("academia_mcp.tools.verify_references")
    tex = tmp_path / "paper.tex"
    tex.write_text(r"\cite{a,b}")
    (tmp_path / "references.bib").write_text(
        "@article{a, title={A}}\n@article{b, title={B}}"
    )
    monkeypatch.setattr(module, "_lookup_title", lambda arxiv, title: (title, None))
    offline = verify_references(str(tex), verify_existence=False)
    assert getattr(offline, "existence_status", None) == "not_requested"
    capped = verify_references(str(tex), max_checks=1)
    assert getattr(capped, "existence_status", None) == "partial"
    assert capped.existence_skipped == 1
    assert getattr(capped, "existence_confirmed", -1) == 1


def test_success_and_mismatch_keep_distinct_results(tmp_path, monkeypatch):
    import importlib

    module = importlib.import_module("academia_mcp.tools.verify_references")
    tex = tmp_path / "paper.tex"
    tex.write_text(r"\cite{a}")
    (tmp_path / "references.bib").write_text("@article{a, title={Actual title}}")
    monkeypatch.setattr(module, "_lookup_title", lambda arxiv, title: (title, None))
    clean = verify_references(str(tex))
    assert clean.ok and getattr(clean, "existence_confirmed", -1) == 1
    assert getattr(clean, "existence_status", None) == "complete"
    monkeypatch.setattr(
        module, "_lookup_title", lambda arxiv, title: ("Unrelated record", None)
    )
    mismatch = verify_references(str(tex))
    assert not mismatch.ok and getattr(mismatch, "existence_confirmed", -1) == 0
    assert any(i.kind == "title_mismatch" for i in mismatch.issues)


def test_whole_bibliography_budget_stops_new_lookups(tmp_path, monkeypatch):
    import importlib

    module = importlib.import_module("academia_mcp.tools.verify_references")
    client = importlib.import_module("academia_mcp.s2_client")
    now = [1000.0]
    calls = []
    monkeypatch.setattr(client, "monotonic", lambda: now[0])

    def lookup(arxiv, title):
        calls.append(title)
        now[0] += min(25, client.budget_remaining())
        return None, "lookup_failed"

    monkeypatch.setattr(module, "_lookup_title", lookup)
    tex = tmp_path / "paper.tex"
    tex.write_text(r"\cite{a,b,c,d}")
    (tmp_path / "references.bib").write_text(
        "\n".join(f"@article{{{key}, title={{{key}}}}}" for key in "abcd")
    )
    result = verify_references(str(tex))
    assert calls == ["a", "b", "c"]
    assert now[0] == 1060
    assert result.existence_status == "partial"
    assert result.existence_unavailable == 3
    assert result.existence_skipped == 1
    assert any(i.key == "d" and i.kind == "verification_skipped" for i in result.issues)
    assert client.budget_remaining() == float(
        "inf"
    )  # context is restored for other calls


async def test_partial_result_survives_the_real_mcp_transport(
    tmp_path, monkeypatch, mcp_server_test
):
    import importlib
    from tests.test_server import call_tool

    module = importlib.import_module("academia_mcp.tools.verify_references")
    tex = tmp_path / "paper.tex"
    tex.write_text(r"\cite{a}")
    (tmp_path / "references.bib").write_text("@article{a, title={A source}}")
    monkeypatch.setattr(
        module, "_lookup_title", lambda arxiv, title: (None, "lookup_failed")
    )
    result = await call_tool(
        mcp_server_test, "verify_references", {"tex_filename": str(tex)}
    )
    assert result["ok"] is True
    assert result["resolved"] == 1
    assert result["existence_status"] == "partial"
    assert result["existence_confirmed"] == 0
    assert result["existence_unavailable"] == 1
