"""Run the Writer paper_length tool on PDF examples.

This script is for deterministic, factual inspection of the metrics returned by
``holosophos.tools.writer.paper_length.analyze_paper_length``.

Examples:
    uv run python tests/tools/writer/check_paper_length_examples.py
    uv run python tests/tools/writer/check_paper_length_examples.py --output /tmp/paper_length_metrics.json
    uv run python tests/tools/writer/check_paper_length_examples.py --pdf-dir tests/tools/writer/paper_examples
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from holosophos.tools.writer.paper_length import analyze_paper_length


DEFAULT_PDF_DIR = Path(__file__).parent / "paper_examples"


def analyze_pdf_dir(pdf_dir: Path) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for pdf_path in sorted(pdf_dir.glob("*.pdf")):
        report = analyze_paper_length(str(pdf_path))
        reports.append(
            {
                "pdf": str(pdf_path),
                "filename": pdf_path.name,
                "report": report,
                "summary": _summary(report),
            }
        )
    return reports


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pdf-dir",
        type=Path,
        default=DEFAULT_PDF_DIR,
        help="Directory containing PDF examples.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write full JSON metrics.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if any canonical A* component is missing.",
    )
    args = parser.parse_args()

    if not args.pdf_dir.is_dir():
        print(f"PDF directory not found: {args.pdf_dir}")
        return 2

    results = analyze_pdf_dir(args.pdf_dir)
    if not results:
        print(f"No PDFs found in: {args.pdf_dir}")
        return 2

    _print_summary(results)

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nWrote full metrics JSON: {args.output}")

    has_parse_failure = any(result["report"].get("total_pages") is None for result in results)
    has_missing_component = any(result["summary"]["missing_components"] for result in results)
    if has_parse_failure:
        return 2
    if args.strict and has_missing_component:
        return 1
    return 0


def _summary(report: dict[str, Any]) -> dict[str, Any]:
    sections = report.get("sections", [])
    found_components = [
        section["component"]
        for section in sections
        if section.get("found")
    ]
    missing_components = [
        section["component"]
        for section in sections
        if not section.get("found")
    ]
    fractional_pages = {
        section["component"]: section["fractional_pages"]
        for section in sections
        if section.get("found")
    }
    return {
        "total_pages": report.get("total_pages"),
        "found_components": found_components,
        "missing_components": missing_components,
        "fractional_pages": fractional_pages,
        "other_detected_headings": report.get("other_detected_headings", []),
        "diagnostics": report.get("diagnostics", []),
    }


def _print_summary(results: list[dict[str, Any]]) -> None:
    for result in results:
        summary = result["summary"]
        print(f"\n{result['filename']}")
        print(f"  total_pages: {summary['total_pages']}")
        print(f"  found_components: {len(summary['found_components'])}")
        if summary["missing_components"]:
            print("  missing_components:")
            for component in summary["missing_components"]:
                print(f"    - {component}")
        else:
            print("  missing_components: none")

        print("  fractional_pages:")
        for component, pages in summary["fractional_pages"].items():
            print(f"    - {component}: {pages}")

        if summary["other_detected_headings"]:
            print("  other_detected_headings:")
            for heading in summary["other_detected_headings"][:12]:
                print(f"    - {heading}")
            if len(summary["other_detected_headings"]) > 12:
                remaining = len(summary["other_detected_headings"]) - 12
                print(f"    ... {remaining} more")

        if summary["diagnostics"]:
            print("  diagnostics:")
            for diagnostic in summary["diagnostics"]:
                print(f"    - {diagnostic}")


if __name__ == "__main__":
    raise SystemExit(main())
