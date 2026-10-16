"""Paper type routing for grouped rubric selection."""

from __future__ import annotations

import os
import re
from typing import Optional

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from .definitions import PaperType
from .loader import normalize_paper_type


class PaperTypeClassification(BaseModel):
    paper_type: PaperType
    reasoning: str = ""
    confidence: str = Field(default="medium")


PAPER_TYPE_PROMPT = """Classify the scientific paper type for rubric selection.

Allowed paper_type values:
- ml_experimental: machine learning paper with experiments, datasets, models, or benchmarks
- llm_system: LLM system, LLM-as-a-judge, prompt, agent, or language-model evaluation paper
- empirical: empirical non-ML paper
- theoretical: mostly theory/proofs/formal argument
- survey: survey or position paper
- mixed: mixes several types; use this when ML/LLM is central but not exclusive
- other: none of the above or unclear

Return JSON only. Do not review the paper.
"""


ML_LLM_PATTERN = re.compile(
    r"\b("
    r"machine learning|deep learning|neural|transformer|language model|llm|"
    r"benchmark|dataset|train|validation|test split|hyperparameter|checkpoint|"
    r"prompt|decoder|fine-tun|inference|agent|llm-as-a-judge"
    r")\b",
    re.IGNORECASE,
)
EMPIRICAL_PATTERN = re.compile(
    r"\b("
    r"experiment|empirical|participants|survey|sample size|statistical test|"
    r"regression|intervention|control group|measurement|observational"
    r")\b",
    re.IGNORECASE,
)
THEORY_PATTERN = re.compile(r"\b(theorem|lemma|proof|proposition|formal guarantee)\b", re.IGNORECASE)
SURVEY_PATTERN = re.compile(r"\b(survey|systematic review|literature review|taxonomy)\b", re.IGNORECASE)


def infer_paper_type_regex(paper_text: str) -> PaperType:
    sample = paper_text[:50000]
    if SURVEY_PATTERN.search(sample):
        return "survey"
    if ML_LLM_PATTERN.search(sample):
        if re.search(r"\b(llm|language model|prompt|agent|llm-as-a-judge)\b", sample, re.IGNORECASE):
            return "llm_system"
        return "ml_experimental"
    if EMPIRICAL_PATTERN.search(sample):
        return "empirical"
    if THEORY_PATTERN.search(sample):
        return "theoretical"
    return "other"


async def infer_paper_type_openrouter(
    paper_text: str,
    *,
    model_name: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> PaperType:
    key = api_key or os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is required for LLM paper type classification")

    client = AsyncOpenAI(
        base_url=base_url or os.getenv("BASE_URL", "https://openrouter.ai/api/v1"),
        api_key=key,
    )
    response = await client.chat.completions.parse(
        model=model_name or os.getenv("REVIEW_MODEL_NAME", "google/gemini-3.5-flash"),
        messages=[
            {"role": "system", "content": "You classify scientific papers for rubric routing."},
            {"role": "user", "content": f"{PAPER_TYPE_PROMPT}\n\n# Paper\n\n{paper_text[:30000]}"},
        ],
        response_format=PaperTypeClassification,
        max_completion_tokens=800,
        temperature=0.0,
    )
    parsed = response.choices[0].message.parsed
    if not parsed:
        return "other"
    return parsed.paper_type


async def resolve_paper_type(
    paper_text: str,
    *,
    paper_type: str | None = None,
    use_llm_classifier: bool = False,
    model_name: Optional[str] = None,
) -> PaperType:
    explicit = normalize_paper_type(paper_type)
    if paper_type and explicit != "other":
        return explicit

    regex_type = infer_paper_type_regex(paper_text)
    if not use_llm_classifier:
        return regex_type

    try:
        return await infer_paper_type_openrouter(paper_text, model_name=model_name)
    except Exception:
        return regex_type
