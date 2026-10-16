import logging
import re
from datetime import date
from typing import Dict, List

logger = logging.getLogger(__name__)


async def extract_keywords_from_paper(text: str, max_keywords: int = 10) -> List[str]:
    """
    Extract key technical terms from paper for arXiv search.
    """
    abstract_match = re.search(r"(?i)abstract\s*\n(.*?)(?=\n\s*\d|##|\Z)", text, re.DOTALL)
    methods_match = re.search(r"(?i)(method|approach|model|architecture).*?\n(.*?)(?=\n\s*\d|##|\Z)", text, re.DOTALL)
    
    key_text = ""
    if abstract_match:
        key_text += abstract_match.group(1)[:1000]
    if methods_match:
        key_text += "\n" + methods_match.group(2)[:1000]
    
    if not key_text:
        key_text = text[:2000]
    
    patterns = [
        r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b',  # Multi-word capitalized
        r'\b[A-Z]{2,}\b',  # Acronyms
        r'\b[a-z]{4,}(?:tion|ness|ity|ance|ence)\b',  # Technical nouns
    ]
    
    terms = []
    for pattern in patterns:
        terms.extend(re.findall(pattern, key_text))
    
    terms = [t.strip() for t in terms if len(t.strip()) > 3]
    terms = list(dict.fromkeys(terms))
    
    stop_words = {
        "this", "that", "with", "from", "have", "been", "were", "are", "was",
        "using", "based", "show", "showing", "propose", "present", "introduce",
        "develop", "design", "learn", "train", "test", "evaluate", "method",
        "approach", "model", "paper", "work", "study", "result", "experiment"
    }
    
    filtered = [t for t in terms if t.lower() not in stop_words]
    
    return filtered[:max_keywords]


async def search_arxiv_papers(
    keywords: List[str],
    max_results: int = 5,
    date_range_years: int = 3,
) -> List[Dict]:
    """
    Search arXiv for papers related to given keywords.
    """
    import time
    start_time = time.time()

    try:
        import asyncio

        from academia_mcp.tools import arxiv_search

        terms = []
        for keyword in keywords[:5]:
            keyword = keyword.replace('"', "").strip()
            if not keyword:
                continue
            if " " in keyword:
                terms.append(f'all:"{keyword}"')
            else:
                terms.append(f"all:{keyword}")
        query = " OR ".join(terms)
        if not query:
            return []
        logger.info(f"Searching arXiv for query: {query}")

        current_year = date.today().year
        min_year = current_year - date_range_years if date_range_years else 0
        start_date = f"{min_year}-01-01" if min_year else None

        response = await asyncio.to_thread(
            arxiv_search,
            query=query,
            limit=max_results * 2,
            sort_by="submittedDate",
            sort_order="descending",
            include_abstracts=True,
            start_date=start_date,
        )

        papers = []
        for result in response.results:
            year_match = re.search(r"(\d{4})", result.published)
            year = int(year_match.group(1)) if year_match else 0

            if year and year < min_year:
                continue

            title_keywords = sum(1 for kw in keywords if kw.lower() in result.title.lower())
            similarity = title_keywords / len(keywords) if keywords else 0

            papers.append({
                "title": result.title,
                "abstract": result.abstract or "",
                "arxiv_id": result.id,
                "year": year,
                "similarity_score": similarity,
            })
            if len(papers) >= max_results:
                break
        
        elapsed = time.time() - start_time
        logger.info(f"arXiv search completed in {elapsed:.2f}s: found {len(papers)} papers")
        return papers
        
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"arXiv search failed after {elapsed:.2f}s: {e}")
        return []


async def find_highly_similar_papers(
    paper_text: str,
    candidate_papers: List[Dict],
    threshold: float = 0.5,
) -> List[Dict]:
    """
    Filter candidate papers to find highly similar ones.
    Uses simple cosine similarity on TF-IDF vectors.
    """
    import time
    start_time = time.time()

    target_words = set(re.findall(r'\b[a-zA-Z]{4,}\b', paper_text.lower()))
    
    similar_papers = []
    for paper in candidate_papers:
        candidate_words = set(re.findall(r'\b[a-zA-Z]{4,}\b', paper["abstract"].lower()))
        
        intersection = len(target_words & candidate_words)
        union = len(target_words | candidate_words)
        similarity = intersection / union if union > 0 else 0
        
        if similarity >= threshold:
            paper["similarity_score"] = similarity
            similar_papers.append(paper)
    
    similar_papers.sort(key=lambda x: x["similarity_score"], reverse=True)
    
    elapsed = time.time() - start_time
    logger.info(f"Similarity filtering completed in {elapsed:.3f}s: {len(similar_papers)}/{len(candidate_papers)} papers above threshold {threshold}")
    return similar_papers


async def enrich_with_related_work(
    paper_content: str,
    max_related: int = 3,
) -> str:
    """
    Full pipeline: extract keywords -> search arXiv -> format context.
    """
    import time
    start_total = time.time()

    keyword_start = time.time()
    keywords = await extract_keywords_from_paper(paper_content)
    keyword_elapsed = time.time() - keyword_start
    logger.info(f"Keyword extraction completed in {keyword_elapsed:.3f}s: {len(keywords)} keywords")
    if not keywords:
        logger.warning("No keywords extracted, skipping arXiv search")
        return "No key technical terms identified for novelty check."
    
    search_start = time.time()
    candidates = await search_arxiv_papers(
        keywords=keywords,
        max_results=max_related * 2,
        date_range_years=3,
    )
    search_elapsed = time.time() - search_start
    
    if not candidates:
        total_elapsed = time.time() - start_total
        logger.info(f"arXiv pipeline completed in {total_elapsed:.2f}s: no candidates found")
        return "No closely related recent work found on arXiv."
    
    filter_start = time.time()
    similar = await find_highly_similar_papers(paper_content, candidates, threshold=0.3)
    filter_elapsed = time.time() - filter_start
    
    if not similar:
        total_elapsed = time.time() - start_total
        logger.info(f"arXiv pipeline completed in {total_elapsed:.2f}s: no similar papers found")
        return "No highly similar papers found. This work appears novel."
    
    context_parts = [
        "# Related Recent Work (from arXiv)",
        "The following papers are most similar to this work based on technical content:\n"
    ]
    
    for i, paper in enumerate(similar[:max_related], 1):
        title = paper.get("title", "Unknown title")
        abstract = paper.get("abstract", "No abstract available")[:400]  # Truncate
        arxiv_id = paper.get("arxiv_id", "")
        year = paper.get("year", "")
        similarity = paper.get("similarity_score", 0)
        context_parts.append(f"## {i}. {title}")
        context_parts.append(f"**arXiv:{arxiv_id} ({year})**")
        context_parts.append(f"**Similarity:** {similarity:.2f}")
        context_parts.append(f"**Abstract:** {abstract}...\n")
    
    context_parts.append(
        "**Use this context to assess:**\n"
        "- Does this paper adequately differentiate from prior work?\n"
        "- Are the claimed novel contributions actually novel?\n"
        "- Is the related work section complete and up-to-date?"
    )
    
    total_elapsed = time.time() - start_total
    logger.info(
        f"arXiv pipeline completed in {total_elapsed:.2f}s: "
        f"keywords={keyword_elapsed:.3f}s, search={search_elapsed:.2f}s, filter={filter_elapsed:.3f}s"
    )

    return "\n\n".join(context_parts)

