"""
Additional search tools for the librarian agent.
These provide broader coverage across academic databases.

Mounted into academia_mcp container and imported by patched server.py.
"""

from typing import Optional

import requests
from pydantic import BaseModel, Field


class SearchResult(BaseModel):  # type: ignore
    title: str = Field(description="Paper title")
    url: str = Field(description="URL to the paper")
    snippet: str = Field(description="Short description or abstract excerpt")
    source: str = Field(description="Which search engine found this")


class SearchResults(BaseModel):  # type: ignore
    results: list[SearchResult] = Field(description="Search results")
    query: str = Field(description="The query used")


def openalex_search(
    query: str,
    max_results: int = 15,
    from_year: int = 0,
    to_year: int = 0,
    sort_by: str = "relevance_score",
) -> SearchResults:
    """Search OpenAlex for academic papers. Free, no API key needed.
    Covers 270M+ scholarly works — the largest open academic index.

    Args:
        query: Search query string.
        max_results: Maximum number of results to return. Default 15.
        from_year: Filter papers from this year onwards. 0 = no filter.
        to_year: Filter papers up to this year. 0 = no filter.
        sort_by: Sort order. Options: relevance_score, cited_by_count, publication_date.
    """
    params = {
        "search": query,
        "per_page": min(max_results, 50),
        "select": "id,title,publication_year,cited_by_count,doi,ids,abstract_inverted_index",
    }
    if sort_by:
        params["sort"] = f"{sort_by}:desc"
    filters = []
    if from_year:
        filters.append(f"from_publication_date:{from_year}-01-01")
    if to_year:
        filters.append(f"to_publication_date:{to_year}-12-31")
    if filters:
        params["filter"] = ",".join(filters)

    try:
        resp = requests.get(
            "https://api.openalex.org/works",
            params=params,
            headers={"User-Agent": "Holosophus/1.0 (mailto:research@holosophus.ai)"},
            timeout=15,
        )
        resp.raise_for_status()
        items = resp.json().get("results", [])
    except Exception as e:
        return SearchResults(results=[], query=query)

    results = []
    for item in items:
        title = item.get("title", "")
        doi = item.get("doi", "")
        ids = item.get("ids", {})
        openalex_id = ids.get("openalex", item.get("id", ""))
        url = doi or openalex_id
        year = item.get("publication_year", "")
        citations = item.get("cited_by_count", 0)
        snippet = f"[{year}] Citations: {citations}"
        results.append(SearchResult(
            title=title, url=url, snippet=snippet, source="openalex"
        ))

    return SearchResults(results=results, query=query)


def crossref_search(
    query: str,
    max_results: int = 15,
) -> SearchResults:
    """Search CrossRef for published academic papers with DOIs.
    Free, no API key needed. Good for finding formally published papers.

    Args:
        query: Search query string.
        max_results: Maximum number of results. Default 15.
    """
    try:
        resp = requests.get(
            "https://api.crossref.org/works",
            params={
                "query": query,
                "rows": min(max_results, 50),
                "select": "DOI,title,author,published-print,container-title",
            },
            headers={"User-Agent": "Holosophus/1.0 (mailto:research@holosophus.ai)"},
            timeout=15,
        )
        resp.raise_for_status()
        items = resp.json().get("message", {}).get("items", [])
    except Exception as e:
        return SearchResults(results=[], query=query)

    results = []
    for item in items:
        title = item.get("title", [""])[0] if item.get("title") else ""
        doi = item.get("DOI", "")
        url = f"https://doi.org/{doi}" if doi else ""
        venue = item.get("container-title", [""])[0] if item.get("container-title") else ""
        pub_date = item.get("published-print", {})
        year = pub_date.get("date-parts", [[""]])[0][0] if pub_date else ""
        snippet = f"[{year}] {venue}" if venue else f"[{year}]"
        if title:
            results.append(SearchResult(
                title=title, url=url, snippet=snippet, source="crossref"
            ))

    return SearchResults(results=results, query=query)


def dblp_search(
    query: str,
    max_results: int = 15,
) -> SearchResults:
    """Search DBLP for computer science papers.
    Free, no API key. Strong for CS conference and journal papers.

    Args:
        query: Search query string.
        max_results: Maximum number of results. Default 15.
    """
    try:
        resp = requests.get(
            "https://dblp.org/search/publ/api",
            params={"q": query, "format": "json", "h": min(max_results, 50)},
            timeout=15,
        )
        resp.raise_for_status()
        hits = resp.json().get("result", {}).get("hits", {}).get("hit", [])
    except Exception as e:
        return SearchResults(results=[], query=query)

    results = []
    for h in hits:
        info = h.get("info", {})
        title = info.get("title", "")
        url = info.get("ee", info.get("url", ""))
        venue = info.get("venue", "")
        year = info.get("year", "")
        snippet = f"[{year}] {venue}" if venue else f"[{year}]"
        if title:
            results.append(SearchResult(
                title=title, url=url, snippet=snippet, source="dblp"
            ))

    return SearchResults(results=results, query=query)


def s2_search(
    query: str,
    max_results: int = 10,
    year_range: str = "",
    fields_of_study: str = "",
) -> SearchResults:
    """Search Semantic Scholar for academic papers by keyword.
    Best relevance ranking for academic papers. Free, no API key.

    Args:
        query: Search query string.
        max_results: Maximum number of results. Default 10.
        year_range: Year filter, e.g. "2023-2025". Empty = no filter.
        fields_of_study: Comma-separated fields, e.g. "Computer Science". Empty = all.
    """
    import time

    params = {
        "query": query,
        "limit": min(max_results, 100),
        "fields": "title,authors,venue,year,abstract,citationCount,externalIds",
    }
    if year_range:
        params["year"] = year_range
    if fields_of_study:
        params["fieldsOfStudy"] = fields_of_study

    for attempt in range(2):
        try:
            resp = requests.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params=params,
                timeout=15,
            )
            if resp.status_code == 200:
                items = resp.json().get("data", [])
                results = []
                for item in items:
                    title = item.get("title", "")
                    ext_ids = item.get("externalIds", {})
                    arxiv_id = ext_ids.get("ArXiv", "")
                    url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""
                    year = item.get("year", "")
                    citations = item.get("citationCount", 0)
                    venue = item.get("venue", "")
                    abstract = (item.get("abstract") or "")[:200]
                    snippet = f"[{year}] {venue} | Citations: {citations} | {abstract}"
                    if title:
                        results.append(SearchResult(
                            title=title, url=url, snippet=snippet, source="s2"
                        ))
                return SearchResults(results=results, query=query)
            if resp.status_code == 429:
                time.sleep(3)
                continue
        except Exception:
            pass

    return SearchResults(results=[], query=query)


def alphaxiv_search(
    query: str,
    max_results: int = 10,
) -> SearchResults:
    """Search AlphaXiv for ML/AI papers. Free, no API key, very fast.
    Returns papers with titles, abstracts, and AI-generated summaries.

    Args:
        query: Search query string.
        max_results: Maximum number of results. Default 10.
    """
    try:
        resp = requests.get(
            "https://api.alphaxiv.org/v1/search/paper",
            params={"q": query},
            timeout=10,
        )
        if resp.status_code != 200:
            return SearchResults(results=[], query=query)
        raw = resp.json()
        papers = raw if isinstance(raw, list) else raw.get("papers", [])
    except Exception:
        return SearchResults(results=[], query=query)

    results = []
    for item in papers[:max_results]:
        title = item.get("title", "")
        arxiv_id = item.get("arxivId", item.get("paperId", ""))
        url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""
        summary = item.get("summary", item.get("abstract", ""))[:200]
        if title:
            results.append(SearchResult(
                title=title, url=url, snippet=summary, source="alphaxiv"
            ))

    return SearchResults(results=results, query=query)
