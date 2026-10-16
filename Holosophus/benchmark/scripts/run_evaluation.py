#!/usr/bin/env python3
"""Run evaluator on one or more paper directories and produce a consolidated report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from writer.acl.metrics.evaluator import Evaluator as ACLEvaluator
from writer.base.metrics.evaluator import Evaluator as BaseEvaluator


def main():
    parser = argparse.ArgumentParser(description="Evaluate paper packages")
    parser.add_argument("dirs", nargs="+", help="Paper directories to evaluate")
    parser.add_argument(
        "--profile",
        default="acl",
        choices=["base", "acl"],
        help="Writer benchmark profile to use",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--output", "-o", help="Write report to file")
    args = parser.parse_args()

    evaluator_cls = ACLEvaluator if args.profile == "acl" else BaseEvaluator
    evaluator = evaluator_cls()
    all_reports = {}

    for paper_dir in args.dirs:
        paper_dir = Path(paper_dir)
        if not paper_dir.exists():
            print(f"WARNING: {paper_dir} does not exist, skipping", file=sys.stderr)
            continue

        try:
            results = evaluator.evaluate_directory(paper_dir)
        except FileNotFoundError as e:
            print(f"WARNING: {paper_dir}: {e}", file=sys.stderr)
            continue

        if args.json:
            all_reports[str(paper_dir)] = evaluator.report(results)
        else:
            print(f"\n{'#'*70}")
            print(f"# {paper_dir}")
            print(f"{'#'*70}")
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
