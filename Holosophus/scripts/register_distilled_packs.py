#!/usr/bin/env python3
"""Register distilled benchmark packs in the metadata registry."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = REPO_ROOT / "benchmark"
sys.path.insert(0, str(BENCHMARK_ROOT))

from metadata_registry.builders import build_distilled_record, read_json_object
from metadata_registry.naming import parse_variant_id
from metadata_registry.store import MetadataStore


def _iter_pack_dirs(distilled_root: Path) -> list[Path]:
    pack_dirs: list[Path] = []
    for paper_dir in sorted(path for path in distilled_root.iterdir() if path.is_dir()):
        for pack_dir in sorted(path for path in paper_dir.iterdir() if path.is_dir()):
            try:
                parse_variant_id(pack_dir.name)
            except ValueError:
                continue
            pack_dirs.append(pack_dir)
    return pack_dirs


def _resolve_paper_record(store: MetadataStore, pack_dir: Path, parsed: dict[str, str]) -> dict | None:
    paper_record = store.read("paper", parsed["paper_uid"])
    if paper_record is not None:
        return paper_record

    manifest = read_json_object(pack_dir / "manifest.json")
    manifest_paper_id = manifest.get("paper_id")
    if not isinstance(manifest_paper_id, str) or not manifest_paper_id:
        return None

    for record in store.iter_records("paper"):
        if record.get("original_paper_id") == manifest_paper_id:
            return record

    legacy_match = re.fullmatch(r"paper_(\d+)", manifest_paper_id)
    if legacy_match is None:
        return None

    paper_number = legacy_match.group(1)
    candidates = [
        record
        for record in store.iter_records("paper")
        if str(record.get("conference")) == parsed["conference"]
        and str(record.get("year")) == parsed["year"]
        and str(record.get("original_paper_id", "")).endswith(f".{paper_number}")
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--distilled-root",
        type=Path,
        default=BENCHMARK_ROOT / "data" / "benchmark_distilled",
        help="Directory containing distilled pack folders",
    )
    parser.add_argument(
        "--metadata-root",
        type=Path,
        default=BENCHMARK_ROOT / "data" / "metadata",
        help="Metadata registry root",
    )
    args = parser.parse_args()

    distilled_root = args.distilled_root.resolve()
    if not distilled_root.exists():
        raise SystemExit(f"Distilled root does not exist: {distilled_root}")

    store = MetadataStore(args.metadata_root.resolve())
    store.ensure_dirs()

    written = []
    for pack_dir in _iter_pack_dirs(distilled_root):
        parsed = parse_variant_id(pack_dir.name)
        paper_record = _resolve_paper_record(store, pack_dir, parsed)
        record = build_distilled_record(
            pack_dir=pack_dir,
            benchmark_root=BENCHMARK_ROOT,
            paper_record=paper_record,
        )
        path = store.write("distilled", record["distilled_variant_id"], record)
        store.merge_paper_links(
            record["paper_uid"],
            distilled_variant_ids=[record["distilled_variant_id"]],
        )
        written.append(path)

    for path in written:
        print(path)
    print(f"Registered distilled packs: {len(written)}")


if __name__ == "__main__":
    main()
