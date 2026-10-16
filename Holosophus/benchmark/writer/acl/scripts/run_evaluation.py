#!/usr/bin/env python3
"""Run the ACL writer evaluator on one or more paper directories."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from writer.acl.metrics.evaluator import Evaluator


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate paper packages with writer/acl")
    parser.add_argument("dirs", nargs="+", help="Paper directories to evaluate")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--output", "-o", help="Write report to file")
    args = parser.parse_args()

    evaluator = Evaluator()
    all_reports = {}
    for paper_dir in args.dirs:
        path = Path(paper_dir)
        if not path.exists():
            print(f"WARNING: {path} does not exist, skipping", file=sys.stderr)
            continue
        try:
            results = evaluator.evaluate_directory(path)
        except FileNotFoundError as exc:
            print(f"WARNING: {path}: {exc}", file=sys.stderr)
            continue

        if args.json:
            all_reports[str(path)] = evaluator.report(results)
        else:
            print(f"\n{'#' * 70}")
            print(f"# {path}")
            print(f"{'#' * 70}")
            evaluator.print_report(results)

    if args.json:
        output = json.dumps(all_reports, indent=2, ensure_ascii=False)
        if args.output:
            Path(args.output).write_text(output)
            print(f"Report written to {args.output}", file=sys.stderr)
        else:
            print(output)


if __name__ == "__main__":
    main()

