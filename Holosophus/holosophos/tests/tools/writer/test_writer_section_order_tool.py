from pathlib import Path

import pytest

from holosophos.tools.writer.section_order import analyze_section_order


def test_reports_inversion_without_missing_sections(tmp_path: Path) -> None:
    path = tmp_path / "paper.tex"
    path.write_text(
        "\\section{Introduction}\n\\section{Method}\n"
        "\\section{Results}\n\\section{Related Work}\n",
        encoding="utf-8",
    )

    report = analyze_section_order(str(path))

    assert [item["canonical"] for item in report["detected_sections"]] == [
        "introduction",
        "method",
        "results",
        "related_work",
    ]
    assert report["violations"][0]["section"]["canonical"] == "related_work"
    assert report["violations"][0]["after"]["canonical"] == "results"
    assert "missing_sections" not in report


@pytest.mark.parametrize("headings", [
    ["Введение и обзор литературы", "Методика", "Результаты и обсуждение", "Выводы"],
    ["Introduction and Related Work", "Method", "Results and Discussion", "Conclusion"],
])
def test_combined_sections_are_recognized_without_forcing_separate_headings(tmp_path, headings):
    path = tmp_path / "paper.tex"
    path.write_text("\n".join(f"\\section{{{title}}}" for title in headings))
    report = analyze_section_order(str(path))
    assert [item["canonical"] for item in report["detected_sections"]] == [
        "introduction", "method", "results", "conclusion",
    ]
    assert report["violations"] == []
    assert report["other_headings"] == []
