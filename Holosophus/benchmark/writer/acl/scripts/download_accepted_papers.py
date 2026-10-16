#!/usr/bin/env python3
"""Download accepted ACL paper PDFs from ACL Anthology."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from writer.acl.accepted_papers import (
    OFFICIAL_ACCEPTED_URLS,
    TRACKS,
    fetch_acl_accepted_papers,
    normalize_text,
    parse_official_acl_2022,
    parse_official_acl_2024_2023,
    similarity,
    slugify_title,
)


USER_AGENT = "WriterBenchmark/0.1"
ACL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = ACL_ROOT / "download_papers"
TITLE_MATCH_THRESHOLD = 0.97
DEFAULT_REQUEST_TIMEOUT = 90
DEFAULT_REQUEST_RETRIES = 3
OFFICIAL_PAGE_TIMEOUT = 20
OFFICIAL_PAGE_RETRIES = 1
PDF_DOWNLOAD_TIMEOUT = 180
PDF_DOWNLOAD_RETRIES = 3
DOWNLOAD_CHUNK_SIZE = 1024 * 1024


def build_pdf_url(paper: dict[str, Any]) -> str | None:
    anthology_url = (paper.get("anthology_url") or "").strip()
    anthology_id = (paper.get("anthology_id") or "").strip()

    if anthology_url:
        return anthology_url.rstrip("/") + ".pdf"

    if anthology_id and ".official." not in anthology_id:
        return f"https://aclanthology.org/{anthology_id}.pdf"

    return None


def build_output_name(paper: dict[str, Any]) -> str:
    anthology_id = (paper.get("anthology_id") or "").strip()
    title = (paper.get("title") or "untitled").strip()
    stem = anthology_id or slugify_title(title)
    return f"{stem}__{slugify_title(title)}.pdf"


def build_default_output_dir(year: int) -> Path:
    return DEFAULT_OUTPUT_ROOT / str(year)


def fetch_text(url: str, timeout: int = DEFAULT_REQUEST_TIMEOUT, retries: int = DEFAULT_REQUEST_RETRIES) -> str:
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        request = Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                wait_sec = min(2**attempt, 10)
                print(f"[warn] fetch timed out/failed ({attempt}/{retries}) for {url}; retrying in {wait_sec}s", file=sys.stderr)
                time.sleep(wait_sec)

    raise RuntimeError(f"failed to fetch {url}: {last_error}") from last_error


def fetch_official_acl_accepted_papers_fast(year: int) -> list[Any]:
    url = OFFICIAL_ACCEPTED_URLS.get(year)
    if not url:
        raise ValueError(f"No official accepted-list URL configured for ACL {year}")

    html = fetch_text(url, timeout=OFFICIAL_PAGE_TIMEOUT, retries=OFFICIAL_PAGE_RETRIES)
    if year in {2023, 2024}:
        return parse_official_acl_2024_2023(html, year)
    if year == 2022:
        return parse_official_acl_2022(html, year)

    raise ValueError(f"No parser implemented for official ACL {year} page")


def extract_bib_field(entry: str, field: str) -> str | None:
    match = re.search(rf"\b{re.escape(field)}\s*=\s*([{{\"])", entry)
    if not match:
        return None

    opener = match.group(1)
    closer = "}" if opener == "{" else '"'
    index = match.end()
    depth = 1 if opener == "{" else 0
    chars = []

    while index < len(entry):
        char = entry[index]
        if opener == "{":
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    break
        elif char == closer:
            break

        chars.append(char)
        index += 1

    value = "".join(chars)
    value = re.sub(r"\s+", " ", value)
    return value.replace("{", "").replace("}", "").strip()


def iter_bib_entries(bib_text: str) -> list[str]:
    return [
        entry.strip()
        for entry in re.split(r"(?=^@)", bib_text, flags=re.MULTILINE)
        if entry.strip().startswith("@inproceedings")
    ]


def fetch_anthology_bib_index(year: int, tracks: set[str]) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}

    for track in sorted(tracks):
        volume_id = f"{year}.acl-{track}"
        bib_url = f"https://aclanthology.org/volumes/{volume_id}.bib"
        bib_text = fetch_text(bib_url, timeout=30)

        for entry in iter_bib_entries(bib_text):
            title = extract_bib_field(entry, "title")
            url = extract_bib_field(entry, "url")
            if not title or not url:
                continue

            anthology_id = url.rstrip("/").rsplit("/", 1)[-1]
            if anthology_id.endswith(".0"):
                continue

            index[(normalize_text(title), track)] = {
                "title": title,
                "track": track,
                "anthology_id": anthology_id,
                "anthology_url": url,
                "volume_id": volume_id,
                "title_match_score": 1.0,
            }

    return index


def papers_from_anthology_bib_index(year: int, tracks: set[str]) -> list[dict[str, Any]]:
    papers = []
    index = fetch_anthology_bib_index(year, tracks)

    for (_norm_title, track), paper in index.items():
        papers.append(
            {
                "year": year,
                "venue": "acl",
                "title": paper["title"],
                "authors": [],
                "track": track,
                "accepted_source": "anthology_bib_volume",
                "anthology_id": paper["anthology_id"],
                "anthology_url": paper["anthology_url"],
                "volume_id": paper["volume_id"],
                "title_match_score": 1.0,
            }
        )

    return papers


def fast_fetch_official_papers(year: int, tracks: set[str] | None) -> list[dict[str, Any]]:
    indexed_tracks = tracks or set(TRACKS)

    try:
        official_papers = fetch_official_acl_accepted_papers_fast(year)
    except Exception as exc:
        print(
            f"[warn] official ACL page failed ({exc}); using ACL Anthology volume BibTeX instead",
            file=sys.stderr,
        )
        return papers_from_anthology_bib_index(year, indexed_tracks)

    if tracks:
        official_papers = [paper for paper in official_papers if paper.track in tracks]

    enriched = []
    track_counts: dict[str, int] = {}

    for paper in official_papers:
        record = asdict(paper)
        track_counts[paper.track] = track_counts.get(paper.track, 0) + 1
        volume_id = f"{year}.acl-{paper.track}"
        anthology_id = f"{volume_id}.{track_counts[paper.track]}"
        record.update(
            {
                "anthology_id": anthology_id,
                "anthology_url": f"https://aclanthology.org/{anthology_id}/",
                "volume_id": volume_id,
                "title_match_score": None,
            }
        )

        enriched.append(record)

    return enriched


def download_file(url: str, destination: Path, overwrite: bool) -> str:
    if destination.exists() and not overwrite:
        return "already_present"

    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_suffix(destination.suffix + ".part")
    last_error: Exception | None = None

    for attempt in range(1, PDF_DOWNLOAD_RETRIES + 1):
        request = Request(url, headers={"User-Agent": USER_AGENT})

        try:
            with urlopen(request, timeout=PDF_DOWNLOAD_TIMEOUT) as response:
                with temp_path.open("wb") as output:
                    while True:
                        chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                        if not chunk:
                            break
                        output.write(chunk)
            break
        except Exception as exc:
            last_error = exc
            if temp_path.exists():
                temp_path.unlink()

            if attempt < PDF_DOWNLOAD_RETRIES:
                wait_sec = min(2**attempt, 10)
                print(
                    f"[warn] PDF download failed ({attempt}/{PDF_DOWNLOAD_RETRIES}) for {url}; retrying in {wait_sec}s",
                    file=sys.stderr,
                )
                time.sleep(wait_sec)
                continue

            raise RuntimeError(str(last_error)) from last_error

    temp_path.replace(destination)
    return "downloaded"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download accepted ACL paper PDFs from ACL Anthology."
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
        help="How many accepted papers to download; default is all matched papers",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Where PDFs and metadata should be saved; default is acl/download_papers/<year>",
    )
    parser.add_argument(
        "--metadata-file",
        type=Path,
        help="Optional explicit metadata JSON path",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Re-download PDFs even if the target file already exists",
    )
    parser.add_argument(
        "--download-delay",
        type=float,
        default=0.0,
        help="Sleep between PDF downloads in seconds; only used with --workers 1",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Parallel PDF download workers",
    )
    return parser.parse_args()


def download_one_paper(paper: dict[str, Any], output_dir: Path, overwrite: bool) -> dict[str, Any]:
    title = paper.get("title", "")
    anthology_id = paper.get("anthology_id", "")
    pdf_url = build_pdf_url(paper)
    output_path = output_dir / build_output_name(paper)

    record = {
        "year": paper.get("year"),
        "track": paper.get("track"),
        "title": title,
        "authors": paper.get("authors", []),
        "accepted_source": paper.get("accepted_source"),
        "anthology_id": anthology_id,
        "anthology_url": paper.get("anthology_url"),
        "pdf_url": pdf_url,
        "pdf_path": str(output_path),
    }

    if not pdf_url:
        record["status"] = "missing_pdf_url"
        return record

    try:
        record["status"] = download_file(pdf_url, output_path, overwrite=overwrite)
    except Exception as exc:
        record["status"] = "download_failed"
        record["error"] = str(exc)

    return record


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir or build_default_output_dir(args.year)
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = args.metadata_file or output_dir / "metadata.json"
    selected_tracks = set(args.track) if args.track else None

    try:
        if args.source == "official":
            papers = fast_fetch_official_papers(year=args.year, tracks=selected_tracks)
        else:
            papers = fetch_acl_accepted_papers(year=args.year, source=args.source)
    except ImportError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("Install missing dependencies, for example: pip install acl-anthology beautifulsoup4", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: failed to fetch ACL accepted papers: {exc}", file=sys.stderr)
        return 1

    if selected_tracks:
        papers = [paper for paper in papers if paper.get("track") in selected_tracks]

    if args.paper_count is not None:
        papers = papers[: args.paper_count]

    if not papers:
        print("No accepted papers matched the selected filters.", file=sys.stderr)
        return 1

    records: list[dict[str, Any]] = []
    downloaded = 0
    worker_count = max(1, args.workers)

    if worker_count == 1:
        for index, paper in enumerate(papers, start=1):
            record = download_one_paper(paper, output_dir, overwrite=args.overwrite)
            records.append(record)
            if record["status"] == "downloaded":
                downloaded += 1
            print(f"[{index}/{len(papers)}] {record['status']} :: {record['anthology_id']} :: {record['title']}")
            if args.download_delay > 0 and index < len(papers):
                time.sleep(args.download_delay)
    else:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_index = {
                executor.submit(download_one_paper, paper, output_dir, args.overwrite): index
                for index, paper in enumerate(papers, start=1)
            }
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                record = future.result()
                records.append(record)
                if record["status"] == "downloaded":
                    downloaded += 1
                print(f"[{index}/{len(papers)}] {record['status']} :: {record['anthology_id']} :: {record['title']}")

    metadata_path.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nSaved metadata to {metadata_path}")
    print(f"Downloaded {downloaded} PDFs out of {len(papers)} selected papers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
