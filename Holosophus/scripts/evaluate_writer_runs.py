#!/usr/bin/env python3
"""Evaluate existing Writer run workdirs without rerunning Writer.

This script is the post-hoc counterpart to `run_writer_pack.py --evaluator`.
It reads run metadata from the registry, evaluates already generated paper
packages in `workdir/<session_id>/`, writes `evaluation_<profile>.json` beside
the paper outputs, and registers evaluation records in the metadata registry.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = REPO_ROOT / "benchmark"
sys.path.insert(0, str(BENCHMARK_ROOT))

from metadata_registry.store import MetadataStore  # type: ignore[import-not-found]
from writer_pack_evaluation import evaluate_and_record_run


REQUIRED_OUTPUTS = ("paper.tex", "references.bib", "paper.pdf")


def _resolve_workdir(run_record: dict[str, Any], workdir_root: Path) -> Path:
    """Resolve the workdir for a run record."""
    relpath = run_record.get("workdir_relpath")
    if isinstance(relpath, str) and relpath:
        return (REPO_ROOT / relpath).resolve()
    return (workdir_root / str(run_record["session_id"])).resolve()


def _missing_outputs(workdir: Path) -> list[str]:
    """Return required Writer outputs missing from a workdir."""
    return [name for name in REQUIRED_OUTPUTS if not (workdir / name).exists()]


def _resolve_run_records(
    store: MetadataStore,
    session_ids: list[str],
) -> list[dict[str, Any]]:
    """Resolve explicit session ids or all registered runs."""
    if session_ids:
        records: list[dict[str, Any]] = []
        for session_id in session_ids:
            record = store.read("run", session_id)
            if record is None:
                raise SystemExit(f"Run metadata not found for session_id: {session_id}")
            records.append(record)
        return records
    return store.iter_records("run")


def _write_indexes(store: MetadataStore) -> None:
    """Refresh JSONL indexes after registry updates."""
    for kind in ("paper", "distilled", "run", "evaluation"):
        path = store.write_jsonl_index(kind)
        print(f"Index updated: {path}")


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(
        description="Evaluate existing Writer runs and register evaluation metadata"
    )
    parser.add_argument(
        "session_ids",
        nargs="*",
        help="Optional session ids to evaluate. If omitted, evaluate all registered complete runs.",
    )
    parser.add_argument(
        "--profile",
        choices=["acl", "base"],
        default="acl",
        help="Evaluator profile to run",
    )
    parser.add_argument(
        "--workdir-root",
        type=Path,
        default=REPO_ROOT / "workdir",
        help="Fallback workdir root when run metadata lacks workdir_relpath",
    )
    parser.add_argument(
        "--metadata-root",
        type=Path,
        default=BENCHMARK_ROOT / "data" / "metadata",
        help="Benchmark metadata registry root",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Recompute evaluation even when eval__<session_id>__<profile> already exists",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be evaluated without running evaluators",
    )
    parser.add_argument(
        "--no-rebuild-index",
        action="store_true",
        help="Do not rebuild metadata JSONL indexes after evaluation",
    )
    args = parser.parse_args()

    store = MetadataStore(args.metadata_root.resolve())
    store.ensure_dirs()
    run_records = _resolve_run_records(store, args.session_ids)
    if not run_records:
        raise SystemExit("No run metadata records found.")

    evaluated = 0
    skipped = 0
    failed = 0

    for run_record in run_records:
        session_id = str(run_record["session_id"])
        workdir = _resolve_workdir(run_record, args.workdir_root.resolve())
        evaluation_id = f"eval__{session_id}__{args.profile}"
        existing_eval_path = store.path_for("evaluation", evaluation_id)

        if existing_eval_path.exists() and not args.overwrite:
            skipped += 1
            print(f"Skipping existing evaluation: {evaluation_id}")
            continue

        if not workdir.exists():
            skipped += 1
            print(f"Skipping missing workdir for {session_id}: {workdir}", file=sys.stderr)
            continue

        missing = _missing_outputs(workdir)
        if missing:
            skipped += 1
            print(
                f"Skipping incomplete run {session_id}; missing: {', '.join(missing)}",
                file=sys.stderr,
            )
            continue

        print(f"Evaluating {session_id} with profile={args.profile}")
        if args.dry_run:
            continue

        try:
            # Work on a shallow copy so a failed evaluation cannot partially
            # mutate the in-memory source record before we decide what to write.
            updated_run_record = dict(run_record)
            updated_run_record.update(
                evaluate_and_record_run(
                    profile=args.profile,
                    workdir=workdir,
                    session_id=session_id,
                    run_record=updated_run_record,
                    store=store,
                    benchmark_root=BENCHMARK_ROOT,
                    repo_root=REPO_ROOT,
                )
            )
            updated_run_record.pop("evaluation_error", None)
            store.write("run", session_id, updated_run_record)
            evaluated += 1
        except Exception as exc:
            failed += 1
            print(f"Evaluation failed for {session_id}: {exc}", file=sys.stderr)

    print(f"Completed evaluation. Evaluated: {evaluated}. Skipped: {skipped}. Failed: {failed}.")
    if not args.dry_run and not args.no_rebuild_index:
        _write_indexes(store)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
