"""Operator command for preparing registered tables; does not launch MCP or an LLM."""

import argparse
import json

from dataset_registry import DatasetRegistry
from dataset_store import DatasetStore, DatasetStoreError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("datasets", nargs="*", help="Registered dataset IDs")
    parser.add_argument("--registry", help="Path to datasets.yaml")
    parser.add_argument("--data-dir", help="Persistent dataset directory")
    parser.add_argument(
        "--preload", action="store_true", help="Prepare datasets marked preload: true"
    )
    parser.add_argument("--refresh", action="store_true", help="Explicitly prepare a new version")
    args = parser.parse_args(argv)
    if bool(args.datasets) == args.preload:
        parser.error("Specify dataset IDs or --preload, exclusively.")
    try:
        store = DatasetStore(DatasetRegistry(args.registry), args.data_dir)
        selected = args.datasets or [s.key for s in store.registry.specs if s.preload]
        prepared = []
        for dataset_id in selected:
            snapshot = store.prepare(dataset_id, refresh=args.refresh)
            prepared.append(
                {
                    "dataset_id": snapshot.metadata["dataset_id"],
                    "row_count": snapshot.metadata["row_count"],
                    "version": snapshot.version,
                    "records_path": str(snapshot.records_path),
                }
            )
        print(json.dumps({"prepared": prepared}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, DatasetStoreError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
