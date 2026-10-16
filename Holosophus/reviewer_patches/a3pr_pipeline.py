"""A3PR Pipeline: 4-stage cascaded review (Tier 2, Item 4)."""

import json
import logging
import time
from typing import Dict, List, Optional

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from settings import settings
from section_parser import ParsedPaper
from arxiv_search import enrich_with_related_work
from models import (
    ExtendedReviewResponse,
    AnalysisJson,
    ArgumentJson,
    AspectItem,
    WEIGHTS,
    compute_overall_score,
    map_to_overall_score,
)

logger = logging.getLogger(__name__)


class AnalysisResult(BaseModel):
    """Structured extraction of paper facts (A3PR Stage 1)."""
    goals: List[str] = Field(default_factory=list)
    methods: List[str] = Field(default_factory=list)
    datasets: List[str] = Field(default_factory=list)
    claims: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    key_contributions: List[str] = Field(default_factory=list)
    
    citations: Dict[str, str] = Field(default_factory=dict)  # claim -> "Section 3.2, p.5"


ANALYSIS_PROMPT = """You are extracting factual information from a research paper.

# Task
Read the paper and extract the following information as structured JSON:

1. **goals**: What are the main research questions or objectives? (1-3 items)
2. **methods**: What techniques/approaches are used? (1-3 items)
3. **datasets**: What datasets/benchmarks are used for evaluation? (list all)
4. **claims**: What are the main claims made by the authors? (3-5 key claims)
5. **limitations**: What limitations do the authors acknowledge? (list all)
6. **key_contributions**: What do the authors claim as their main contributions? (1-3 items)

# Critical Requirements
- EVERY item MUST include a citation to the specific section/page where it appears
- Do NOT infer or hallucinate - only extract what is explicitly stated
- Use exact quotes when possible
- If something is unclear or missing, note it in the "limitations" field

# Output Format
Return JSON only:
{
    "goals": [{"text": "...", "citation": "Abstract, p.1"}],
    "methods": [{"text": "...", "citation": "Section 3, p.3"}],
    "datasets": [{"name": "...", "citation": "Section 4.1, p.5"}],
    "claims": [{"text": "...", "citation": "Section 5.2, p.7"}],
    "limitations": [{"text": "...", "citation": "Section 6, p.9"}],
    "key_contributions": [{"text": "...", "citation": "Introduction, p.2"}]
}
"""


async def run_analysis_stage(
    parsed_paper: ParsedPaper,
    model_name: Optional[str] = None,
) -> AnalysisResult:
    """
    Stage 1: Extract structured facts from paper.
    """
    model_name = model_name or settings.REVIEW_MODEL_NAME
    key = settings.OPENROUTER_API_KEY
    base_url = settings.BASE_URL
    
    client = AsyncOpenAI(base_url=base_url, api_key=key)
    
    paper_text = parsed_paper.to_structured_prompt()
    
    messages = [
        {"role": "system", "content": "You are a meticulous fact extractor. Only extract what is explicitly stated."},
        {"role": "user", "content": f"{ANALYSIS_PROMPT}\n\n# Paper Text\n\n{paper_text}"}
    ]
    
    try:
        response = await client.chat.completions.parse(
            model=model_name,
            messages=messages,
            response_format=AnalysisResult,
            max_completion_tokens=2000,
            temperature=0.0,
        )
        
        result = response.choices[0].message.parsed
        assert result
        
        logger.info(f"Analysis completed: {len(result.goals)} goals, {len(result.methods)} methods, {len(result.claims)} claims")
        return result
        
    except Exception as e:
        logger.error(f"Analysis stage failed: {e}")
        return AnalysisResult(
            goals=[{"text": "Unable to extract", "citation": "N/A"}],
            methods=[{"text": "Unable to extract", "citation": "N/A"}],
            datasets=[],
            claims=[],
            limitations=[],
            key_contributions=[],
        )



class ArgumentResult(BaseModel):
    """Arguments for/against acceptance (A3PR Stage 2)."""
    pros: List[str] = Field(default_factory=list)
    cons: List[str] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)
    evidence_citations: Dict[str, str] = Field(default_factory=dict)


ARGUMENT_PROMPT = """You are formulating arguments for and against accepting this paper.

# Task
Based on the extracted facts, formulate clear arguments:

1. **pros**: Strong reasons to accept this paper (3-5 arguments)
2. **cons**: Strong reasons to reject this paper (3-5 arguments)
3. **open_questions**: Questions that could change your decision (2-4 questions)

# Critical Requirements
- EVERY argument MUST cite specific evidence from the AnalysisResult
- Focus on technical merit, not personal preference
- Be specific: "Method X has Y limitation" not "Method is weak"
- Consider novelty in context of related work (if provided)

# Output Format
Return JSON only:
{
    "pros": [
        {"argument": "...", "evidence": "AnalysisResult.claims[0], Section 3.2"}
    ],
    "cons": [
        {"argument": "...", "evidence": "AnalysisResult.limitations[1], Section 6"}
    ],
    "open_questions": ["Question 1?", "Question 2?"],
    "evidence_citations": {"Argument text": "Source location"}
}
"""


async def run_argument_stage(
    analysis: AnalysisResult,
    related_work_context: Optional[str] = None,
    model_name: Optional[str] = None,
) -> ArgumentResult:
    """
    Stage 2: Formulate arguments for/against acceptance.
    """
    model_name = model_name or settings.REVIEW_MODEL_NAME
    key = settings.OPENROUTER_API_KEY
    base_url = settings.BASE_URL
    
    client = AsyncOpenAI(base_url=base_url, api_key=key)
    
    analysis_json = analysis.model_dump()
    
    prompt_content = f"{ARGUMENT_PROMPT}\n\n# Analysis Result\n\n{json.dumps(analysis_json, indent=2)}"
    
    if related_work_context:
        prompt_content += f"\n\n# Related Work Context\n\n{related_work_context}"
    
    messages = [
        {"role": "system", "content": "You are a balanced reviewer. Consider both strengths and weaknesses."},
        {"role": "user", "content": prompt_content}
    ]
    
    try:
        response = await client.chat.completions.parse(
            model=model_name,
            messages=messages,
            response_format=ArgumentResult,
            max_completion_tokens=2000,
            temperature=0.1,
        )
        
        result = response.choices[0].message.parsed
        assert result
        
        logger.info(f"Argument completed: {len(result.pros)} pros, {len(result.cons)} cons, {len(result.open_questions)} questions")
        return result
        
    except Exception as e:
        logger.error(f"Argument stage failed: {e}")
        return ArgumentResult(
            pros=["Unable to formulate arguments"],
            cons=["Unable to formulate arguments"],
            open_questions=["Unable to formulate questions"],
        )

class AssessmentResult(BaseModel):
    """Scores for 7 criteria (A3PR Stage 3)."""
    originality: AspectItem = Field(default_factory=AspectItem)
    importance: AspectItem = Field(default_factory=AspectItem)
    claim_support: AspectItem = Field(default_factory=AspectItem)
    experimental_soundness: AspectItem = Field(default_factory=AspectItem)
    clarity: AspectItem = Field(default_factory=AspectItem)
    community_value: AspectItem = Field(default_factory=AspectItem)
    contextualization: AspectItem = Field(default_factory=AspectItem)


ASSESSMENT_PROMPT = """You are assigning scores to 7 criteria based on evidence.

# Criteria (score 1-4: poor/fair/good/excellent)

1. **Originality**: Novelty of ideas, methods, or insights
2. **Importance**: Significance of the research question
3. **Claim Support**: Are claims backed by evidence (theory/experiments)?
4. **Experimental Soundness**: Rigor of experimental design and analysis
5. **Clarity**: Quality of writing and organization
6. **Community Value**: Usefulness to the research community
7. **Contextualization**: How well positioned vs prior work

# Critical Requirements
- EVERY score MUST be justified with specific evidence
- Cite AnalysisResult items or ArgumentResult points
- Do NOT give the same score for all criteria without strong justification
- Be strict: excellence is rare

# Output Format
Return JSON only:
{
    "originality": {"score": 3, "reasoning": "...", "strengths": [], "weaknesses": []},
    "importance": {"score": 4, "reasoning": "...", "strengths": [], "weaknesses": []},
    ...
}
"""


async def run_assessment_stage(
    analysis: AnalysisResult,
    argument: ArgumentResult,
    model_name: Optional[str] = None,
) -> AssessmentResult:
    """
    Stage 3: Assign scores to 7 criteria.
    """
    model_name = model_name or settings.REVIEW_MODEL_NAME
    key = settings.OPENROUTER_API_KEY
    base_url = settings.BASE_URL
    
    client = AsyncOpenAI(base_url=base_url, api_key=key)
    
    context = {
        "analysis": analysis.model_dump(),
        "argument": argument.model_dump(),
    }
    
    messages = [
        {"role": "system", "content": "You are a rigorous evaluator. Scores must reflect evidence."},
        {"role": "user", "content": f"{ASSESSMENT_PROMPT}\n\n# Context\n\n{json.dumps(context, indent=2)}"}
    ]
    
    try:
        response = await client.chat.completions.parse(
            model=model_name,
            messages=messages,
            response_format=AssessmentResult,
            max_completion_tokens=3000,
            temperature=0.0,
        )
        
        result = response.choices[0].message.parsed
        assert result
        
        logger.info(f"Assessment completed: scores assigned for 7 criteria")
        return result
        
    except Exception as e:
        logger.error(f"Assessment stage failed: {e}")
        return AssessmentResult()


def assemble_final_review(
    analysis: AnalysisResult,
    argument: ArgumentResult,
    assessment: AssessmentResult,
    paper_summary: str = "",
) -> ExtendedReviewResponse:
    """
    Stage 4: Assemble final review programmatically.
    """
    scores = {
        "originality": assessment.originality.score,
        "importance": assessment.importance.score,
        "claim_support": assessment.claim_support.score,
        "experimental_soundness": assessment.experimental_soundness.score,
        "clarity": assessment.clarity.score,
        "community_value": assessment.community_value.score,
        "contextualization": assessment.contextualization.score,
    }

    overall_score_raw = compute_overall_score(scores)
    overall_score_mapped = map_to_overall_score(overall_score_raw)

    overall_reasoning_parts = []
    for criterion, aspect in [
        ("Originality", assessment.originality),
        ("Importance", assessment.importance),
        ("Claim Support", assessment.claim_support),
        ("Experimental Soundness", assessment.experimental_soundness),
    ]:
        if aspect.reasoning:
            overall_reasoning_parts.append(f"{criterion}: {aspect.reasoning}")

    overall_reasoning = "\n".join(overall_reasoning_parts) or "Weighted average of 7 criteria."

    analysis_json = AnalysisJson(
        goals=[g.get("text", "") if isinstance(g, dict) else str(g) for g in analysis.goals] if analysis.goals else [],
        methods=[m.get("text", "") if isinstance(m, dict) else str(m) for m in analysis.methods] if analysis.methods else [],
        datasets=list(analysis.datasets),
        claims=[c.get("text", "") if isinstance(c, dict) else str(c) for c in analysis.claims] if analysis.claims else [],
        limitations=[l.get("text", "") if isinstance(l, dict) else str(l) for l in analysis.limitations] if analysis.limitations else [],
        key_contributions=[kc.get("text", "") if isinstance(kc, dict) else str(kc) for kc in analysis.key_contributions] if analysis.key_contributions else [],
    )

    argument_json = ArgumentJson(
        analysis_summary=json.dumps(analysis.model_dump(), indent=2)[:500],
        argument_structure=[f"PRO: {p}" for p in argument.pros] + [f"CON: {c}" for c in argument.cons],
        assessment_summary=json.dumps(assessment.model_dump(), indent=2)[:500],
        key_findings=argument.open_questions[:5],
    )

    review = ExtendedReviewResponse(
        summary=paper_summary or (analysis.key_contributions[0] if analysis.key_contributions else ""),
        originality=assessment.originality,
        importance=assessment.importance,
        claim_support=assessment.claim_support,
        experimental_soundness=assessment.experimental_soundness,
        clarity=assessment.clarity,
        community_value=assessment.community_value,
        contextualization=assessment.contextualization,
        questions=argument.open_questions[:5],
        limitations=[lim.get("text", "") if isinstance(lim, dict) else str(lim) for lim in (analysis.limitations or [])][:5],
        overall_score_raw=overall_score_raw,
        overall={
            "reasoning": overall_reasoning,
            "score": overall_score_mapped,
        },
        confidence={
            "reasoning": "Based on comprehensive 4-stage analysis with evidence citations.",
            "score": 4,
        },
        analysis_json=analysis_json,
        argument_json=argument_json,
    )

    logger.info(f"Assembled final review: overall={overall_score_mapped}/10 (from raw {overall_score_raw:.2f}/4)")
    return review



async def run_a3pr_review(
    parsed_paper: ParsedPaper,
    use_arxiv_search: bool = True,
    model_name: Optional[str] = None,
) -> ExtendedReviewResponse:
    """
    Full A3PR pipeline: Analysis -> Argument -> Assessment -> Polish.
    """
    start_time = time.time()
    logger.info("Starting A3PR pipeline...")
    
    arxiv_elapsed = 0.0
    related_work_context = None
    if use_arxiv_search:
        try:
            arxiv_start = time.time()
            logger.info("Searching arXiv for related work...")
            related_work_context = await enrich_with_related_work(
                paper_content=parsed_paper.full_text,
                max_related=3,
            )
            arxiv_elapsed = time.time() - arxiv_start
            logger.info(f"arXiv search completed in {arxiv_elapsed:.2f}s")
        except Exception as e:
            arxiv_elapsed = time.time() - arxiv_start if 'arxiv_start' in locals() else 0.0
            logger.warning(f"arXiv search failed after {arxiv_elapsed:.2f}s: {e}")
    
    stage_times = []

    logger.info("Stage 1/4: Analysis...")
    stage_start = time.time()
    analysis = await run_analysis_stage(parsed_paper, model_name)
    stage_times.append(("Analysis", time.time() - stage_start))
    logger.info(f"Stage 1/4 (Analysis) completed in {stage_times[-1][1]:.2f}s")
    
    logger.info("Stage 2/4: Argument...")
    stage_start = time.time()
    argument = await run_argument_stage(analysis, related_work_context, model_name)
    stage_times.append(("Argument", time.time() - stage_start))
    logger.info(f"Stage 2/4 (Argument) completed in {stage_times[-1][1]:.2f}s")
    
    logger.info("Stage 3/4: Assessment...")
    stage_start = time.time()
    assessment = await run_assessment_stage(analysis, argument, model_name)
    stage_times.append(("Assessment", time.time() - stage_start))
    logger.info(f"Stage 3/4 (Assessment) completed in {stage_times[-1][1]:.2f}s")
    
    logger.info("Stage 4/4: Polish...")
    stage_start = time.time()
    final_review = assemble_final_review(analysis, argument, assessment)
    stage_times.append(("Polish", time.time() - stage_start))
    logger.info(f"Stage 4/4 (Polish) completed in {stage_times[-1][1]:.3f}s")
    
    elapsed = time.time() - start_time
    total_stage_time = sum(t[1] for t in stage_times)

    logger.info("A3PR PIPELINE SUMMARY")
    for stage_name, stage_time in stage_times:
        pct = (stage_time / total_stage_time * 100) if total_stage_time > 0 else 0
        logger.info(f"  {stage_name:12}: {stage_time:6.2f}s ({pct:5.1f}%)")
    if use_arxiv_search:
        logger.info(f"  arXiv search: {arxiv_elapsed:6.2f}s")
    logger.info(f"  TOTAL: {elapsed:.1f}s (pipeline: {total_stage_time:.1f}s, overhead: {elapsed - total_stage_time:.1f}s)")
    return final_review
