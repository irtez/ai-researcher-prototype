#!/usr/bin/env python3
"""Rebuild JSONL indexes from benchmark metadata manifests."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = REPO_ROOT / "benchmark"
sys.path.insert(0, str(BENCHMARK_ROOT))

from metadata_registry.store import MetadataStore


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--metadata-root",
        type=Path,
        default=BENCHMARK_ROOT / "data" / "metadata",
        help="Metadata registry root",
    )
    args = parser.parse_args()

    store = MetadataStore(args.metadata_root.resolve())
    store.ensure_dirs()

    for kind in ("paper", "distilled", "run", "evaluation"):
        path = store.write_jsonl_index(kind)
        print(path)


if __name__ == "__main__":
    main()
