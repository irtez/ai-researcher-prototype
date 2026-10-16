#!/usr/bin/env python3
"""Collect accepted ACL papers and download associated source artifacts."""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import tarfile
import time
import urllib.parse
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from writer.acl.accepted_papers import fetch_acl_papers


USER_AGENT = "WriterBenchmark/0.1"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
DEFAULT_REQUEST_DELAY_SEC = 3.0
DEFAULT_ARXIV_MAX_RESULTS = 10
DEFAULT_ARXIV_MATCH_THRESHOLD = 0.90
SCRIPT_ROOT = Path(__file__).resolve().parent
ACL_ROOT = SCRIPT_ROOT.parent
DEFAULT_OUTPUT_ROOT = ACL_ROOT / "downloaded_artifacts"


def normalize_title(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def slugify_title(text: str) -> str:
    slug = normalize_title(text).replace(" ", "-")
    return slug[:80] or "untitled"


def build_default_output_dir(year: int) -> Path:
    return DEFAULT_OUTPUT_ROOT / str(year)


def search_arxiv_by_title(
    title: str,
    *,
    max_results: int,
    request_delay_sec: float,
) -> list[dict[str, Any]]:
    query = urllib.parse.quote(f'ti:"{title}"')
    url = f"http://export.arxiv.org/api/query?search_query={query}&start=0&max_results={max_results}"
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=30) as response:
        root = ET.fromstring(response.read())

    entries = []
    for entry in root.findall("atom:entry", ATOM_NS):
        arxiv_url = entry.findtext("atom:id", default="", namespaces=ATOM_NS)
        arxiv_id = arxiv_url.rsplit("/", 1)[-1]
        arxiv_title = " ".join(
            (entry.findtext("atom:title", default="", namespaces=ATOM_NS) or "").split()
        )
        entries.append({"arxiv_id": arxiv_id, "title": arxiv_title, "url": arxiv_url})

    if request_delay_sec > 0:
        time.sleep(request_delay_sec)
    return entries


def best_arxiv_match(
    title: str,
    *,
    threshold: float,
    max_results: int,
    request_delay_sec: float,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    normalized_target = normalize_title(title)
    candidates = []
    for candidate in search_arxiv_by_title(
        title,
        max_results=max_results,
        request_delay_sec=request_delay_sec,
    ):
        score = SequenceMatcher(
            None,
            normalized_target,
            normalize_title(candidate["title"]),
        ).ratio()
        enriched = dict(candidate)
        enriched["score"] = score
        candidates.append(enriched)

    if not candidates:
        return None, []

    candidates.sort(key=lambda item: item["score"], reverse=True)
    if candidates[0]["score"] < threshold:
        return None, candidates
    return candidates[0], candidates


def download_arxiv_source(
    arxiv_id: str,
    destination: Path,
    *,
    overwrite: bool,
) -> tuple[bool, str]:
    destination.mkdir(parents=True, exist_ok=True)
    tex_files = list(destination.rglob("*.tex"))
    if tex_files and not overwrite:
        return True, "already_present"

    archive_path = destination / "arxiv_source.tar"
    if archive_path.exists() and overwrite:
        archive_path.unlink()

    url = f"https://arxiv.org/e-print/{arxiv_id}"
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=60) as response:
            data = response.read()
    except HTTPError as exc:
        return False, f"http_{exc.code}"
    except URLError as exc:
        return False, f"url_error: {exc.reason}"
    except Exception as exc:
        return False, f"error: {exc}"

    archive_path.write_bytes(data)

    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
            tar.extractall(destination)
    except tarfile.TarError:
        if data[:5] == b"\\docu" or b"\\begin{document}" in data[:2000]:
            (destination / "main.tex").write_bytes(data)
        else:
            return False, "not_tar_or_tex"

    tex_files = list(destination.rglob("*.tex"))
    return bool(tex_files), f"tex_files={len(tex_files)}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect accepted ACL papers and download source artifacts.",
    )
    parser.add_argument("--year", type=int, required=True, help="ACL year, for example 2024")
    parser.add_argument(
        "--source",
        choices=("auto", "official", "anthology"),
        default="auto",
        help="How to build the accepted-paper list",
    )
    parser.add_argument(
        "--track",
        action="append",
        choices=("long", "short"),
        help="Track filter; pass multiple times to keep more than one track",
    )
    parser.add_argument(
        "--count",
        "--max-papers",
        dest="paper_count",
        type=int,
        default=None,
        help="How many accepted papers to process; default is all matched papers",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Where per-paper folders and metadata should be saved; default is acl/downloaded_artifacts/<year>",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-download or overwrite existing source artifacts",
    )
    parser.add_argument(
        "--arxiv-max-results",
        type=int,
        default=DEFAULT_ARXIV_MAX_RESULTS,
        help="How many arXiv title search results to inspect per paper",
    )
    parser.add_argument(
        "--arxiv-match-threshold",
        type=float,
        default=DEFAULT_ARXIV_MATCH_THRESHOLD,
        help="Minimum title similarity score for an arXiv match",
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=DEFAULT_REQUEST_DELAY_SEC,
        help="Delay between arXiv API requests in seconds",
    )
    parser.add_argument(
        "--metadata-file",
        type=Path,
        help="Optional explicit JSON path for the run manifest",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir or build_default_output_dir(args.year)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = args.metadata_file or output_dir / "manifest.json"

    papers = fetch_acl_papers(
        year=args.year,
        source=args.source,
        max_papers=args.paper_count,
    )

    if args.track:
        selected_tracks = set(args.track)
        papers = [paper for paper in papers if paper.get("track") in selected_tracks]

    if args.paper_count is not None:
        papers = papers[: args.paper_count]

    if not papers:
        print("No accepted papers matched the selected filters.", file=sys.stderr)
        return 1

    records = []

    for index, paper in enumerate(papers, start=1):
        anthology_id = paper["anthology_id"]
        paper_dir = output_dir / anthology_id.replace("/", "_")
        paper_dir.mkdir(parents=True, exist_ok=True)

        record = dict(paper)
        record["paper_dir"] = str(paper_dir)

        print(f"[{index}/{len(papers)}] {anthology_id} :: {paper['title']}")

        (paper_dir / "paper_metadata.json").write_text(
            json.dumps(record, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        try:
            match, candidates = best_arxiv_match(
                paper["title"],
                threshold=args.arxiv_match_threshold,
                max_results=args.arxiv_max_results,
                request_delay_sec=args.request_delay,
            )
        except Exception as exc:
            record["status"] = "match_error"
            record["error"] = str(exc)
            records.append(record)
            print(f"  [error] matching failed: {exc}")
            continue

        if match is None:
            record.update(
                {
                    "status": "no_confident_arxiv_match",
                    "candidate_count": len(candidates),
                    "best_candidate": candidates[0]["title"] if candidates else None,
                    "best_score": candidates[0]["score"] if candidates else 0.0,
                }
            )
            (paper_dir / "arxiv_candidates.json").write_text(
                json.dumps(candidates, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            records.append(record)
            print("  [skip] no confident arXiv match")
            if candidates:
                print(
                    f"         best candidate: {candidates[0]['arxiv_id']} "
                    f"score={candidates[0]['score']:.3f}"
                )
            continue

        (paper_dir / "arxiv_candidates.json").write_text(
            json.dumps(candidates, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (paper_dir / "arxiv_match.json").write_text(
            json.dumps(match, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        ok, download_note = download_arxiv_source(
            match["arxiv_id"],
            paper_dir,
            overwrite=args.overwrite,
        )
        record.update(
            {
                "status": "ready" if ok else "download_failed",
                "arxiv_id": match["arxiv_id"],
                "arxiv_title": match["title"],
                "arxiv_url": match["url"],
                "match_score": match["score"],
                "download_note": download_note,
            }
        )
        records.append(record)
        print(f"  [match] {match['arxiv_id']} score={match['score']:.3f}")
        print(f"  [{'ok' if ok else 'fail'}] {download_note}")

    manifest_path.write_text(
        json.dumps(records, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"\nSaved manifest to {manifest_path}")
    ready_count = sum(record["status"] == "ready" for record in records)
    print(f"Ready source packages: {ready_count}/{len(records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
