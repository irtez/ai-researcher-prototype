"""Related-work enrichment for the grouped-rubric judge."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any, Optional

from openai import AsyncOpenAI
from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)

KeywordExtractor = Callable[[str], Awaitable[list[str]]]
PaperSearcher = Callable[[list[str], int], Awaitable[list[dict[str, Any]]]]


class KeywordExtractionResult(BaseModel):
    keywords: list[str] = Field(
        default_factory=list,
        description=(
            "Five to ten specific technical search terms, method names, datasets, "
            "benchmarks, or algorithm names for finding closely related papers."
        ),
    )
    query_hints: list[str] = Field(
        default_factory=list,
        description="Optional alternative short query phrases for academic search.",
    )


KEYWORD_EXTRACTION_PROMPT = """Extract search terms for finding closely related scientific papers.

Return JSON only.

Rules:
- Prefer highly specific technical terms, method names, dataset names, benchmark names, and task names.
- Avoid generic terms such as "machine learning", "deep learning", "evaluation", "paper", "method".
- Keep terms short enough to work in arXiv search.
- Include 5-10 keywords.
- Do not review the paper.
"""


def extract_keywords_regex(paper_text: str, max_keywords: int = 10) -> list[str]:
    abstract_match = re.search(r"(?i)abstract\s*\n(.*?)(?=\n\s*\d|##|\Z)", paper_text, re.DOTALL)
    methods_match = re.search(
        r"(?i)(method|approach|model|architecture).*?\n(.*?)(?=\n\s*\d|##|\Z)",
        paper_text,
        re.DOTALL,
    )

    key_text = ""
    if abstract_match:
        key_text += abstract_match.group(1)[:1000]
    if methods_match:
        key_text += "\n" + methods_match.group(2)[:1000]
    if not key_text:
        key_text = paper_text[:3000]

    patterns = [
        r"\b[A-Z][a-z]+(?:[ \t]+[A-Z][a-z]+)+\b",
        r"\b[A-Z]{2,}\b",
        r"\b[a-z]{4,}(?:tion|ness|ity|ance|ence)\b",
    ]
    terms: list[str] = []
    for pattern in patterns:
        terms.extend(re.findall(pattern, key_text))

    stop_words = {
        "this",
        "that",
        "with",
        "from",
        "using",
        "based",
        "show",
        "showing",
        "propose",
        "present",
        "introduce",
        "develop",
        "design",
        "learn",
        "train",
        "test",
        "evaluate",
        "method",
        "approach",
        "model",
        "paper",
        "work",
        "study",
        "result",
        "experiment",
    }
    filtered = []
    seen = set()
    for term in terms:
        normalized = term.strip()
        key = normalized.lower()
        is_acronym = normalized.isupper() and len(normalized) >= 2
        if (len(normalized) <= 3 and not is_acronym) or key in stop_words or key in seen:
            continue
        seen.add(key)
        filtered.append(normalized)

    return filtered[:max_keywords]


def _format_arxiv_term(keyword: str) -> str:
    keyword = keyword.replace('"', "").strip()
    if not keyword:
        return ""
    if " " in keyword:
        return f'all:"{keyword}"'
    return f"all:{keyword}"


def _build_arxiv_query(keywords: list[str]) -> str:
    terms = [_format_arxiv_term(keyword) for keyword in keywords[:5]]
    return " OR ".join(term for term in terms if term)


def _year_start(years_back: int = 3) -> str:
    return f"{date.today().year - years_back}-01-01"


async def extract_keywords_openrouter(
    paper_text: str,
    *,
    model_name: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    max_context_chars: int = 25000,
) -> list[str]:
    key = api_key or os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is required for LLM keyword extraction")

    client = AsyncOpenAI(
        base_url=base_url or os.getenv("BASE_URL", "https://openrouter.ai/api/v1"),
        api_key=key,
    )
    response = await client.chat.completions.parse(
        model=model_name or os.getenv("REVIEW_MODEL_NAME", "google/gemini-3.5-flash"),
        messages=[
            {
                "role": "system",
                "content": "You extract academic search terms for related-work discovery.",
            },
            {
                "role": "user",
                "content": f"{KEYWORD_EXTRACTION_PROMPT}\n\n# Paper\n\n{paper_text[:max_context_chars]}",
            },
        ],
        response_format=KeywordExtractionResult,
        max_completion_tokens=1000,
        temperature=0.0,
    )
    parsed = response.choices[0].message.parsed
    if not parsed:
        return []
    keywords = parsed.keywords + parsed.query_hints
    deduped = list(dict.fromkeys(keyword.strip() for keyword in keywords if keyword.strip()))
    return deduped[:10]


async def search_arxiv_papers(keywords: list[str], max_results: int = 6) -> list[dict[str, Any]]:
    if not keywords:
        return []

    try:
        from academia_mcp.tools import arxiv_search
    except Exception as exc:
        logger.warning("academia_mcp arXiv search is unavailable: %s", exc)
        return []

    query = _build_arxiv_query(keywords)
    if not query:
        return []

    response = await asyncio.to_thread(
        arxiv_search,
        query=query,
        limit=max_results,
        sort_by="submittedDate",
        sort_order="descending",
        include_abstracts=True,
        start_date=_year_start(),
    )
    papers = []
    for result in response.results:
        papers.append(
            {
                "title": result.title,
                "abstract": result.abstract or "",
                "arxiv_id": result.id,
                "submitted": result.published,
            }
        )
    return papers


def rank_similar_papers(
    paper_text: str,
    candidate_papers: list[dict[str, Any]],
    *,
    threshold: float = 0.2,
) -> list[dict[str, Any]]:
    target_words = set(re.findall(r"\b[a-zA-Z]{4,}\b", paper_text.lower()))
    ranked = []

    for paper in candidate_papers:
        candidate_text = f"{paper.get('title', '')} {paper.get('abstract', '')}"
        candidate_words = set(re.findall(r"\b[a-zA-Z]{4,}\b", candidate_text.lower()))
        union = len(target_words | candidate_words)
        similarity = len(target_words & candidate_words) / union if union else 0.0
        if similarity >= threshold:
            enriched = dict(paper)
            enriched["similarity_score"] = similarity
            ranked.append(enriched)

    ranked.sort(key=lambda paper: paper["similarity_score"], reverse=True)
    return ranked


def format_related_work_context(related_papers: list[dict[str, Any]], keywords: list[str]) -> str:
    if not related_papers:
        return "No closely related recent work found."

    parts = [
        "# Related Recent Work",
        f"Search keywords: {', '.join(keywords)}",
        "Use this only for novelty, positioning, and related-work coverage checks.",
    ]
    for index, paper in enumerate(related_papers, 1):
        title = paper.get("title", "Unknown title")
        abstract = paper.get("abstract", "No abstract available")[:500]
        arxiv_id = paper.get("arxiv_id", "")
        similarity = paper.get("similarity_score")
        similarity_text = f"\nSimilarity: {similarity:.2f}" if isinstance(similarity, float) else ""
        parts.append(
            f"## {index}. {title}\n"
            f"arXiv: {arxiv_id or 'unknown'}{similarity_text}\n"
            f"Abstract: {abstract}"
        )
    return "\n\n".join(parts)


async def enrich_with_related_work(
    paper_text: str,
    *,
    use_llm_keywords: bool = False,
    model_name: Optional[str] = None,
    max_related: int = 3,
    keyword_extractor: KeywordExtractor | None = None,
    paper_searcher: PaperSearcher | None = None,
) -> str:
    if keyword_extractor is not None:
        keywords = await keyword_extractor(paper_text)
    elif use_llm_keywords:
        keywords = await extract_keywords_openrouter(paper_text, model_name=model_name)
    else:
        keywords = extract_keywords_regex(paper_text)

    if not keywords:
        return "No search keywords could be extracted for related-work discovery."

    searcher = paper_searcher or search_arxiv_papers
    candidates = await searcher(keywords, max_related * 2)
    ranked = rank_similar_papers(paper_text, candidates)[:max_related]
    return format_related_work_context(ranked, keywords)
