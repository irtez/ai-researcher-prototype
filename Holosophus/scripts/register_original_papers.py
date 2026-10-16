#!/usr/bin/env python3
"""Register original/gold paper folders in the benchmark metadata registry."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = REPO_ROOT / "benchmark"
sys.path.insert(0, str(BENCHMARK_ROOT))

from metadata_registry.builders import build_original_record
from metadata_registry.store import MetadataStore


def _infer_acl_metadata(paper_dir: Path) -> tuple[str, str, str | None]:
    parts = paper_dir.parts
    if "acl_accepted" in parts:
        parent = paper_dir.parent.name
        match = re.fullmatch(r"(\d{4})_acl", parent)
        if match:
            return "acl", match.group(1), paper_dir.name
    return "unknown", "unknown", paper_dir.name


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gold-root",
        type=Path,
        default=BENCHMARK_ROOT / "data" / "gold" / "acl_accepted" / "2024_acl",
        help="Directory containing original paper folders",
    )
    parser.add_argument("--conference", help="Override conference value")
    parser.add_argument("--year", help="Override year value")
    parser.add_argument(
        "--metadata-root",
        type=Path,
        default=BENCHMARK_ROOT / "data" / "metadata",
        help="Metadata registry root",
    )
    args = parser.parse_args()

    gold_root = args.gold_root.resolve()
    if not gold_root.exists():
        raise SystemExit(f"Gold root does not exist: {gold_root}")

    store = MetadataStore(args.metadata_root.resolve())
    store.ensure_dirs()

    written = []
    for paper_dir in sorted(path for path in gold_root.iterdir() if path.is_dir()):
        inferred_conference, inferred_year, original_paper_id = _infer_acl_metadata(paper_dir)
        conference = args.conference or inferred_conference
        year = args.year or inferred_year
        record = build_original_record(
            paper_dir=paper_dir,
            benchmark_root=BENCHMARK_ROOT,
            conference=conference,
            year=year,
            original_paper_id=original_paper_id,
        )
        path = store.write("paper", record["paper_uid"], record)
        written.append(path)

    for path in written:
        print(path)
    print(f"Registered original papers: {len(written)}")


if __name__ == "__main__":
    main()
