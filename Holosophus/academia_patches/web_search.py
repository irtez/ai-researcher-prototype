from typing import List, Optional, Tuple

import requests
from pydantic import BaseModel, Field

from academia_mcp.settings import settings
from academia_mcp.utils import get_with_retries, post_with_retries, sanitize_output

EXA_SEARCH_URL = "https://api.exa.ai/search"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
SEARXNG_DEFAULT_URL = "http://searxng:8080"
EXCLUDE_DOMAINS = ["chatpaper.com"]


def _parse_domains(query: str) -> Tuple[str, List[str]]:
    site_term = "site:"
    if site_term not in query:
        return query, []
    parts = query.split()
    query_parts = []
    include_domains = []
    for part in parts:
        if not part.startswith(site_term):
            query_parts.append(part)
            continue
        domain = part[len(site_term) :]
        if domain:
            include_domains.append(domain)
    query = " ".join(query_parts)
    return query, include_domains


class WebSearchEntry(BaseModel):  # type: ignore
    id: str = Field(description="ID of the search entry, usually the URL")
    title: str = Field(description="Title of the web page")
    content: str = Field(description="Content of the web page")


class WebSearchResponse(BaseModel):  # type: ignore
    results: List[WebSearchEntry] = Field(description="Results of the search")
    search_provider: str = Field(description="Provider used to get the results")


def web_search(
    query: str,
    limit: int = 20,
    provider: str = "searxng",
    include_domains: Optional[List[str]] = None,
) -> WebSearchResponse:
    """
    Search the web using SearXNG (self-hosted), Exa Search, Brave Search or Tavily and return normalized results.
    If the specified provider is not available, the function will try to use the next available provider.
    Default provider is SearXNG (self-hosted, unlimited, no API key needed).

    Args:
        query: The search query, required.
        limit: The maximum number of items to return. 20 by default, maximum 25.
        provider: The provider to use. "searxng", "exa", "tavily" or "brave". "searxng" by default.
        include_domains: Optional list of domains to include in the search. None by default.
    """
    providers = ("searxng", "tavily", "brave", "exa")
    assert provider in providers, "Error: provider must be 'searxng', 'exa', 'tavily' or 'brave'"
    if include_domains:
        assert len(include_domains) > 0, "Error: include_domains should be a non-empty list"
        assert all(
            isinstance(domain, str) for domain in include_domains
        ), "Error: include_domains should be a list of strings"

    query, query_include_domains = _parse_domains(query)
    if query_include_domains:
        if include_domains:
            include_domains.extend(query_include_domains)
        else:
            include_domains = query_include_domains

    is_tavily_available = bool(settings.TAVILY_API_KEY)
    is_exa_available = bool(settings.EXA_API_KEY)
    is_brave_available = bool(settings.BRAVE_API_KEY)
    is_searxng_available = bool(getattr(settings, "SEARXNG_URL", None))
    assert is_tavily_available or is_exa_available or is_brave_available or is_searxng_available
    availability = {
        "searxng": is_searxng_available,
        "tavily": is_tavily_available,
        "brave": is_brave_available,
        "exa": is_exa_available,
    }

    if not availability[provider]:
        for p in providers:
            if availability[p]:
                provider = p
                break

    result: Optional[WebSearchResponse] = None
    if provider == "searxng":
        result = searxng_web_search(query, limit, include_domains=include_domains)
    elif provider == "exa":
        result = exa_web_search(query, limit, include_domains=include_domains)
    elif provider == "brave":
        result = brave_web_search(query, limit)
    elif provider == "tavily":
        result = tavily_web_search(query, limit, include_domains=include_domains)
    assert result is not None, "Error: No provider was available"
    return result


def searxng_web_search(
    query: str, limit: int = 20, include_domains: Optional[List[str]] = None
) -> WebSearchResponse:
    """
    Search the web using a self-hosted SearXNG instance and return results.
    Aggregates results from multiple search engines (Google, Bing, DuckDuckGo, etc.).
    No API key required.

    Args:
        query: The search query, required.
        limit: The maximum number of items to return. 20 by default.
        include_domains: Optional list of domains to include in the search.
    """
    assert isinstance(query, str), "Error: Your search query must be a string"
    assert query.strip(), "Error: Your query should not be empty"
    assert isinstance(limit, int), "Error: limit should be an integer"
    assert 0 < limit <= 50, "Error: limit should be between 1 and 50"

    base_url = getattr(settings, "SEARXNG_URL", "") or SEARXNG_DEFAULT_URL
    search_query = query
    if include_domains:
        site_filters = " OR ".join(f"site:{d}" for d in include_domains)
        search_query = f"{query} ({site_filters})"

    params = {
        "q": search_query,
        "format": "json",
        "pageno": 1,
    }
    url = base_url.rstrip("/") + "/search"

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()

    data = response.json()
    raw_results = data.get("results", [])[:limit]
    entries = []
    seen_urls: set = set()
    for r in raw_results:
        result_url = r.get("url", "")
        if not result_url or result_url in seen_urls:
            continue
        if any(domain in result_url for domain in EXCLUDE_DOMAINS):
            continue
        seen_urls.add(result_url)
        title = r.get("title", "")
        content = r.get("content", "")
        content = sanitize_output(content)
        entries.append(WebSearchEntry(id=result_url, title=title, content=content))

    return WebSearchResponse(results=entries, search_provider="searxng")


def tavily_web_search(
    query: str, limit: int = 20, include_domains: Optional[List[str]] = None
) -> WebSearchResponse:
    """
    Search the web using Tavily and return results.

    Args:
        query: The search query, required.
        limit: The maximum number of items to return. 20 by default, maximum 25.
        include_domains: Optional list of domains to include in the search. None by default.
    """
    assert isinstance(query, str), "Error: Your search query must be a string"
    assert query.strip(), "Error: Your query should not be empty"
    assert isinstance(limit, int), "Error: limit should be an integer"
    assert 0 < limit <= 25, "Error: limit should be between 1 and 25"
    if include_domains:
        assert len(include_domains) > 0, "Error: include_domains should be a non-empty list"
        assert all(
            isinstance(domain, str) for domain in include_domains
        ), "Error: include_domains should be a list of strings"

    key = settings.TAVILY_API_KEY or ""
    assert key, "Error: TAVILY_API_KEY is not set and no api_key was provided"
    payload = {
        "query": query,
        "max_results": limit,
        "auto_parameters": True,
        "exclude_domains": EXCLUDE_DOMAINS,
    }
    if include_domains:
        payload["include_domains"] = include_domains
    response = post_with_retries(TAVILY_SEARCH_URL, payload, key)
    results = response.json()["results"]
    for result in results:
        content = " ".join(result["content"].split(" ")[:40])
        content = sanitize_output(content.strip("., "))
        result["content"] = content
        result.pop("raw_content", None)
        result.pop("score", None)
    entries = [
        WebSearchEntry(id=result["url"], title=result["title"], content=result["content"])
        for result in results
    ]
    return WebSearchResponse(results=entries, search_provider="tavily")


def exa_web_search(
    query: str, limit: int = 20, include_domains: Optional[List[str]] = None
) -> WebSearchResponse:
    """
    Search the web using Exa and return results.

    Args:
        query: The search query, required.
        limit: The maximum number of items to return. 20 by default, maximum 25.
        include_domains: Optional list of domains to include in the search. None by default.
    """
    assert isinstance(query, str), "Error: Your search query must be a string"
    assert query.strip(), "Error: Your query should not be empty"
    assert isinstance(limit, int), "Error: limit should be an integer"
    assert 0 < limit <= 25, "Error: limit should be between 1 and 25"
    if include_domains:
        assert len(include_domains) > 0, "Error: include_domains should be a non-empty list"
        assert all(
            isinstance(domain, str) for domain in include_domains
        ), "Error: include_domains should be a list of strings"

    key = settings.EXA_API_KEY or ""
    assert key, "Error: EXA_API_KEY is not set and no api_key was provided"
    payload = {
        "query": query,
        "type": "auto",
        "numResults": limit,
        "context": False,
        "excludeDomains": EXCLUDE_DOMAINS,
        "contents": {
            "text": False,
            "highlights": {
                "numSentences": 5,
            },
            "context": False,
        },
    }
    if include_domains:
        payload["includeDomains"] = include_domains

    response = post_with_retries(EXA_SEARCH_URL, payload, key)
    results = response.json()["results"]
    entries = []
    for result in results:
        content = " || ".join(result["highlights"])
        content = sanitize_output(content)
        entries.append(WebSearchEntry(id=result["url"], title=result["title"], content=content))
    return WebSearchResponse(results=entries, search_provider="exa")


def brave_web_search(
    query: str, limit: int = 20, extra_snippets: bool = False
) -> WebSearchResponse:
    """
    Search the web using Brave and return results.

    Args:
        query: The search query, required.
        limit: The maximum number of items to return. 20 by default, maximum 20.
        extra_snippets: Include extra snippets in the content. False by default.
    """
    assert isinstance(query, str), "Error: Your search query must be a string"
    assert query.strip(), "Error: Your query should not be empty"
    assert isinstance(limit, int), "Error: limit should be an integer"
    assert 0 < limit <= 20, "Error: limit should be between 1 and 20"

    key = settings.BRAVE_API_KEY or ""
    assert key, "Error: BRAVE_API_KEY is not set and no api_key was provided"
    payload = {"q": query, "count": limit, "extra_snippets": extra_snippets}
    response = get_with_retries(BRAVE_SEARCH_URL, key, params=payload)
    results = response.json()["web"]["results"]
    entries = []
    for result in results:
        content = str(result["description"])
        if extra_snippets and "extra_snippets" in result:
            content += "\n\nExtra snippets:\n" + "\n".join(result["extra_snippets"])
        content = sanitize_output(content)
        entries.append(WebSearchEntry(id=result["url"], title=result["title"], content=content))
    return WebSearchResponse(results=entries, search_provider="brave")
