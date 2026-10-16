import asyncio

from writer.base.llm_judge.related_work import enrich_with_related_work, extract_keywords_regex


def test_regex_keyword_extraction_returns_specific_terms():
    keywords = extract_keywords_regex(
        "Abstract\nWe introduce Retrieval Augmented Generation for LLM agents.\n"
        "Methods\nThe system uses Contrastive Learning and RAG."
    )

    assert "Retrieval Augmented Generation" in keywords
    assert "LLM" in keywords


def test_enrich_with_related_work_uses_injected_llm_keywords_and_searcher():
    async def keyword_extractor(paper_text: str) -> list[str]:
        assert "paper" in paper_text
        return ["Retrieval Augmented Generation", "LLM agents"]

    async def paper_searcher(keywords: list[str], max_results: int) -> list[dict]:
        assert keywords == ["Retrieval Augmented Generation", "LLM agents"]
        assert max_results == 4
        return [
            {
                "title": "Retrieval Augmented Generation for LLM Agents",
                "abstract": "Retrieval augmented generation improves LLM agents.",
                "arxiv_id": "2401.00001",
            }
        ]

    context = asyncio.run(
        enrich_with_related_work(
            "paper about Retrieval Augmented Generation and LLM agents",
            max_related=2,
            keyword_extractor=keyword_extractor,
            paper_searcher=paper_searcher,
        )
    )

    assert "Search keywords: Retrieval Augmented Generation, LLM agents" in context
    assert "Retrieval Augmented Generation for LLM Agents" in context
