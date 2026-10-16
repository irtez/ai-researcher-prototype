"""Tests for the Writer PDF-only paper_length tool."""

from __future__ import annotations

from pathlib import Path

import fitz  # type: ignore[import-untyped]
import pytest

from holosophos.tools.writer.paper_length import analyze_paper_length


def _write_synthetic_pdf(path: Path) -> None:
    doc = fitz.open()

    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 72), "Abstract", fontsize=16)
    page.insert_text((72, 120), "This is abstract text.", fontsize=10)
    page.insert_text((72, 260), "1 Introduction", fontsize=16)
    page.insert_text((72, 310), "This is introduction text.", fontsize=10)

    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 90), "2 Related Work", fontsize=16)
    page.insert_text((72, 140), "This is related work text.", fontsize=10)
    page.insert_text((72, 420), "3 Method", fontsize=16)
    page.insert_text((72, 470), "This is method text.", fontsize=10)

    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "4 Experimental Setup", fontsize=16)
    page.insert_text((72, 150), "This is setup text.", fontsize=10)
    page.insert_text((72, 250), "5 Results", fontsize=16)
    page.insert_text((72, 300), "This is results text.", fontsize=10)
    page.insert_text((72, 380), "6 Ablation Study", fontsize=16)
    page.insert_text((72, 430), "This is ablation text.", fontsize=10)
    page.insert_text((72, 500), "7 Limitations", fontsize=16)
    page.insert_text((72, 540), "This is limitations text.", fontsize=10)
    page.insert_text((72, 600), "8 Conclusion", fontsize=16)
    page.insert_text((72, 625), "This is conclusion text.", fontsize=10)
    page.insert_text((72, 650), "References", fontsize=16)
    page.insert_text((72, 700), "[1] A reference.", fontsize=10)

    doc.save(path)
    doc.close()


@pytest.mark.parametrize("heading", ["Введение и обзор литературы", "Introduction and Related Work"])
def test_combined_introduction_is_measured_from_pdf_outline(tmp_path, heading):
    path = tmp_path / "combined.pdf"
    with fitz.open() as doc:
        for _ in range(3):
            doc.new_page()
        doc.set_toc([[1, heading, 1], [1, "Method", 2], [1, "References", 3]])
        doc.save(path)
    report = analyze_paper_length(str(path))
    introduction = next(s for s in report["sections"] if s["component"] == "Introduction")
    assert introduction["start_page"] == 1
    assert introduction["measurement_source"] == "outline_page_fallback"
    assert "section_not_found: Introduction" not in report["diagnostics"]


def _write_pdf_with_outline(path: Path, *, outline_method_page: int = 2) -> None:
    doc = fitz.open()

    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 72), "Abstract", fontsize=16)
    page.insert_text((72, 260), "1 Introduction", fontsize=16)

    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 120), "2 Method", fontsize=16)

    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 80), "3 Conclusion", fontsize=16)
    page.insert_text((72, 300), "References", fontsize=16)
    page.insert_text((72, 500), "Appendix A", fontsize=16)

    doc.set_toc(
        [
            [1, "Abstract", 1],
            [1, "1 Introduction", 1],
            [1, "2 Method", outline_method_page],
            [1, "3 Conclusion", 3],
            [1, "References", 3],
            [1, "Appendix A", 3],
        ]
    )
    doc.save(path)
    doc.close()


def _rendered_document_position(path: Path, title: str) -> float:
    with fitz.open(path) as doc:
        for page_index, page in enumerate(doc):
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    text = " ".join(
                        span.get("text", "") for span in line.get("spans", [])
                    )
                    if text == title:
                        return page_index + float(line["bbox"][1]) / float(
                            page.rect.height
                        )
    raise AssertionError(f"Rendered title not found: {title}")


def test_paper_length_counts_real_pdf_pages(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    _write_synthetic_pdf(pdf_path)

    report = analyze_paper_length(str(pdf_path))

    assert report["tool_name"] == "paper_length"
    assert report["total_pages"] == 3
    assert "status" not in report
    assert "score" not in report
    assert "warnings" not in report
    assert "suggested_actions" not in report


def test_paper_length_detects_visible_sections(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    _write_synthetic_pdf(pdf_path)

    report = analyze_paper_length(str(pdf_path))
    components = [section["component"] for section in report["sections"]]

    assert components == [
        "Abstract",
        "Introduction",
        "Related Work",
        "Method",
        "Experimental Setup",
        "Results",
        "Analysis / Ablations",
        "Limitations / Ethics / Reproducibility",
        "Conclusion",
    ]
    assert all(section["found"] for section in report["sections"])
    assert report["sections"][1]["matched_titles"] == ["1 Introduction"]
    assert "References" in report["other_detected_headings"]


def test_paper_length_computes_page_ranges(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    _write_synthetic_pdf(pdf_path)

    report = analyze_paper_length(str(pdf_path))
    sections = {section["component"]: section for section in report["sections"]}

    assert sections["Abstract"]["start_page"] == 1
    assert sections["Abstract"]["end_page"] == 1
    assert sections["Abstract"]["page_span"] == 1

    assert sections["Introduction"]["start_page"] == 1
    assert sections["Introduction"]["end_page"] == 2
    assert sections["Introduction"]["page_span"] == 2

    assert sections["Experimental Setup"]["start_page"] == 3
    assert sections["Experimental Setup"]["end_page"] == 3
    assert sections["Experimental Setup"]["page_span"] == 1


def test_paper_length_computes_fractional_pages_from_coordinates(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    _write_synthetic_pdf(pdf_path)

    report = analyze_paper_length(str(pdf_path))
    sections = {section["component"]: section for section in report["sections"]}

    assert 0 < sections["Abstract"]["fractional_pages"] < 1
    assert sections["Introduction"]["fractional_pages"] > 0.5
    assert 0 < sections["Limitations / Ethics / Reproducibility"]["fractional_pages"] <= 1


def test_main_content_pages_stops_at_rendered_references(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    _write_synthetic_pdf(pdf_path)

    report = analyze_paper_length(str(pdf_path))

    expected = round(_rendered_document_position(pdf_path, "References"), 2)
    assert report["main_content_pages"] == expected
    assert report["main_content_measurement_source"] == "rendered_heading"


@pytest.mark.parametrize(
    "terminal_title",
    [
        "Acknowledgments",
        "Acknowledgements",
        "References",
        "Bibliography",
        "Appendix A",
    ],
)
def test_main_content_stops_at_top_of_excluded_heading(
    tmp_path: Path,
    terminal_title: str,
) -> None:
    pdf_path = tmp_path / "terminal.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 72), "Abstract", fontsize=16)
    page.insert_text((72, 300), "Discussion", fontsize=16)
    page.insert_text((72, 650), terminal_title, fontsize=16)
    doc.save(pdf_path)
    doc.close()

    report = analyze_paper_length(str(pdf_path))
    expected = round(_rendered_document_position(pdf_path, terminal_title), 2)

    assert report["main_content_pages"] == expected
    assert report["main_content_measurement_source"] == "rendered_heading"
    assert terminal_title in report["other_detected_headings"]


def test_standalone_discussion_remains_main_content(tmp_path: Path) -> None:
    pdf_path = tmp_path / "discussion.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 72), "Abstract", fontsize=16)
    page.insert_text((72, 300), "Discussion", fontsize=16)
    page.insert_text((72, 650), "References", fontsize=16)
    doc.save(pdf_path)
    doc.close()

    report = analyze_paper_length(str(pdf_path))
    expected = round(_rendered_document_position(pdf_path, "References"), 2)

    assert report["main_content_pages"] == expected
    assert "Discussion" in report["other_detected_headings"]


def test_main_content_pages_uses_outline_page_fallback(tmp_path: Path) -> None:
    pdf_path = tmp_path / "outlined.pdf"
    doc = fitz.open()
    doc.new_page(width=612, height=792)
    doc.new_page(width=612, height=792)
    doc.new_page(width=612, height=792)
    doc.set_toc([[1, "Abstract", 1], [1, "References", 3]])
    doc.save(pdf_path)
    doc.close()

    report = analyze_paper_length(str(pdf_path))

    assert report["main_content_pages"] == 2.0
    assert report["main_content_measurement_source"] == "outline_page_fallback"
    assert "main_content_outline_page_fallback" in report["diagnostics"]


def test_main_content_pages_uses_document_end_without_terminal(tmp_path: Path) -> None:
    pdf_path = tmp_path / "paper.pdf"
    doc = fitz.open()
    doc.new_page(width=612, height=792).insert_text((72, 72), "Abstract", fontsize=16)
    doc.save(pdf_path)
    doc.close()

    report = analyze_paper_length(str(pdf_path))

    assert report["main_content_pages"] == 1.0
    assert report["main_content_measurement_source"] == "document_end"


def test_missing_pdf_has_no_main_content_measurement(tmp_path: Path) -> None:
    report = analyze_paper_length(str(tmp_path / "missing.pdf"))

    assert report["main_content_pages"] is None
    assert report["main_content_measurement_source"] is None


def test_paper_length_reports_missing_pdf_diagnostic(tmp_path: Path) -> None:
    report = analyze_paper_length(str(tmp_path / "missing.pdf"))

    assert report["tool_name"] == "paper_length"
    assert report["total_pages"] is None
    assert len(report["sections"]) == 9
    assert all(not section["found"] for section in report["sections"])
    assert report["other_detected_headings"] == []
    assert report["diagnostics"] == ["pdf_missing"]


def test_paper_length_reports_no_section_diagnostic(tmp_path: Path) -> None:
    pdf_path = tmp_path / "no_sections.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 72), "This PDF has body text but no visible section headings.", fontsize=10)
    doc.save(pdf_path)
    doc.close()

    report = analyze_paper_length(str(pdf_path))

    assert report["total_pages"] == 1
    assert len(report["sections"]) == 9
    assert all(not section["found"] for section in report["sections"])
    assert "section_heading_not_found" in report["diagnostics"]
    assert "section_not_found: Abstract" in report["diagnostics"]


def test_paper_length_maps_aliases_and_merges_same_component(tmp_path: Path) -> None:
    pdf_path = tmp_path / "aliases.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 72), "Abstract", fontsize=16)
    page.insert_text((72, 160), "1 Intro", fontsize=16)
    page.insert_text((72, 260), "2 Model", fontsize=16)
    page.insert_text((72, 360), "3 Framework", fontsize=16)
    page.insert_text((72, 460), "4 Evaluation", fontsize=16)
    page.insert_text((72, 560), "5 Error Analysis", fontsize=16)
    doc.save(pdf_path)
    doc.close()

    report = analyze_paper_length(str(pdf_path))
    sections = {section["component"]: section for section in report["sections"]}

    assert sections["Introduction"]["matched_titles"] == ["1 Intro"]
    assert sections["Method"]["matched_titles"] == ["2 Model", "3 Framework"]
    assert sections["Results"]["matched_titles"] == ["4 Evaluation"]
    assert sections["Analysis / Ablations"]["matched_titles"] == ["5 Error Analysis"]


def test_paper_length_uses_rendered_positions_and_stops_at_references(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "outlined.pdf"
    _write_pdf_with_outline(pdf_path)

    report = analyze_paper_length(str(pdf_path))
    sections = {section["component"]: section for section in report["sections"]}

    conclusion = sections["Conclusion"]
    assert conclusion["found"] is True
    assert conclusion["start_page"] == 3
    assert conclusion["end_page"] == 3
    assert conclusion["page_span"] == 1
    assert 0.25 < conclusion["fractional_pages"] < 0.35
    assert conclusion["measurement_source"] == "rendered_heading"
    assert "References" in report["other_detected_headings"]
    assert "Appendix A" in report["other_detected_headings"]


def test_paper_length_uses_outline_when_a_visible_heading_is_missing(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "outline_fallback.pdf"
    doc = fitz.open()
    doc.new_page(width=612, height=792).insert_text(
        (72, 72), "Abstract", fontsize=16
    )
    doc.new_page(width=612, height=792)
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 300), "References", fontsize=16)
    doc.set_toc(
        [[1, "Abstract", 1], [1, "2 Method", 2], [1, "References", 3]]
    )
    doc.save(pdf_path)
    doc.close()

    report = analyze_paper_length(str(pdf_path))
    sections = {section["component"]: section for section in report["sections"]}

    method = sections["Method"]
    assert method["found"] is True
    assert method["start_page"] == 2
    assert method["end_page"] == 3
    assert method["fractional_pages"] == 2.0
    assert method["measurement_source"] == "outline_page_fallback"
    assert "outline_page_fallback: Method" in report["diagnostics"]


def test_paper_length_reports_outline_rendered_page_disagreement(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "outline_disagreement.pdf"
    _write_pdf_with_outline(pdf_path, outline_method_page=1)

    report = analyze_paper_length(str(pdf_path))
    sections = {section["component"]: section for section in report["sections"]}

    assert sections["Method"]["start_page"] == 2
    assert "outline_rendered_page_mismatch: Method" in report["diagnostics"]
