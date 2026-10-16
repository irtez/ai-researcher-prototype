"""Context length control: truncate or summarize long papers."""

import logging
import time
from typing import Optional

from openai import AsyncOpenAI

from academia_mcp.settings import settings

logger = logging.getLogger(__name__)


CHARS_PER_TOKEN = 4

DEFAULT_MAX_PAPER_TOKENS = 50000
DEFAULT_MAX_TEXT_TOKENS = 40000
DEFAULT_MAX_IMAGE_TOKENS = 10000


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def truncate_text(
    text: str,
    max_tokens: int = DEFAULT_MAX_TEXT_TOKENS,
    keep_beginning: bool = True,
    keep_abstract: bool = True,
) -> str:
    """
    Truncate text to fit within token limit.
    """
    start_time = time.time()
    current_tokens = estimate_tokens(text)
    
    if current_tokens <= max_tokens:
        logger.info(f"No truncation needed: {current_tokens} tokens <= {max_tokens}")
        return text
    
    sections = text.split("\n\n")
    
    abstract = ""
    if keep_abstract and sections:
        abstract = sections[0]
        sections = sections[1:]
        abstract_tokens = estimate_tokens(abstract)
        remaining_tokens = max_tokens - abstract_tokens
    else:
        remaining_tokens = max_tokens
    
    logger.info(f"Truncating from {current_tokens} to {max_tokens} tokens")
    
    if keep_beginning:
        selected_sections = []
        current_count = 0
        for section in sections:
            section_tokens = estimate_tokens(section)
            if current_count + section_tokens <= remaining_tokens:
                selected_sections.append(section)
                current_count += section_tokens
            else:
                logger.debug(f"Stopping at section with {section_tokens} tokens")
                break
        result = "\n\n".join([abstract] + selected_sections) if abstract else "\n\n".join(selected_sections)
        if result is None:
            raise RuntimeError("Truncation resulted in empty text")
        
    else:
        selected_sections = []
        current_count = 0
        for section in reversed(sections):
            section_tokens = estimate_tokens(section)
            if current_count + section_tokens <= remaining_tokens:
                selected_sections.insert(0, section)
                current_count += section_tokens
            else:
                break
        
        result = "\n\n".join(selected_sections)
        if abstract:
            result = abstract + "\n\n[... truncated ...]\n\n" + result
    print(f"Truncated text: {estimate_tokens(result)} tokens")
    final_tokens = estimate_tokens(result)
    removed_tokens = current_tokens - final_tokens
    result += f"\n\n[Note: Truncated {removed_tokens} tokens from original {current_tokens} tokens]"
    
    elapsed = time.time() - start_time
    logger.info(f"Truncation completed in {elapsed:.3f}s: {current_tokens} → {final_tokens} tokens (removed {removed_tokens})")
    
    if result is None:
        raise RuntimeError("Truncation resulted in empty text")
    return result


async def summarize_paper(
    paper_text: str,
    max_tokens: int = 5000,
    model_name: Optional[str] = None,
) -> str:
    """
    Summarize a long paper to fit within token limits.
    """
    start_time = time.time()
    model_name = model_name or settings.REVIEW_MODEL_NAME
    key = settings.OPENROUTER_API_KEY
    assert key, "Please set OPENROUTER_API_KEY in environment"
    base_url = settings.BASE_URL
    
    client = AsyncOpenAI(base_url=base_url, api_key=key)
    
    max_input_chars = 100000
    if len(paper_text) > max_input_chars:
        logger.warning(f"Input too long ({len(paper_text)} chars), truncating to {max_input_chars}")
        paper_text = paper_text[:max_input_chars]
    
    input_tokens = estimate_tokens(paper_text)
    messages = [
        {
            "role": "system",
            "content": """You are an expert research assistant summarizing academic papers.
Your goal is to create a concise summary that preserves:
- Main research question and contributions
- Key methods and techniques
- Experimental setup and datasets
- Main results and findings
- Limitations acknowledged by authors

Maintain technical accuracy and cite specific sections when possible."""
        },
        {
            "role": "user",
            "content": f"Summarize the following paper in approximately {max_tokens // 2} words (preserving key technical details):\n\n{paper_text}"
        }
    ]
    
    try:
        response = await client.chat.completions.create(
            model=model_name,
            messages=messages,
            max_completion_tokens=max_tokens,
            temperature=0.3,
        )
        
        summary = response.choices[0].message.content
        assert summary
        
        output_tokens = estimate_tokens(summary)
        elapsed = time.time() - start_time
        token_savings = input_tokens - output_tokens
        savings_pct = (token_savings / input_tokens * 100) if input_tokens > 0 else 0

        logger.info(
            f"Summarization completed in {elapsed:.2f}s: "
            f"{input_tokens} → {output_tokens} tokens (saved {token_savings} tokens, {savings_pct:.1f}%)"
        )
        return summary
        
    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"Summarization failed after {elapsed:.2f}s: {e}")


def prepare_paper_for_review(
    paper_text: str,
    use_images: bool = True,
    max_total_tokens: int = DEFAULT_MAX_PAPER_TOKENS,
    strategy: str = "truncate", 
) -> tuple[str, str]:
    """
    Prepare paper text for review, applying context length control.
    """
    start_time = time.time()
    current_tokens = estimate_tokens(paper_text)
    
    text_budget = max_total_tokens
    if use_images:
        text_budget -= DEFAULT_MAX_IMAGE_TOKENS
        logger.info(f"Reserved {DEFAULT_MAX_IMAGE_TOKENS} tokens for images")
    
    if current_tokens <= text_budget:
        logger.info(f"Paper fits within budget: {current_tokens} <= {text_budget}")
        return paper_text, "none"
    
    if strategy == "truncate":
        prepared = truncate_text(paper_text, text_budget)
        elapsed = time.time() - start_time
        logger.info(f"Context preparation completed in {elapsed:.3f}s (strategy: truncate)")
        return prepared, "truncate"
    
    elif strategy == "hybrid":
        prepared = truncate_text(paper_text, text_budget * 1.2)
        if estimate_tokens(prepared) > text_budget:
            logger.warning("Hybrid strategy requires async summarization")
            prepared = truncate_text(paper_text, text_budget)
        elapsed = time.time() - start_time
        logger.info(f"Context preparation completed in {elapsed:.3f}s (strategy: hybrid)")
        return prepared, "hybrid"
    
    else:
        logger.warning(f"Unknown strategy '{strategy}', falling back to truncation")
        prepared = truncate_text(paper_text, text_budget)
        elapsed = time.time() - start_time
        return prepared, "truncate"


async def prepare_paper_for_review_async(
    paper_text: str,
    use_images: bool = True,
    max_total_tokens: int = DEFAULT_MAX_PAPER_TOKENS,
    strategy: str = "summarize",
) -> tuple[str, str]:
    start_time = time.time()
    current_tokens = estimate_tokens(paper_text)

    text_budget = max_total_tokens
    if use_images:
        text_budget -= DEFAULT_MAX_IMAGE_TOKENS

    if current_tokens <= text_budget:
        elapsed = time.time() - start_time
        logger.info(f"Context preparation completed in {elapsed:.3f}s: no processing needed ({current_tokens} tokens)")
        return paper_text, "none"

    if strategy == "summarize":
        prepared = await summarize_paper(paper_text, text_budget)
        elapsed = time.time() - start_time
        logger.info(f"Context preparation completed in {elapsed:.2f}s (strategy: summarize)")
        return prepared, "summarize"

    elif strategy == "hybrid":
        truncated = truncate_text(paper_text, text_budget * 1.5)
        if estimate_tokens(truncated) > text_budget:
            prepared = await summarize_paper(truncated, text_budget)
            elapsed = time.time() - start_time
            logger.info(f"Context preparation completed in {elapsed:.2f}s (strategy: hybrid)")
            return prepared, "hybrid"
        elapsed = time.time() - start_time
        logger.info(f"Context preparation completed in {elapsed:.3f}s (strategy: hybrid)")
        return truncated, "hybrid"

    else:
        prepared = truncate_text(paper_text, text_budget)
        elapsed = time.time() - start_time
        logger.info(f"Context preparation completed in {elapsed:.3f}s (strategy: truncate)")
        return prepared, "truncate"