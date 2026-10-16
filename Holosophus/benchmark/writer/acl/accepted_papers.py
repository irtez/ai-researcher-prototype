"""Fetch real accepted ACL papers from official lists with Anthology fallback."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Optional
from urllib.request import Request, urlopen

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

try:
    from acl_anthology import Anthology
except ImportError:
    Anthology = None


USER_AGENT = "WriterBenchmark/0.1"
REQUEST_DELAY_SEC = 1.0
TRACKS = ("long", "short")

OFFICIAL_ACCEPTED_URLS = {
    2024: "https://2024.aclweb.org/program/main_conference_papers/",
    2023: "https://2023.aclweb.org/program/accepted_main_conference/",
    2022: "https://2022.aclweb.org/papers.html",
}


@dataclass
class AcceptedPaper:
    year: int
    venue: str
    title: str
    authors: list[str]
    track: str
    accepted_source: str
    anthology_id: Optional[str] = None
    anthology_url: Optional[str] = None
    volume_id: Optional[str] = None
    title_match_score: Optional[float] = None


def normalize_text(text: str) -> str:
    text = text.lower().strip()
    text = text.replace("’", "'").replace("`", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()


def slugify_title(text: str) -> str:
    slug = normalize_text(text).replace(" ", "-")
    return slug[:80] or "untitled"


def fetch_html(url: str) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def split_authors(author_line: str) -> list[str]:
    parts = re.split(r"\s*,\s*|\s+and\s+", author_line.strip())
    return [p.strip() for p in parts if p.strip()]


def parse_official_acl_2024_2023(html: str, year: int) -> list[AcceptedPaper]:
    if BeautifulSoup is None:
        raise ImportError("beautifulsoup4 is required for official accepted-list parsing")

    soup = BeautifulSoup(html, "html.parser")
    papers = []

    for track_label, track in (("Long Papers", "long"), ("Short Papers", "short")):
        header = soup.find("h2", string=lambda s: s and s.strip() == track_label)
        if header is None:
            continue

        paper_list = header.find_next_sibling("ul")
        if paper_list is None:
            continue

        for item in paper_list.find_all("li", recursive=False):
            title_tag = item.find("strong")
            authors_tag = item.find("em")

            title = title_tag.get_text(" ", strip=True) if title_tag else ""
            authors_text = authors_tag.get_text(" ", strip=True) if authors_tag else ""
            authors = split_authors(authors_text) if authors_text else []

            if not title:
                text = item.get_text(" ", strip=True)
                if not text:
                    continue
                title = text

            papers.append(
                AcceptedPaper(
                    year=year,
                    venue="acl",
                    title=title,
                    authors=authors,
                    track=track,
                    accepted_source="official",
                )
            )

    uniq = {}
    for p in papers:
        uniq[(normalize_text(p.title), p.track)] = p
    return list(uniq.values())


def parse_official_acl_2022(html: str, year: int) -> list[AcceptedPaper]:
    if BeautifulSoup is None:
        raise ImportError("beautifulsoup4 is required for official accepted-list parsing")

    soup = BeautifulSoup(html, "html.parser")
    lines = [x.strip() for x in soup.get_text("\n").splitlines()]
    lines = [x for x in lines if x]

    papers = []
    in_main_conf = False
    current_track = None

    for i, line in enumerate(lines):
        if line == "Main Conference":
            in_main_conf = True
            current_track = None
            continue

        if line == "FINDINGS OF ACL 2022":
            break

        if not in_main_conf:
            continue

        if line == "Long Papers":
            current_track = "long"
            continue

        if line == "Short Papers":
            current_track = "short"
            continue

        if line == "System Demonstration Papers":
            current_track = None
            continue

        if current_track in {"long", "short"} and i + 1 < len(lines):
            nxt = lines[i + 1]
            if (
                line not in {"Back to Top", "Long Papers", "Short Papers"}
                and nxt not in {"Back to Top", "Long Papers", "Short Papers"}
                and len(line) > 10
                and ("," in nxt or " and " in nxt)
            ):
                papers.append(
                    AcceptedPaper(
                        year=year,
                        venue="acl",
                        title=line,
                        authors=split_authors(nxt),
                        track=current_track,
                        accepted_source="official",
                    )
                )

    uniq = {}
    for p in papers:
        uniq[(normalize_text(p.title), p.track)] = p
    return list(uniq.values())


def fetch_official_acl_accepted_papers(year: int) -> list[AcceptedPaper]:
    url = OFFICIAL_ACCEPTED_URLS.get(year)
    if not url:
        raise ValueError(f"No official accepted-list URL configured for ACL {year}")

    html = fetch_html(url)

    if year in {2023, 2024}:
        papers = parse_official_acl_2024_2023(html, year)
    elif year == 2022:
        papers = parse_official_acl_2022(html, year)
    else:
        raise ValueError(f"No parser implemented for official ACL {year} page")

    time.sleep(REQUEST_DELAY_SEC)
    return papers


def iter_volume_papers(anthology, volume_id: str):
    volume = anthology.get(volume_id)
    if volume is None:
        return []
    if hasattr(volume, "papers"):
        return list(volume.papers())
    if hasattr(volume, "items"):
        return [item for item in volume.items() if hasattr(item, "title")]
    return []


def is_non_paper_entry(paper) -> bool:
    title = str(getattr(paper, "title", "") or "").strip()
    paper_id = str(getattr(paper, "id", "") or "").strip()

    if not title:
        return True
    if paper_id == "0":
        return True
    if title.startswith("Proceedings of "):
        return True

    lowered = title.lower()
    bad_prefixes = (
        "preface",
        "message from",
        "organizing committee",
        "program committee",
        "table of contents",
    )
    return any(lowered.startswith(x) for x in bad_prefixes)


def fetch_anthology_acl_main_papers(year: int) -> list[AcceptedPaper]:
    if Anthology is None:
        raise ImportError("acl_anthology is not installed")

    anthology = Anthology.from_repo()
    results = []

    for track in TRACKS:
        volume_id = f"{year}.acl-{track}"

        for paper in iter_volume_papers(anthology, volume_id):
            if is_non_paper_entry(paper):
                continue

            title = str(getattr(paper, "title", "") or "").strip()
            paper_id = str(getattr(paper, "id", "") or "").strip()
            full_id = getattr(paper, "full_id", None) or f"{volume_id}.{paper_id}"

            authors = []
            for author in getattr(paper, "authors", []) or []:
                name = getattr(author, "name", author)
                a = str(name).strip()
                if a:
                    authors.append(a)

            results.append(
                AcceptedPaper(
                    year=year,
                    venue="acl",
                    title=title,
                    authors=authors,
                    track=track,
                    accepted_source="anthology_fallback",
                    anthology_id=full_id,
                    anthology_url=f"https://aclanthology.org/{full_id}/",
                    volume_id=volume_id,
                    title_match_score=1.0,
                )
            )

    uniq = {}
    for p in results:
        uniq[(normalize_text(p.title), p.track)] = p
    return list(uniq.values())


def build_anthology_index(year: int) -> dict[tuple[str, str], dict]:
    papers = fetch_anthology_acl_main_papers(year)
    return {(normalize_text(p.title), p.track): asdict(p) for p in papers}


def enrich_official_with_anthology(year: int, official_papers: list[AcceptedPaper]) -> list[dict]:
    index = build_anthology_index(year)
    enriched = []

    for p in official_papers:
        record = asdict(p)

        exact = index.get((normalize_text(p.title), p.track))
        if exact:
            record["anthology_id"] = exact["anthology_id"]
            record["anthology_url"] = exact["anthology_url"]
            record["volume_id"] = exact["volume_id"]
            record["title_match_score"] = 1.0
            enriched.append(record)
            continue

        best = None
        best_score = -1.0
        for (norm_title, track), cand in index.items():
            if track != p.track:
                continue
            score = similarity(p.title, cand["title"])
            if score > best_score:
                best_score = score
                best = cand

        if best and best_score >= 0.97:
            record["anthology_id"] = best["anthology_id"]
            record["anthology_url"] = best["anthology_url"]
            record["volume_id"] = best["volume_id"]
            record["title_match_score"] = best_score
        else:
            record["volume_id"] = f"{year}.acl-{p.track}"
            record["anthology_id"] = f"{year}.acl-{p.track}.official.{slugify_title(p.title)}"
            record["anthology_url"] = None
            record["title_match_score"] = best_score if best_score >= 0 else None

        enriched.append(record)

    return enriched


def fetch_acl_accepted_papers(year: int, source: str = "auto") -> list[dict]:
    source = source.lower().strip()

    if source == "official":
        official = fetch_official_acl_accepted_papers(year)
        if not official:
            raise ValueError(f"Official accepted-list parser returned 0 papers for ACL {year}")
        return enrich_official_with_anthology(year, official)

    if source == "anthology":
        return [asdict(p) for p in fetch_anthology_acl_main_papers(year)]

    if source == "auto":
        if year in OFFICIAL_ACCEPTED_URLS:
            try:
                official = fetch_official_acl_accepted_papers(year)
                if official:
                    return enrich_official_with_anthology(year, official)
                print(f"[warn] official accepted-list parser returned 0 papers for ACL {year}; falling back to anthology")
            except Exception:
                return [asdict(p) for p in fetch_anthology_acl_main_papers(year)]
            return [asdict(p) for p in fetch_anthology_acl_main_papers(year)]
        return [asdict(p) for p in fetch_anthology_acl_main_papers(year)]

    raise ValueError("source must be one of: official, anthology, auto")


def fetch_acl_papers(year: int, source: str = "auto", max_papers: int | None = None, min_papers: int | None = None) -> list[dict]:
    papers = fetch_acl_accepted_papers(year=year, source=source)
    target_count = max_papers if max_papers else len(papers)
    if min_papers:
        target_count = max(target_count, min_papers)
    return papers[:target_count]
