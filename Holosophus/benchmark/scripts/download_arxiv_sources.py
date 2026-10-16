#!/usr/bin/env python3
"""Download arXiv LaTeX source packages for benchmark gold papers.

Fetches source tar files from arXiv and extracts them into data/gold/arxiv/.
Uses the arXiv e-print API to download source files.

Usage:
    python download_arxiv_sources.py --ids 2405.15793 2407.16741 --output data/gold/arxiv/
"""

from __future__ import annotations

import argparse
import io
import os
import tarfile
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# Curated list of ML/AI papers with well-structured LaTeX sources
DEFAULT_ARXIV_IDS = [
    "2405.15793",  # SWE-agent
    "2407.16741",  # OpenHands
    "2310.06770",  # SWE-bench
    "2305.16291",  # Tree of Thoughts
    "2210.03629",  # ReAct
    "2305.10601",  # Gorilla: LLM Connected with APIs
    "2304.08354",  # Auto-GPT / HuggingGPT
    "2303.17580",  # HuggingGPT
    "2310.12931",  # Lemur: LLM Agents
    "2308.12950",  # AutoGen
]


def download_source(arxiv_id: str, output_dir: Path) -> bool:
    """Download and extract arXiv source for a given paper ID."""
    url = f"https://arxiv.org/e-print/{arxiv_id}"
    dest = output_dir / arxiv_id.replace("/", "_")

    if dest.exists() and any(dest.glob("*.tex")):
        print(f"  [skip] {arxiv_id} already exists at {dest}")
        return True

    dest.mkdir(parents=True, exist_ok=True)

    print(f"  [download] {arxiv_id} from {url}")
    try:
        req = Request(url, headers={"User-Agent": "WriterBenchmark/0.1"})
        resp = urlopen(req, timeout=30)
        data = resp.read()
    except HTTPError as e:
        print(f"  [error] {arxiv_id}: HTTP {e.code}")
        return False
    except Exception as e:
        print(f"  [error] {arxiv_id}: {e}")
        return False

    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
            tar.extractall(dest, filter="data")
        print(f"  [ok] {arxiv_id} -> {dest}")
        return True
    except tarfile.TarError:
        # Might be a single .tex file (not tarred)
        if data[:5] == b"\\docu" or b"\\begin{document}" in data[:2000]:
            (dest / "main.tex").write_bytes(data)
            print(f"  [ok] {arxiv_id} -> {dest} (single .tex)")
            return True
        else:
            print(f"  [error] {arxiv_id}: not a valid tar or tex file")
            return False


def main():
    parser = argparse.ArgumentParser(description="Download arXiv LaTeX sources")
    parser.add_argument(
        "--ids", nargs="*", default=DEFAULT_ARXIV_IDS,
        help="arXiv paper IDs to download",
    )
    parser.add_argument(
        "--output", "-o", default="data/gold/arxiv/",
        help="Output directory",
    )
    parser.add_argument(
        "--delay", type=float, default=3.0,
        help="Delay between requests (seconds) to respect rate limits",
    )
    args = parser.parse_args()

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    success = 0
    for arxiv_id in args.ids:
        if download_source(arxiv_id, output):
            success += 1
        time.sleep(args.delay)

    print(f"\nDownloaded {success}/{len(args.ids)} papers to {output}")


if __name__ == "__main__":
    main()
