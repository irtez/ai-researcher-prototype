"""Russian and English documents follow the same deterministic writer checks."""

from pathlib import Path

import fitz
import pytest
from holosophos.tools.writer.content_placement import analyze_content_placement
from holosophos.tools.writer.paper_length import analyze_paper_length
from holosophos.tools.writer.section_order import analyze_section_order


def test_shipped_russian_template_has_recognized_sections_and_artifacts(tmp_path):
    root = Path(__file__).resolve().parents[4]
    template = (
        root / "academia_mcp/academia_mcp/latex_templates/research_ru/research_ru.tex"
    )
    source = template.read_text(encoding="utf-8").replace(
        r"\section{Результаты}",
        "\\section{Результаты}\n\\begin{figure}\n\\end{figure}",
    )
    path = tmp_path / "paper.tex"
    path.write_text(source, encoding="utf-8")
    order = analyze_section_order(str(path))
    assert [item["canonical"] for item in order["detected_sections"]] == [
        "abstract",
        "introduction",
        "method",
        "results",
        "discussion",
        "conclusion",
    ]
    assert not order["other_headings"]
    assert not order["violations"]
    placement = analyze_content_placement(str(path))
    assert placement["findings"][0]["section"]["canonical"] == "results"
    assert placement["findings"][0]["classification"] == "allowed"


@pytest.mark.parametrize(
    "titles",
    [
        ("Заключение", "Введение"),
        ("Conclusion", "Введение"),
        ("Заключение", "Introduction"),
    ],
)
def test_russian_and_mixed_section_inversions_are_detected(tmp_path, titles):
    path = tmp_path / "paper.tex"
    path.write_text("\n".join(f"\\section{{{title}}}" for title in titles))
    report = analyze_section_order(str(path))
    assert len(report["violations"]) == 1
    assert report["violations"][0]["section"]["canonical"] == "introduction"
    assert report["violations"][0]["after"]["canonical"] == "conclusion"


@pytest.mark.parametrize(
    "heading,environment,canonical,classification",
    [
        (r"\begin{abstract}", "table", "abstract", "forbidden"),
        (r"\section{Введение}", "equation", "introduction", "discouraged"),
        (r"\section{Материалы и методы}", "algorithm", "method", "allowed"),
        (r"\section{Обзор литературы}", "lstlisting", "related_work", "forbidden"),
        (r"\section{Результаты}", "table", "results", "allowed"),
        (r"\section{Обсуждение}", "figure", "discussion", "allowed"),
        (r"\section{Анализ ошибок}", "equation", "analysis", "allowed"),
        (r"\section{Дополнительный анализ}", "figure", "analysis", "allowed"),
        (r"\section{Заключение}", "figure", "conclusion", "discouraged"),
        (r"\section{Список литературы}", "figure", "references", "forbidden"),
        (r"\section{Приложение А}", "proof", "appendix", "allowed"),
    ],
)
def test_russian_artifact_placement_uses_existing_policy(
    tmp_path,
    heading,
    environment,
    canonical,
    classification,
):
    path = tmp_path / "paper.tex"
    path.write_text(f"{heading}\n\\begin{{{environment}}}\nx\n\\end{{{environment}}}")
    finding = analyze_content_placement(str(path))["findings"][0]
    assert finding["section"]["canonical"] == canonical
    assert finding["classification"] == classification


def test_unknown_mixed_heading_is_not_reduced_to_english_keyword(tmp_path):
    path = tmp_path / "paper.tex"
    path.write_text("\\section{Предварительные Results}\n\\begin{table}\\end{table}")
    order = analyze_section_order(str(path))
    assert not order["detected_sections"]
    assert order["other_headings"][0]["title"] == "Предварительные Results"
    assert (
        analyze_content_placement(str(path))["findings"][0]["classification"]
        == "unknown_section"
    )


def _write_headings_pdf(path, titles):
    # Built-in font supports Cyrillic and uses identical metrics in both fixtures.
    font = fitz.Font("cjk")
    with fitz.open() as document:
        page = document.new_page(width=612, height=792)
        page.insert_font(fontname="fixture", fontbuffer=font.buffer)
        for title, y in zip(titles, [72, 150, 240, 330, 420, 510, 650]):
            page.insert_text((50, y), title, fontname="fixture", fontsize=16)
        document.save(path)


@pytest.mark.parametrize(
    "russian_end,english_end",
    [
        ("Список литературы", "References"),
        ("Библиография", "Bibliography"),
        ("Благодарности", "Acknowledgments"),
        ("Приложение А. Дополнительные расчёты", "Appendix A. Additional calculations"),
    ],
)
def test_russian_pdf_boundaries_match_english(tmp_path, russian_end, english_end):
    russian = tmp_path / "russian.pdf"
    english = tmp_path / "english.pdf"
    _write_headings_pdf(
        russian,
        [
            "Аннотация",
            "1 Введение",
            "2 Материалы и методы",
            "3 Результаты",
            "4 Обсуждение",
            "5 Заключение",
            russian_end,
        ],
    )
    _write_headings_pdf(
        english,
        [
            "Abstract",
            "1 Introduction",
            "2 Method",
            "3 Results",
            "4 Discussion",
            "5 Conclusion",
            english_end,
        ],
    )
    ru = analyze_paper_length(str(russian))
    en = analyze_paper_length(str(english))
    assert ru["main_content_measurement_source"] == "rendered_heading"
    assert ru["main_content_pages"] == en["main_content_pages"] < 1
    assert russian_end in ru["other_detected_headings"]
    assert "4 Обсуждение" in ru["other_detected_headings"]
    comparable = lambda report: [
        (item["component"], item["found"], item["fractional_pages"])
        for item in report["sections"]
    ]
    assert comparable(ru) == comparable(en)
    assert ru["diagnostics"] == en["diagnostics"]


def test_russian_outline_excludes_bibliography_and_appendix(tmp_path):
    path = tmp_path / "outlined.pdf"
    with fitz.open() as document:
        for _ in range(4):
            document.new_page()
        document.set_toc(
            [
                [1, "Аннотация", 1],
                [1, "Введение", 1],
                [1, "Методы", 2],
                [1, "Список литературы", 3],
                [1, "Приложение Б", 4],
            ]
        )
        document.save(path)
    report = analyze_paper_length(str(path))
    assert report["main_content_pages"] == 2.0
    assert report["main_content_measurement_source"] == "outline_page_fallback"
    assert "Приложение Б" in report["other_detected_headings"]


@pytest.mark.parametrize("title", ["1 Solubility estimates", "1 Оценка растворимости"])
def test_numbered_scientific_heading_counts_as_main_body(tmp_path, title):
    path = tmp_path / "scientific.pdf"
    _write_headings_pdf(path, [title])
    report = analyze_paper_length(str(path))
    assert title in report["other_detected_headings"]
    assert "main_content_heading_not_found" not in report["diagnostics"]
    assert report["main_content_pages"] == 1.0
