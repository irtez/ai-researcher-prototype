#!/usr/bin/env python3
"""Assemble the critic-replay fixtures from a pulled research session.

Design: ``docs/plans/2026-08-18-self-improving-loop-verification.md`` (Approved).

The ground-truth files (``f3_defects.json``, ``f4_defects.json``,
``f5_actions.json``, ``v3_*.json``) are hand-written and committed. This script
assembles the *artifacts* those files adjudicate, so the snapshot is
reproducible rather than hand-placed, and so a future contributor can see
exactly which paths it came from.

Source material is a session directory pulled off the run host::

    ssh admin@ai-researcher.tech
    # session: /opt/prod/ai_scientists/Holosophus/workdir/
    #          _role_you_are_an_llm_agents_causal_decision-making__20260811_1805/

Pull the per-iteration trees (``mle/results_iter3``,
``mle/results_iter4_snapshot``, ``mle/results_iter5``, ``mle/code_iter3``,
``paper_iter3/``, ``main_iter4.pdf``) plus the hand-written iteration briefs, and
point ``--session`` at the result. ``mle/data`` (250 MB) and ``mle/checkpoints``
(21 MB) are deliberately not part of any fixture.

Usage::

    python scripts/build_critic_fixtures.py --session <dir> [--briefs <dir>] \
        [--legal <dir>] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = PROJECT_DIR / "holosophos" / "evals_and_reports" / "data" / "critics"

# Records a critic must never be handed: raw corpora and model checkpoints are
# huge and, for the replay, beside the point - every audited defect is decidable
# from the result trees.
EXCLUDE_DIRS = {"data", "checkpoints", "proxy", "__pycache__", ".ipynb_checkpoints"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".lock"}


def _copy_tree(src: Path, dst: Path, *, dry: bool) -> tuple[int, int]:
    """Copy a record tree, skipping bulk and build noise. Returns (files, bytes)."""
    if not src.is_dir():
        print(f"  ! missing, skipped: {src}")
        return 0, 0
    files = size = 0
    for item in sorted(src.rglob("*")):
        if item.is_dir() or any(p in EXCLUDE_DIRS for p in item.relative_to(src).parts):
            continue
        if item.suffix in EXCLUDE_SUFFIXES:
            continue
        target = dst / item.relative_to(src)
        files += 1
        size += item.stat().st_size
        if not dry:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
    return files, size


def _copy_file(src: Path, dst: Path, *, dry: bool) -> tuple[int, int]:
    if not src.is_file():
        print(f"  ! missing, skipped: {src}")
        return 0, 0
    if not dry:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return 1, src.stat().st_size


def build(session: Path, briefs: Path | None, legal: Path | None, dry: bool) -> dict:
    """Lay out F3, F4, F5 and the cross-domain holdout FG under data/critics/."""
    manifest: dict[str, dict] = {}

    def record(name: str, entries: list[tuple[str, int, int]]) -> None:
        manifest[name] = {
            "files": sum(e[1] for e in entries),
            "bytes": sum(e[2] for e in entries),
            "contents": {e[0]: e[1] for e in entries},
        }

    # ── F3: the wrong-numbers fixture ────────────────────────────────────────
    # A published paper plus the record tree its numbers are supposed to come
    # from. code/ is included as the negative control: the analysis critic must
    # derive from the records rather than re-run the code that produced them.
    print("F3 — iteration-3 paper + records")
    f3 = FIXTURES_DIR / "f3"
    entries = [
        ("paper/main.tex", *_copy_file(session / "paper_iter3" / "main.tex", f3 / "paper" / "main.tex", dry=dry)),
        (
            "paper/references.bib",
            *_copy_file(session / "paper_iter3" / "references.bib", f3 / "paper" / "references.bib", dry=dry),
        ),
        ("mle/results", *_copy_tree(session / "mle" / "results_iter3", f3 / "mle" / "results", dry=dry)),
        ("mle/code", *_copy_tree(session / "mle" / "code_iter3", f3 / "mle" / "code", dry=dry)),
    ]
    record("F3", entries)

    # ── F4: the clean-but-insufficient fixture ───────────────────────────────
    # Self-audit-clean numerically. The .tex was overwritten in place by
    # iteration 5, so the write-up survives only as PDF.
    print("F4 — iteration-4 write-up + records")
    f4 = FIXTURES_DIR / "f4"
    entries = [
        ("paper/main.pdf", *_copy_file(session / "main_iter4.pdf", f4 / "paper" / "main.pdf", dry=dry)),
        ("mle/results", *_copy_tree(session / "mle" / "results_iter4_snapshot", f4 / "mle" / "results", dry=dry)),
    ]
    record("F4", entries)

    # ── F5: the planner answer key ───────────────────────────────────────────
    print("F5 — hand-written iteration-5 brief + the records it was written from")
    f5 = FIXTURES_DIR / "f5"
    entries = [("mle/results", *_copy_tree(session / "mle" / "results_iter5", f5 / "mle" / "results", dry=dry))]
    if briefs:
        entries.append(("brief.md", *_copy_file(briefs / "iteration5_prompt.md", f5 / "brief.md", dry=dry)))
        entries.append(("task.md", *_copy_file(briefs / "intervene_or_continue_prompt.md", f5 / "task.md", dry=dry)))
        # Iteration 4's brief is a second planner pair (verdict on 3 -> brief for 4)
        # and states each audited defect with its true value: it is where
        # f3_defects.json is transcribed from.
        entries.append(("iteration4_brief.md", *_copy_file(briefs / "iteration4_prompt.md", f5 / "iteration4_brief.md", dry=dry)))
    record("F5", entries)

    # ── FG: the cross-domain holdout ─────────────────────────────────────────
    # A different study entirely: legal retrieval, markdown write-up instead of
    # LaTeX, .json/.npy records. Guards against critics that learned one study's
    # shape rather than how to check work.
    if legal:
        print("FG — cross-domain holdout (legal retrieval)")
        fg = FIXTURES_DIR / "fg"
        entries = [
            ("writeup/research_protocol.md", *_copy_file(legal / "research_protocol.md", fg / "writeup" / "research_protocol.md", dry=dry)),
            ("writeup/graph_report.md", *_copy_file(legal / "graph_report.md", fg / "writeup" / "graph_report.md", dry=dry)),
            ("task.md", *_copy_file(legal / "started_prompt.md", fg / "task.md", dry=dry)),
            ("mle/results", *_copy_tree(legal / "mle" / "results", fg / "mle" / "results", dry=dry)),
        ]
        record("FG", entries)

    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    ap.add_argument("--session", required=True, type=Path, help="pulled session dir (paper_iter3/, mle/results_iter*/)")
    ap.add_argument("--briefs", type=Path, help="dir holding the hand-written iteration*_prompt.md briefs")
    ap.add_argument("--legal", type=Path, help="a second, different study, for the cross-domain holdout")
    ap.add_argument(
        "--upstream",
        help="where the session was pulled FROM (host:path) -- --session is a local "
        "staging dir that will not exist tomorrow, this is the durable provenance",
    )
    ap.add_argument("--dry-run", action="store_true", help="report what would be copied, write nothing")
    args = ap.parse_args()

    if not args.session.is_dir():
        print(f"no such session dir: {args.session}", file=sys.stderr)
        return 1

    manifest = build(args.session, args.briefs, args.legal, args.dry_run)

    print("\n" + "=" * 62)
    total_files = total_bytes = 0
    for name, info in manifest.items():
        print(f"  {name:<4} {info['files']:>5} files  {info['bytes'] / 1048576:>7.2f} MB")
        for path, count in info["contents"].items():
            print(f"         {path:<24} {count:>5}")
        total_files += info["files"]
        total_bytes += info["bytes"]
    print(f"  {'':<4} {total_files:>5} files  {total_bytes / 1048576:>7.2f} MB  total")

    if args.dry_run:
        print("\ndry run — nothing written")
        return 0

    out = FIXTURES_DIR / "fixtures.json"
    out.write_text(
        json.dumps(
            {
                "note": "Written by scripts/build_critic_fixtures.py. Ground truth (f*_defects.json, f5_actions.json, v3_*.json) is hand-written and NOT generated by this script.",
                # Name only. The full path is a staging directory on whichever
                # machine ran the build -- it will not exist tomorrow and means
                # nothing to anyone else. `upstream` is the durable provenance.
                "source_session": args.session.name,
                "upstream": args.upstream or "not recorded — pass --upstream next rebuild",
                "excluded": {"dirs": sorted(EXCLUDE_DIRS), "suffixes": sorted(EXCLUDE_SUFFIXES)},
                "fixtures": manifest,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nmanifest -> {out.relative_to(PROJECT_DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
