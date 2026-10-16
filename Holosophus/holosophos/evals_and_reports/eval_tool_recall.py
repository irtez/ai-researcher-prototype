"""
Tier 1 Eval: Direct Tool Recall Measurement.

Calls MCP search tools DIRECTLY (no executor, no LLM loop).
Measures whether search tools CAN find the target paper.
This is the ceiling for librarian agent accuracy.

Much faster than full agent eval: ~10-15 min for 95 queries.

Usage:
    cd Holosophus/holosophos

    # Smoke test (3 queries)
    uv run python reports/eval_tool_recall.py --nrows 1

    # Full benchmark
    uv run python reports/eval_tool_recall.py

    # Specific benchmark only
    uv run python reports/eval_tool_recall.py --benchmarks librarian
"""

import json
import threading
import time
from pathlib import Path
from typing import Any, Optional

import fire  # type: ignore
import requests
from ddgs import DDGS
from dotenv import load_dotenv

# Academic APIs (OpenAlex etc.) must BYPASS the corporate/Clash proxy, which resets
# connections to api.openalex.org (verified: proxied 0/4 vs direct 3/4). OpenRouter,
# conversely, needs the proxy (direct can't reach it) — that path uses the OpenAI/httpx
# client which reads the proxy env separately, so it is unaffected by this session.
_DIRECT = requests.Session()
_DIRECT.trust_env = False  # ignore HTTP(S)_PROXY env → direct egress for source APIs


DATA_DIR = Path(__file__).parent / "data"
RESULTS_DIR = Path(__file__).parent / "results" / "raw"

BENCHMARK_FILES = {
    "librarian": "librarian_test_sample.jsonl",
    "litsearch": "litsearch_sample.jsonl",
    "infodeepseek": "infodeepseek_sample.jsonl",
    "litsearch_full": "litsearch_full.jsonl",  # full LitSearch 597 (statistical power)
}

# Semantic Scholar API (no key needed, 1 req/sec shared pool)
S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_FIELDS = "title,externalIds,year,citationCount,abstract"


def ddg_search(query: str, max_results: int = 10) -> list[dict]:
    """DuckDuckGo search. Free, no API key, reliable."""
    try:
        return list(DDGS().text(query, max_results=max_results))
    except Exception as e:
        return [{"error": str(e)}]


def google_scholar_search(query: str, max_results: int = 10) -> list[dict]:
    """Google Scholar search via scholarly. Best relevance ranking. Free, no key."""
    try:
        from scholarly import scholarly

        results = []
        for i, r in enumerate(scholarly.search_pubs(query)):
            if i >= max_results:
                break
            bib = r.get("bib", {})
            results.append({
                "title": bib.get("title", ""),
                "year": bib.get("pub_year", ""),
                "abstract": bib.get("abstract", ""),
                "url": r.get("pub_url", r.get("eprint_url", "")),
                "citations": r.get("num_citations", 0),
                "source": "google_scholar",
            })
        return results
    except Exception as e:
        return [{"error": str(e)}]


_s2_last_call: float = 0.0


_s2_lock = threading.Lock()


def s2_search(query: str, limit: int = 10) -> list[dict]:
    """Semantic Scholar keyword search. Free, no key. 1 req/sec rate limit."""
    global _s2_last_call
    # Serialize S2 across worker threads and enforce >=0.5s spacing (shared 1 req/sec pool).
    with _s2_lock:
        elapsed = time.time() - _s2_last_call
        if elapsed < 0.5:
            time.sleep(0.5 - elapsed)
        for attempt in range(2):
            try:
                _s2_last_call = time.time()
                resp = _DIRECT.get(
                    S2_SEARCH_URL,
                    params={"query": query, "limit": limit, "fields": S2_FIELDS},
                    timeout=10,
                )
                if resp.status_code == 200:
                    return resp.json().get("data", [])
                if resp.status_code == 429:
                    time.sleep(3)
                    continue
                return [{"error": f"S2 {resp.status_code}"}]
            except Exception as e:
                return [{"error": str(e)}]
    return []


def _robust_get(url: str, *, retries: int = 3, backoff: float = 0.8, **kwargs):
    """GET with retries on transient connection errors (resets/timeouts). Returns Response or None."""
    for attempt in range(retries):
        try:
            return _DIRECT.get(url, **kwargs)
        except Exception:
            time.sleep(backoff * (attempt + 1))
    return None


def arxiv_search_direct(query: str, max_results: int = 10) -> list[dict]:
    """Direct ArXiv API call with rate limiting."""
    import xml.etree.ElementTree as ET

    url = "http://export.arxiv.org/api/query"
    params = {"search_query": f"all:{query}", "max_results": max_results}
    try:
        resp = _DIRECT.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return [{"error": f"ArXiv {resp.status_code}"}]
        root = ET.fromstring(resp.text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        results = []
        for entry in root.findall("atom:entry", ns):
            title = entry.findtext("atom:title", "", ns).strip()
            entry_id = entry.findtext("atom:id", "", ns).strip()
            summary = entry.findtext("atom:summary", "", ns).strip()
            results.append({"title": title, "id": entry_id, "summary": summary})
        return results
    except Exception as e:
        return [{"error": str(e)}]


def openalex_search(query: str, limit: int = 10) -> list[dict]:
    """OpenAlex search. Free, no key, 200M+ works, excellent coverage."""
    resp = _robust_get(
        "https://api.openalex.org/works",
        params={
            "search": query,
            "per_page": limit,
            "select": "id,title,publication_year,cited_by_count,doi,ids",
        },
        headers={"User-Agent": "Holosophus/1.0 (mailto:research@holosophus.ai)"},
        timeout=15,
    )
    if resp is None:
        return [{"error": "OpenAlex conn_reset"}]
    if resp.status_code != 200:
        return [{"error": f"OpenAlex {resp.status_code}"}]
    try:
        return resp.json().get("results", [])
    except Exception as e:
        return [{"error": str(e)}]


_LLM_QUERY_CACHE: dict[str, list[str]] = {}


def llm_reformulate(query: str) -> list[str]:
    """Use LLM to generate focused search queries. Cached per query."""
    if query in _LLM_QUERY_CACHE:
        return _LLM_QUERY_CACHE[query]

    try:
        import os
        from openai import OpenAI

        # Read API key from .env
        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not api_key:
            from dotenv import dotenv_values

            env = dotenv_values(Path(__file__).parent.parent.parent / ".env")
            api_key = env.get("OPENROUTER_API_KEY", "")
        if not api_key:
            return []

        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            timeout=15.0,
        )
        resp = client.chat.completions.create(
            model="deepseek/deepseek-chat-v3-0324",
            messages=[{
                "role": "user",
                "content": (
                    "A user is looking for a SPECIFIC academic paper. Generate 5 search "
                    "queries to find it. Use diverse strategies:\n"
                    "1. Key technical terms + abbreviations (3-5 words)\n"
                    "2. Likely paper title words (what would the authors name it?)\n"
                    "3. Method name + task + 'arxiv' (for DDG search)\n"
                    "4. Author-style query (key technique + domain)\n"
                    "5. Alternative terminology (synonyms, related terms)\n"
                    "One query per line, nothing else.\n\n"
                    f"Topic: {query}"
                ),
            }],
            max_tokens=150,
            temperature=0.3,
        )
        content = resp.choices[0].message.content or ""
        variants = [line.strip().strip('"').strip("'") for line in content.strip().split("\n") if line.strip()]
        # Remove numbering (1. 2. 3.)
        variants = [v.lstrip("0123456789.-) ") for v in variants]
        _LLM_QUERY_CACHE[query] = variants
        return variants
    except Exception as e:
        _warn_llm_dead("llm_reformulate", str(e))
        return []


_PERSONA_CACHE: dict[str, list[str]] = {}
_LLM_WARNED = False


def _warn_llm_dead(where: str, err: str = "") -> None:
    """Print a one-time loud warning when an LLM call fails (so a dead API key
    cannot silently masquerade as a rule-based result)."""
    global _LLM_WARNED
    if not _LLM_WARNED:
        _LLM_WARNED = True
        import sys

        print(
            f"[WARN] LLM call in {where} failed ({err[:90]}); falling back to rule-based. "
            f"ANY result that depends on the LLM (personas/reranker) is INVALID until a "
            f"working key is configured.",
            file=sys.stderr,
            flush=True,
        )


def persona_queries(query: str) -> list[str]:
    """Generate search queries from 5 researcher personas (LLM via OpenRouter).

    Diverse viewpoints widen lexical coverage for find-paper-by-description.
    Returns [original_query, persona_1, ...] or [] on failure (caller falls back).
    """
    if query in _PERSONA_CACHE:
        return _PERSONA_CACHE[query]
    try:
        import os
        from openai import OpenAI

        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not api_key:
            from dotenv import dotenv_values

            env = dotenv_values(Path(__file__).parent.parent.parent / ".env")
            api_key = env.get("OPENROUTER_API_KEY", "")
        if not api_key:
            return []
        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key, timeout=20.0)
        resp = client.chat.completions.create(
            model="deepseek/deepseek-chat-v3-0324",
            messages=[{
                "role": "user",
                "content": (
                    "A user seeks a SPECIFIC academic paper. Imagine 5 researchers viewing "
                    "it from different angles (empiricist, theorist, systems/engineering, "
                    "survey-writer, skeptic). Each writes ONE short search query (3-8 words) "
                    "they would use to find it. Output one query per line, nothing else.\n\n"
                    f"Topic: {query}"
                ),
            }],
            max_tokens=160,
            temperature=0.4,
        )
        content = resp.choices[0].message.content or ""
        variants = [
            line.strip().strip('"').strip("'").lstrip("0123456789.-) ")
            for line in content.strip().split("\n")
            if line.strip()
        ]
        variants = [query] + [v for v in variants if v]
        _PERSONA_CACHE[query] = variants
        return variants
    except Exception as e:
        _warn_llm_dead("persona_queries", str(e))
        return []


def reformulate_queries(query: str) -> list[str]:
    """Generate query variants for broader search coverage.

    No LLM needed — rule-based reformulation:
    1. Original query
    2. Key terms only (remove filler/question words)
    3. Quoted exact phrases (for key noun phrases)
    """
    import re

    variants = [query]

    # Variant 2: Extract key terms (remove question/filler words)
    filler = {
        "which", "what", "how", "can", "you", "find", "paper", "that", "the",
        "first", "introduces", "introduced", "proposes", "proposed", "presents",
        "presented", "describes", "described", "there", "any", "recent", "work",
        "about", "using", "used", "for", "with", "from", "this", "some", "list",
        "provide", "complete", "could", "suggest", "recommend", "point", "towards",
        "research", "are", "has", "been", "was", "were", "does", "did", "have",
        "also", "specifically", "focusing", "all", "explore", "exploring", "papers",
        "articles", "studies", "publications", "scholarly", "refer", "direct",
        "investigate", "investigating", "methods", "method", "approach", "approaches",
        "is", "in", "on", "of", "to", "an", "a", "and", "or", "not", "by",
    }
    words = re.findall(r'\b[a-zA-Z][a-zA-Z0-9-]+\b', query.lower())
    key_words = [w for w in words if w not in filler and len(w) > 2]
    if key_words and len(key_words) != len(words):
        variants.append(" ".join(key_words))

    # Variant 3: Try with "arxiv" appended for better DDG results
    if "arxiv" not in query.lower():
        short = " ".join(key_words[:6]) if key_words else query[:80]
        variants.append(f"{short} arxiv paper")

    # Variant 4: Short key terms (3-4 words) — better for S2 relevance ranking
    if len(key_words) > 4:
        variants.append(" ".join(key_words[:4]))

    # Variant 5+: LLM-generated expert reformulations
    llm_variants = llm_reformulate(query)
    variants.extend(llm_variants)

    return variants[:8]


def dblp_search(query: str, limit: int = 10) -> list[dict]:
    """DBLP search. Free, no key, strong for CS conference/journal papers."""
    try:
        resp = _DIRECT.get(
            "https://dblp.org/search/publ/api",
            params={"q": query, "format": "json", "h": limit},
            timeout=15,
        )
        if resp.status_code != 200:
            return [{"error": f"DBLP {resp.status_code}"}]
        hits = resp.json().get("result", {}).get("hits", {}).get("hit", [])
        return [h.get("info", {}) for h in hits]
    except Exception as e:
        return [{"error": str(e)}]


def alphaxiv_search(query: str, limit: int = 10) -> list[dict]:
    """AlphaXiv search. Free, no auth, 1.5M req/hour. Good for ML/AI papers."""
    resp = _robust_get(
        "https://api.alphaxiv.org/v1/search/paper",
        params={"q": query},
        timeout=10,
    )
    if resp is None:
        return [{"error": "AlphaXiv conn_reset"}]
    if resp.status_code != 200:
        return [{"error": f"AlphaXiv {resp.status_code}"}]
    try:
        results = resp.json()
        papers = results if isinstance(results, list) else results.get("papers", [])
        return papers[:limit]
    except Exception as e:
        return [{"error": str(e)}]


def crossref_search(query: str, limit: int = 10) -> list[dict]:
    """CrossRef search. Free, no key, excellent for published papers with DOIs."""
    try:
        resp = _DIRECT.get(
            "https://api.crossref.org/works",
            params={
                "query": query,
                "rows": limit,
                "select": "DOI,title,author,published-print,container-title",
            },
            headers={"User-Agent": "Holosophus/1.0 (mailto:research@holosophus.ai)"},
            timeout=15,
        )
        if resp.status_code != 200:
            return [{"error": f"CrossRef {resp.status_code}"}]
        return resp.json().get("message", {}).get("items", [])
    except Exception as e:
        return [{"error": str(e)}]


def searxng_search(query: str, limit: int = 20) -> list[dict]:
    """SearXNG self-hosted meta-search — aggregates Google Scholar / Semantic Scholar /
    OpenAlex / arXiv / Crossref / web engines in one call, via the container's clean egress.
    Bypasses host-side OpenAlex rate-limits and restores Exa/Tavily-style web coverage."""
    import os

    port = os.getenv("SEARXNG_PORT", "8888")
    resp = _robust_get(
        f"http://localhost:{port}/search",
        params={"q": query, "format": "json", "language": "en"},
        headers={"User-Agent": "Holosophus/1.0"},
        timeout=25,
    )
    if resp is None:
        return [{"error": "SearXNG conn"}]
    if resp.status_code != 200:
        return [{"error": f"SearXNG {resp.status_code}"}]
    try:
        return resp.json().get("results", [])[:limit]
    except Exception as e:
        return [{"error": str(e)}]


def search_for_paper(
    query: str,
    sources: Optional[Any] = None,
    query_strategy: str = "rule",
) -> dict[str, str]:
    """Run all search tools for a query with reformulated variants.
    Uses ThreadPoolExecutor to parallelize API calls for speed.

    sources: optional iterable/comma-string of source prefixes to include
             (openalex, alphaxiv, ddg, crossref, dblp, s2, arxiv). None = all.
    query_strategy: 'rule' (default, byte-identical to baseline) or 'personas'
             (adds persona-diversified variants + wider academic fan-out).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if query_strategy == "raw":
        variants = [query]
    else:
        variants = reformulate_queries(query)
        if query_strategy == "personas":
            for v in persona_queries(query):
                if v not in variants:
                    variants.append(v)
    if not variants:
        variants = [query]
    n_acad = 4 if query_strategy == "personas" else 2
    key_q = variants[1] if len(variants) > 1 else query

    # Build all search tasks (name, callable)
    tasks: list[tuple[str, callable]] = []

    # OpenAlex — n_acad variants
    for i, q in enumerate(variants[:n_acad]):
        tasks.append((f"openalex_{i}", lambda q=q: openalex_search(q, limit=25)))

    # AlphaXiv — n_acad variants
    for i, q in enumerate(variants[:n_acad]):
        tasks.append((f"alphaxiv_{i}", lambda q=q: alphaxiv_search(q, limit=10)))

    # DDG — all variants
    for i, q in enumerate(variants):
        tasks.append((f"ddg_{i}", lambda q=q: ddg_search(q, max_results=15)))

    # CrossRef — 1 call
    tasks.append(("crossref", lambda: crossref_search(key_q, limit=15)))

    # DBLP — 1 call
    tasks.append(("dblp", lambda: dblp_search(key_q, limit=15)))

    # S2 and ArXiv also go into the parallel pool (rate-limited internally)
    tasks.append(("s2", lambda: s2_search(key_q, limit=10)))
    tasks.append(("arxiv", lambda: arxiv_search_direct(key_q, max_results=10)))

    # SearXNG meta-search (self-hosted; aggregates Scholar/S2/OpenAlex/arXiv/Crossref/web).
    tasks.append(("searxng", lambda: searxng_search(key_q, limit=20)))

    # Optional source ablation: keep only requested source prefixes.
    if sources is not None:
        if isinstance(sources, str):
            src = {s.strip() for s in sources.split(",") if s.strip()}
        else:
            src = set(sources)
        tasks = [(name, fn) for name, fn in tasks if name.split("_")[0] in src]

    # Execute ALL calls in parallel
    results: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(fn): name for name, fn in tasks}
        for future in as_completed(futures):
            name = futures[future]
            try:
                data = future.result()
                results[name] = json.dumps(data, ensure_ascii=False)
            except Exception:
                results[name] = "[]"

    return results


def _extract_titles(results: dict, k: int = 15) -> list:
    """Pull up to k candidate titles from merged source results (for LLM reflection)."""
    titles: list = []
    for payload in results.values():
        try:
            items = json.loads(payload)
        except Exception:
            continue
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            t = it.get("title") or it.get("display_name") or it.get("name") or ""
            if isinstance(t, list):
                t = t[0] if t else ""
            if isinstance(t, str) and t.strip():
                titles.append(t.strip()[:120])
            if len(titles) >= k:
                return titles
    return titles


def llm_followup_queries(query: str, titles: list, n: int = 2) -> list:
    """Reflect on candidates found so far and propose n NEW search queries (LLM)."""
    try:
        import os
        from openai import OpenAI

        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not api_key:
            from dotenv import dotenv_values

            api_key = dotenv_values(Path(__file__).parent.parent.parent / ".env").get(
                "OPENROUTER_API_KEY", ""
            )
        if not api_key:
            return []
        client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=api_key, timeout=25.0)
        found = "\n".join(f"- {t}" for t in titles[:15]) or "(nothing relevant yet)"
        resp = client.chat.completions.create(
            model="deepseek/deepseek-chat-v3-0324",
            messages=[{"role": "user", "content": (
                "You are iteratively searching for a SPECIFIC academic paper.\n"
                f"Target description: {query}\n\n"
                f"Papers found so far (may be off-target/incomplete):\n{found}\n\n"
                f"Identify what is still missing and write {n} NEW, different search queries "
                "(3-7 words each, fresh terminology/angles, not repeats) likely to surface the "
                "target. One query per line, nothing else."
            )}],
            max_tokens=120,
            temperature=0.5,
        )
        content = resp.choices[0].message.content or ""
        qs = [
            ln.strip().strip('"').strip("'").lstrip("0123456789.-) ")
            for ln in content.strip().split("\n")
            if ln.strip()
        ]
        return [q for q in qs if q][:n]
    except Exception as e:
        _warn_llm_dead("llm_followup_queries", str(e))
        return []


def agentic_search(query: str, sources=None, rounds: int = 2, n_followups: int = 2) -> dict:
    """local-deep-researcher-style loop: search -> reflect on gaps -> re-query, N rounds.
    Returns a merged results dict (same shape as search_for_paper)."""
    merged: dict = {}
    r0 = search_for_paper(query, sources=sources, query_strategy="rule")
    for name, payload in r0.items():
        merged[f"r0_{name}"] = payload
    for rnd in range(1, rounds + 1):
        titles = _extract_titles(merged, k=15)
        for j, nq in enumerate(llm_followup_queries(query, titles, n=n_followups)):
            rj = search_for_paper(nq, sources=sources, query_strategy="raw")
            for name, payload in rj.items():
                merged[f"r{rnd}_{j}_{name}"] = payload
    return merged


_LITSEARCH_TITLE_MAP: dict[str, int] | None = None


def _get_litsearch_title_map() -> dict[str, int]:
    """Load LitSearch corpus title→corpusid map. Cached after first load."""
    global _LITSEARCH_TITLE_MAP
    if _LITSEARCH_TITLE_MAP is not None:
        return _LITSEARCH_TITLE_MAP

    map_path = DATA_DIR / "litsearch_title_map.json"
    if map_path.exists():
        with open(map_path) as f:
            _LITSEARCH_TITLE_MAP = json.load(f)
    else:
        from datasets import load_dataset

        corpus = load_dataset("princeton-nlp/LitSearch", "corpus_clean", split="full")
        _LITSEARCH_TITLE_MAP = {}
        for item in corpus:
            title = item["title"].strip().lower()
            if title:
                _LITSEARCH_TITLE_MAP[title] = item["corpusid"]
        with open(map_path, "w") as f:
            json.dump(_LITSEARCH_TITLE_MAP, f)

    return _LITSEARCH_TITLE_MAP


def _normalize_title(title: str) -> str:
    """Normalize title for fuzzy matching."""
    import re

    t = title.lower().strip()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def check_target_found(results: dict[str, str], record: dict) -> dict:
    """Check if any tool result contains the target paper."""
    all_text = " ".join(results.values()).lower()

    found_by_tool: dict[str, str] = {}
    found = False

    # librarian_test: check ArXiv IDs
    if "target_arxiv_ids" in record:
        for aid in record["target_arxiv_ids"]:
            if aid in all_text:
                found = True
                for tool, text in results.items():
                    if aid in text.lower():
                        found_by_tool[tool] = aid

    # litsearch: match by title (corpus_ids won't appear in DDG/S2 results)
    if "target_corpus_ids" in record:
        title_map = _get_litsearch_title_map()
        # Build reverse map: corpusid → normalized title
        target_ids = set(record["target_corpus_ids"])
        target_titles = []
        for title, cid in title_map.items():
            if cid in target_ids:
                target_titles.append((cid, _normalize_title(title)))

        # Check if any target title appears in results
        # Strategy: check 4-word consecutive chunks (fuzzy enough for reformatted titles)
        all_text_norm = _normalize_title(all_text)
        for cid, norm_title in target_titles:
            words = norm_title.split()
            min_chunk = min(4, len(words))
            if len(words) <= 3:
                # Very short title — require all words present
                if all(w in all_text_norm for w in words):
                    found = True
                    for tool, text in results.items():
                        if all(w in _normalize_title(text) for w in words):
                            found_by_tool[tool] = f"title_match:{cid}"
            else:
                # Check if any 4-word chunk from title appears in results
                for start in range(len(words) - min_chunk + 1):
                    chunk = " ".join(words[start : start + min_chunk])
                    if chunk in all_text_norm:
                        found = True
                        for tool, text in results.items():
                            if chunk in _normalize_title(text):
                                found_by_tool[tool] = f"title_match:{cid}"
                        break

    # infodeepseek: check if key terms from answer are found
    if "answer" in record:
        answer = record["answer"].lower()
        key_terms = [w for w in answer.split() if len(w) > 5 and w.isalpha()]
        if key_terms:
            matched = sum(1 for t in key_terms if t in all_text)
            ratio = matched / len(key_terms) if key_terms else 0
            found = ratio >= 0.4
            if found:
                found_by_tool["keyword_match"] = f"{matched}/{len(key_terms)}"

    return {"found": found, "found_by_tool": found_by_tool}


def run_eval(
    benchmarks: str = "all",
    nrows: Optional[int] = None,
    experiment_name: str = "tool_recall_baseline",
    sources: Optional[str] = None,
    query_strategy: str = "rule",
    max_workers: int = 8,
    agentic_rounds: int = 2,
) -> None:
    """Run tool recall evaluation on benchmark samples."""
    load_dotenv()

    if benchmarks == "all":
        bench_list = list(BENCHMARK_FILES.keys())
    else:
        bench_list = [b.strip() for b in benchmarks.split(",")]

    # Load all records
    all_records = []
    for bench_name in bench_list:
        fname = BENCHMARK_FILES.get(bench_name)
        if not fname:
            continue
        fpath = DATA_DIR / fname
        if not fpath.exists():
            print(f"Missing: {fpath}")
            continue
        with open(fpath) as f:
            records = [json.loads(line) for line in f]
        if nrows:
            records = records[:nrows]
        for r in records:
            r["_benchmark"] = bench_name
        all_records.extend(records)
        print(f"  {bench_name}: {len(records)} queries")

    total = len(all_records)
    print(f"\n{'='*60}")
    print(f"Tool Recall Eval: {experiment_name}")
    print(f"Total queries: {total}")
    print(f"Tools: DDG + S2 + ArXiv (direct, no MCP)")
    print(f"{'='*60}\n")

    t_start = time.time()
    eval_results = []

    def _process(record: dict) -> dict:
        query = record.get("query", "")
        q_start = time.time()
        if query_strategy == "agentic":
            tool_results = agentic_search(query, sources=sources, rounds=agentic_rounds)
        else:
            tool_results = search_for_paper(query, sources=sources, query_strategy=query_strategy)
        eval_result = check_target_found(tool_results, record)
        return {
            "benchmark": record["_benchmark"],
            "query": query,
            "found": eval_result["found"],
            "found_by_tool": eval_result.get("found_by_tool", {}),
            "time_s": round(time.time() - q_start),
            "record": record,
        }

    # Parallelize across questions (sources already run in parallel within each).
    from concurrent.futures import ThreadPoolExecutor, as_completed

    done = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_process, r) for r in all_records]
        for fut in as_completed(futures):
            r = fut.result()
            eval_results.append(r)
            done += 1
            tag = "FOUND" if r["found"] else "MISS"
            tu = list(r["found_by_tool"].keys())
            print(
                f"  [{tag}] {r['benchmark']} {done}/{total} ({r['time_s']}s): "
                f"{r['query'][:55]}... {tu if tu else ''}",
                flush=True,
            )

    t_total = time.time() - t_start

    # Summary
    print(f"\n{'='*60}")
    print(f"TOOL RECALL RESULTS ({experiment_name})")
    print(f"{'='*60}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    for bench_name in bench_list:
        br = [r for r in eval_results if r["benchmark"] == bench_name]
        if not br:
            continue
        found = sum(1 for r in br if r["found"])
        total_b = len(br)
        recall = found / total_b * 100 if total_b else 0
        print(f"\n  {bench_name}: {found}/{total_b} ({recall:.1f}%) tool recall")
        for r in br:
            tag = "FOUND" if r["found"] else "MISS"
            print(f"    [{tag}] {r['query'][:70]}...")

    overall_found = sum(1 for r in eval_results if r["found"])
    print(f"\n  OVERALL: {overall_found}/{total} ({overall_found/total*100:.1f}%)")
    print(f"  Wall time: {t_total:.0f}s ({t_total/total:.0f}s/query)")

    # Save results
    raw_path = RESULTS_DIR / f"{experiment_name}_raw.jsonl"
    with open(raw_path, "w") as f:
        for r in eval_results:
            entry = {k: v for k, v in r.items() if k != "record"}
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # Experiment log
    log_entry = {
        "experiment": experiment_name,
        "type": "tool_recall",
        "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "total_queries": total,
        "wall_time_s": round(t_total),
    }
    for bench_name in bench_list:
        br = [r for r in eval_results if r["benchmark"] == bench_name]
        if br:
            log_entry[f"{bench_name}_found"] = sum(1 for r in br if r["found"])
            log_entry[f"{bench_name}_total"] = len(br)
            log_entry[f"{bench_name}_recall"] = round(
                sum(1 for r in br if r["found"]) / len(br) * 100, 1
            )
    log_path = RESULTS_DIR / "experiment_log.jsonl"
    with open(log_path, "a") as f:
        f.write(json.dumps(log_entry) + "\n")

    print(f"\n  Raw results: {raw_path}")
    print(f"  Experiment log: {log_path}")
    print(f"{'='*60}")


def main(**kwargs: Any) -> None:
    run_eval(**kwargs)


if __name__ == "__main__":
    fire.Fire(main)
