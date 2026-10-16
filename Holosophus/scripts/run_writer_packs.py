#!/usr/bin/env python3
"""Run the Writer benchmark on multiple distilled packs.

This is a thin batch wrapper around `scripts/run_writer_pack.py`. It intentionally
does not reimplement single-pack execution: each pack is delegated to the
single-pack runner so copying, Writer invocation, event capture, metadata, and
optional evaluation stay consistent in one place.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = REPO_ROOT / "benchmark"
sys.path.insert(0, str(BENCHMARK_ROOT))

from metadata_registry.naming import parse_variant_id, slugify  # type: ignore[import-not-found]


DEFAULT_DISTILLED_ROOT = BENCHMARK_ROOT / "data" / "benchmark_distilled"
DEFAULT_LEVELS = ("oracle", "realistic", "incomplete")


def _is_pack_dir(path: Path) -> bool:
    """Return whether a directory looks like one distilled Writer pack."""
    return path.is_dir() and (path / "writer_handoff.md").exists()


def _iter_pack_dirs(distilled_root: Path, levels: set[str]) -> list[Path]:
    """Discover canonical pack directories under a benchmark_distilled root."""
    pack_dirs: list[Path] = []
    if not distilled_root.exists():
        raise SystemExit(f"Distilled root does not exist: {distilled_root}")

    for paper_dir in sorted(path for path in distilled_root.iterdir() if path.is_dir()):
        for pack_dir in sorted(path for path in paper_dir.iterdir() if path.is_dir()):
            try:
                parsed = parse_variant_id(pack_dir.name)
            except ValueError:
                continue
            if parsed["distillation_level"] not in levels:
                continue
            if _is_pack_dir(pack_dir):
                pack_dirs.append(pack_dir)

    return pack_dirs


def _resolve_pack_dirs(explicit_pack_dirs: list[str], distilled_root: Path, levels: set[str]) -> list[Path]:
    """Use explicit pack paths when provided, otherwise scan the distilled root."""
    if explicit_pack_dirs:
        pack_dirs = [Path(path).resolve() for path in explicit_pack_dirs]
        missing = [path for path in pack_dirs if not _is_pack_dir(path)]
        if missing:
            formatted = "\n".join(str(path) for path in missing)
            raise SystemExit(f"Not valid distilled pack directories:\n{formatted}")
        return pack_dirs

    return _iter_pack_dirs(distilled_root.resolve(), levels)


def _limit_by_papers(pack_dirs: list[Path], paper_limit: int) -> list[Path]:
    """Keep all resolved packs for the first N paper directories."""
    selected_papers: set[Path] = set()
    selected_pack_dirs: list[Path] = []
    for pack_dir in pack_dirs:
        paper_dir = pack_dir.parent
        if paper_dir not in selected_papers:
            if len(selected_papers) >= paper_limit:
                break
            selected_papers.add(paper_dir)
        selected_pack_dirs.append(pack_dir)
    return selected_pack_dirs


def _format_duration(seconds: float | None) -> str:
    """Format elapsed/remaining seconds for human-readable progress output."""
    if seconds is None:
        return "unknown"
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _count_papers(pack_dirs: list[Path]) -> int:
    """Count distinct paper directories represented by a pack list."""
    return len({pack_dir.parent for pack_dir in pack_dirs})


def _progress_line(
    *,
    processed: int,
    total: int,
    successes: int,
    failures: int,
    skipped: int,
    touched_papers: int,
    total_papers: int,
    started_at: float,
) -> str:
    """Build a one-line batch progress summary with ETA."""
    elapsed = time.monotonic() - started_at
    eta = None
    if processed:
        eta = (elapsed / processed) * (total - processed)
    percent = (processed / total * 100) if total else 100.0
    return (
        f"Progress: {processed}/{total} packs ({percent:.1f}%) | "
        f"papers touched: {touched_papers}/{total_papers} | "
        f"ok: {successes}, failed: {failures}, skipped: {skipped} | "
        f"elapsed: {_format_duration(elapsed)} | ETA: {_format_duration(eta)}"
    )


def _infer_session_id(pack_dir: Path) -> str:
    """Build the same deterministic session id as the single-pack runner."""
    try:
        parsed = parse_variant_id(pack_dir.name)
        return f"writerbench__{parsed['paper_uid']}__{parsed['distillation_level']}"
    except ValueError:
        level = slugify(pack_dir.name, "level")
        paper_id = slugify(pack_dir.parent.name, "paper")
        return f"writerbench__{paper_id}__{level}"


def _existing_workdir(args: argparse.Namespace, pack_dir: Path) -> Path:
    """Return the workdir that would be used for one pack."""
    return Path(args.workdir_root).resolve() / _infer_session_id(pack_dir)


def _build_single_pack_command(args: argparse.Namespace, pack_dir: Path) -> list[str]:
    """Build a `run_writer_pack.py` command for one pack."""
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "run_writer_pack.py"),
        str(pack_dir),
        "--app-url",
        args.app_url,
        "--workdir-root",
        str(args.workdir_root),
        "--metadata-root",
        str(args.metadata_root),
        "--timeout-seconds",
        str(args.timeout_seconds),
        "--evaluator",
        args.evaluator,
    ]
    if args.overwrite:
        command.append("--overwrite")
    if args.no_print_output:
        command.append("--no-print-output")
    return command


def _run_pack(command: list[str], keep_going: bool) -> bool:
    """Run one single-pack command and return whether it succeeded."""
    try:
        subprocess.run(command, cwd=REPO_ROOT, check=True)
        return True
    except subprocess.CalledProcessError as exc:
        print(f"Pack failed with exit code {exc.returncode}: {' '.join(command)}", file=sys.stderr)
        if not keep_going:
            raise
        return False


def main() -> None:
    """CLI entrypoint for sequential multi-pack Writer benchmark runs."""
    parser = argparse.ArgumentParser(
        description="Run Holosophos writer on multiple distilled benchmark packs"
    )
    parser.add_argument(
        "pack_dirs",
        nargs="*",
        help="Optional explicit distilled pack directories. If omitted, scan --distilled-root.",
    )
    parser.add_argument(
        "--distilled-root",
        type=Path,
        default=DEFAULT_DISTILLED_ROOT,
        help="Root scanned when explicit pack dirs are not provided",
    )
    parser.add_argument(
        "--levels",
        nargs="+",
        choices=DEFAULT_LEVELS,
        default=list(DEFAULT_LEVELS),
        help="Distillation levels to run when scanning --distilled-root",
    )
    parser.add_argument(
        "--app-url",
        default="http://127.0.0.1:5055",
        help="Holosophos app base URL",
    )
    parser.add_argument(
        "--workdir-root",
        type=Path,
        default=REPO_ROOT / "workdir",
        help="Root directory where session workdirs are created",
    )
    parser.add_argument(
        "--metadata-root",
        type=Path,
        default=BENCHMARK_ROOT / "data" / "metadata",
        help="Benchmark metadata registry root",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=3600,
        help="HTTP timeout passed to each single-pack writer run",
    )
    parser.add_argument(
        "--evaluator",
        choices=["none", "acl", "base"],
        default="none",
        help="Optional evaluator passed to each single-pack run",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Pass --overwrite to each single-pack run",
    )
    parser.add_argument(
        "--no-print-output",
        action="store_true",
        help="Suppress streamed Writer output for each single-pack run",
    )
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Continue with later packs if one pack fails",
    )
    limit_group = parser.add_mutually_exclusive_group()
    limit_group.add_argument(
        "--limit",
        type=int,
        help="Optionally run only the first N resolved packs",
    )
    limit_group.add_argument(
        "--limit-papers",
        type=int,
        help="Optionally run all resolved levels for the first N paper directories",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved packs and commands without running Writer",
    )
    args = parser.parse_args()

    pack_dirs = _resolve_pack_dirs(
        args.pack_dirs,
        args.distilled_root,
        levels=set(args.levels),
    )
    if args.limit is not None:
        pack_dirs = pack_dirs[: args.limit]
    if args.limit_papers is not None:
        pack_dirs = _limit_by_papers(pack_dirs, args.limit_papers)

    if not pack_dirs:
        raise SystemExit("No distilled pack directories resolved.")

    total_papers = _count_papers(pack_dirs)
    print(f"Resolved packs: {len(pack_dirs)} across {total_papers} papers")
    if args.dry_run:
        for index, pack_dir in enumerate(pack_dirs, start=1):
            command = _build_single_pack_command(args, pack_dir)
            workdir = _existing_workdir(args, pack_dir)
            print(f"\n[{index}/{len(pack_dirs)}] {pack_dir}")
            if workdir.exists() and not args.overwrite:
                print(f"Would skip existing workdir: {workdir}")
            print(" ".join(command))
        return

    successes = 0
    failures = 0
    skipped = 0
    started_at = time.monotonic()
    touched_papers: set[Path] = set()
    for index, pack_dir in enumerate(pack_dirs, start=1):
        touched_papers.add(pack_dir.parent)
        workdir = _existing_workdir(args, pack_dir)
        if workdir.exists() and not args.overwrite:
            skipped += 1
            print(f"\n[{index}/{len(pack_dirs)}] Skipping existing workdir: {workdir}")
            print(
                _progress_line(
                    processed=index,
                    total=len(pack_dirs),
                    successes=successes,
                    failures=failures,
                    skipped=skipped,
                    touched_papers=len(touched_papers),
                    total_papers=total_papers,
                    started_at=started_at,
                )
            )
            continue

        print(f"\n[{index}/{len(pack_dirs)}] Running {pack_dir}")
        print(f"Paper progress: {len(touched_papers)}/{total_papers}")
        command = _build_single_pack_command(args, pack_dir)
        if _run_pack(command, keep_going=args.keep_going):
            successes += 1
        else:
            failures += 1
        print(
            _progress_line(
                processed=index,
                total=len(pack_dirs),
                successes=successes,
                failures=failures,
                skipped=skipped,
                touched_papers=len(touched_papers),
                total_papers=total_papers,
                started_at=started_at,
            )
        )

    print(f"\nCompleted batch. Successes: {successes}. Failures: {failures}. Skipped: {skipped}.")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
