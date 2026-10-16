# Based on
# https://arxiv.org/abs/2504.12976
# https://web.stanford.edu/class/cs197c/slides/02-literature-search.pdf

import json
import random
from typing import Any, Dict, List, Literal, Optional

from datasets import load_dataset  # type: ignore
from pydantic import BaseModel, Field, field_validator

from academia_mcp.llm import ChatMessage, llm_acall_structured
from academia_mcp.settings import settings
from academia_mcp.tools.arxiv_download import arxiv_download
from academia_mcp.utils import encode_prompt


class ProposalDataset:
    dataset: Optional[List[Any]] = None

    @classmethod
    def get_dataset(cls) -> List[Any]:
        if cls.dataset is None:
            cls.dataset = list(load_dataset("UniverseTBD/hypogen-dr1")["train"])
        return cls.dataset


EXTRACT_PROMPT = """
You extract a Bit-Flip-Spark triple from a scientific abstract or paper excerpt.
The goal is not to summarize the paper. The goal is to identify the precise
assumption the work overturns and the mechanism by which it overturns it.

## Definitions

- Bit: the status quo, assumption, design choice, or bottleneck the work most
  directly challenges. It must be specific and falsifiable: a reviewer could
  reasonably argue for or against it.
- Flip: the counter-position introduced by the work, including the concrete
  mechanism that makes the counter-position plausible. A result without a
  mechanism is not enough.
- Spark: a 4-6 word handle for the inversion.

## Extraction checklist

Think internally before writing JSON:
1. Identify the main performance, scaling, data, architecture, optimization, or
   evaluation claim in the abstract.
2. Ask what prior assumption would have made that claim surprising.
3. Separate the central mechanism from side effects, application domain, and
   benchmark details.
4. If the abstract supports several possible Bits, choose the one that best
   explains the paper's named method and reported evidence.
5. Do not infer missing mechanisms. If the text is thin, make the Bit/Flip
   narrower rather than inventing unsupported claims.

## Calibration examples

### Strong Bit
Sequence models are commonly trained to predict each token only from preceding
tokens, which makes the representation at a position blind to right-side context.
This constraint limits tasks whose labels depend on both preceding and following
words, such as coreference or word sense.

### Strong Flip
The representation is instead trained with a masked-token objective inside a
bidirectional encoder, so every unmasked position can attend to both sides of the
sentence. Hiding the target token prevents answer leakage while preserving
bidirectional conditioning.

### Weak Bit to avoid
Language understanding is hard.

### Weak Flip to avoid
Use a better model to improve language understanding.

## Abstract or excerpt

{{abstract}}

## Output rules

- Bit and Flip must each be at least two self-contained sentences.
- The Bit must name both the status quo and the limitation it creates.
- The Flip must name both the new approach and its enabling mechanism.
- Do not cite authors or the paper title. Do not use first person.
- Do not include markdown, commentary, or scratch work.

Return exactly one JSON object:
{
    "bit": "Status quo plus limitation, at least two sentences.",
    "flip": "New approach plus enabling mechanism, at least two sentences.",
    "spark": "4-6 word summary"
}
"""

SYSTEM_IMPROVEMENT_PROMPT = """
You are a senior ML/AI research scientist designing proposals for top-tier
venues. Your proposals must be simultaneously:

- Novel: they invert a real assumption, not merely add scale, data, or a common
  module.
- Grounded: every substantive claim is traceable to the supplied Bit, references,
  literature notes, or broadly known public benchmarks.
- Mechanistic: the proposal names the algorithm, architecture, training signal,
  data construction, or evaluation protocol that creates the effect.
- Feasible: the core experiment can be run by one strong PhD student in 4-8
  weeks using public data and a single 8xA100 node or smaller.
- Falsifiable: the proposal predicts an outcome that a concrete experiment could
  disprove.

Hard constraints:
- Do not fabricate papers, authors, citations, datasets, baselines, or numerical
  results.
- Write in English only. Do not mix languages, scripts, transliterations, or
  stray non-English fragments in any field.
- Do not use vague verbs like "enhance", "leverage", "improve", or "optimize"
  unless the mechanism is named in the same sentence.
- Do not claim completed experimental results. Proposals may state expected
  outcomes, success thresholds, or hypotheses, but not "experiments show",
  "we demonstrate", or exact improvements as if already measured.
- Prefer a surprising but checkable mechanism over a grand idea with no decisive
  experiment.
- If the supplied context is weak, narrow the claim and design a smaller
  falsification experiment instead of inventing support.

Before finalizing each proposal, silently reject it if it is only a known
technique with new packaging, if its main experiment lacks a named dataset or
baseline, or if the central hypothesis could not be falsified by the listed
experiments.

Return outputs that satisfy the requested JSON schema exactly. Do not add fields
that are not requested by the user prompt.
"""

IMPROVEMENT_PROMPT = """
Produce {{num_proposals}} research proposals that flip the supplied Bit.

## Bit to flip

{{bit}}

{% if additional_context %}
## Grounding context

{{additional_context}}

Use this context as the fixed literature packet for this eval. You may use
widely-known public benchmarks, but do not invent paper titles, authors,
citations, datasets, baselines, or numerical results.
{% endif %}

{% if literature_summary %}
## Literature summary

{{literature_summary}}
{% endif %}

{% if examples %}
## Format examples only

{% for example in examples %}
Example {{loop.index}}:
- Bit: {{example["bit"]}}
- Flip: {{example["flip"]}}
- Spark: {{example["spark"]}}
{% endfor %}

Use these examples only to understand the Bit/Flip/Spark shape. Do not reuse
their content or level of specificity.
{% endif %}

## Internal generation procedure

For each proposal, reason internally through this procedure before writing JSON:

1. Decompose the Bit into 2-4 attackable sub-claims.
2. Choose a different sub-claim for each proposal so the final set is diverse.
3. For the chosen sub-claim, write the inversion in one sentence: "If the field
   assumes X, test whether Y works because Z."
4. Name the mechanism at implementation level: model component, training signal,
   data transformation, search procedure, objective, or evaluation protocol.
5. Design the smallest decisive experiment: named dataset/benchmark, named
   baseline, primary metric, ablation isolating the mechanism, and failure
   threshold.
6. Reject the idea if it is only a standard method pasted onto the topic, if it
   needs unavailable data/compute, or if no experiment could disprove it.

## Proposal quality requirements

Each proposal must satisfy all requirements:

- All text must be English. Do not include Chinese, Cyrillic, Arabic, Japanese,
  Korean, mojibake, random symbols, or mixed-language fragments.
- Do not report completed results or precise measured improvements. Use phrases
  like "we will test whether", "success is defined as", and "the hypothesis is"
  instead of "experiments show" or "achieves 30%".
- `flip` contains at least two sentences and names the exact mechanism.
- `abstract` is conference-style: motivation, method, evaluation plan, expected
  result, and significance. It must not claim results already happened.
- `experiments` contains at least three strings:
  1. a main experiment with dataset, baseline, metric, and success threshold;
  2. an ablation that isolates the proposed mechanism;
  3. a stress test or negative-control experiment.
- `risks_and_limitations` contains at least two strings, including one concrete
  falsification criterion specific to the proposal.
- The {{num_proposals}} proposals must not be near-duplicates.
- Return exactly {{num_proposals}} proposal objects unless the tool schema
  enforces fewer. Each object must be a JSON object, not a string.
- The only allowed proposal keys are `proposal_id`, `flip`, `spark`,
  `abstract`, `experiments`, and `risks_and_limitations`.

## Output

Return only valid JSON. No prose, no markdown fences.

{
    "proposals": [
        {
            "proposal_id": 0,
            "flip": "At least two sentences. Specific mechanism, not aspiration.",
            "spark": "4-6 word summary",
            "abstract": "Conference-format abstract of about 180-260 words. Motivation, method, evaluation plan, expected result, significance.",
            "experiments": [
                "Main experiment: dataset + baseline + metric + success threshold.",
                "Ablation: component removed or changed + expected diagnostic outcome.",
                "Stress test or negative control: dataset/setting + metric + failure signal."
            ],
            "risks_and_limitations": [
                "Specific failure mode.",
                "Falsification criterion: if concrete observation occurs, reject the hypothesis."
            ]
        }
    ]
}

"""


SCORE_SYSTEM_PROMPT = """You are an expert reviewer for a top-tier ML/AI conference (NeurIPS, ICML, ICLR).
You evaluate research proposals with high standards. Be critical but constructive.

Scoring discipline:
- Most proposals should score 2-3 overall. Reserve 4+ for genuinely strong, publishable ideas.
- A score of 1 indicates a fundamentally flawed or trivial proposal.
- Do not let strengths in one area compensate for critical weaknesses in another.
- Evaluate each criterion independently before forming an overall judgment."""

SCORE_PROMPT = """You are given research proposals to evaluate.

{% for proposal in proposals %}
## Proposal {{loop.index0}}
{{proposal}}

{% endfor %}

## Evaluation rubric

**Novelty** (1-4):
1 — Straightforward application of known methods. No new insight.
2 — Combines existing ideas in a somewhat new way, or applies known methods to a new domain.
3 — Meaningfully new perspective or method not clearly present in prior work.
4 — Fundamentally new paradigm or technique that could shift the research direction.

**Clarity** (1-4):
1 — Vague, contradictory, or missing key details.
2 — Main idea understandable but important details unclear.
3 — Well-written with clear problem, method, experiments. Minor gaps.
4 — Exceptionally clear. Could be submitted as-is.

**Significance** (1-4):
1 — Trivial or already-solved problem. Minimal impact.
2 — Real problem but limited to narrow subfield.
3 — Could meaningfully advance a research area.
4 — Broad impact across fields or fundamentally change practice.

**Feasibility** (1-4):
1 — Requires unavailable data/compute/methods. Underspecified.
2 — Achievable but significant challenges. Some steps unclear.
3 — Clearly achievable. Experiments well-specified with named datasets/metrics.
4 — Straightforward. All datasets, baselines, metrics explicitly named.

**Soundness** (1-4):
1 — Fundamental flaws or unsupported assumptions.
2 — Reasonable but notable gaps in reasoning or design.
3 — Solid methodology, well-justified. Minor concerns.
4 — Rigorous. Design comprehensively addresses hypothesis.

**Overall** (1-5):
1 — Reject: fundamentally flawed or trivial.
2 — Weak reject: interesting direction but major issues.
3 — Borderline: has merit, needs significant revision.
4 — Accept: clear contribution, well-executed.
5 — Strong accept: top quality, ready for publication.

For each proposal provide per-criterion reasoning BEFORE scores, >=2 strengths, >=2 weaknesses, 2-3 improvement suggestions.

Return JSON:
{
    "proposals": [
        {"proposal_id": 0, "spark": "...",
         "novelty_reasoning": "...", "novelty": 2,
         "clarity_reasoning": "...", "clarity": 3,
         "significance_reasoning": "...", "significance": 2,
         "feasibility_reasoning": "...", "feasibility": 3,
         "soundness_reasoning": "...", "soundness": 2,
         "strengths": ["..."], "weaknesses": ["..."],
         "improvement_suggestions": ["..."],
         "overall_reasoning": "...", "overall": 3}, ...
    ]
}
"""


CHEMISTRY_SYSTEM_PROMPT = """You design chemistry research using supplied molecular or laboratory data and literature.
Ground proposals in identified sources, available tools and feasible local calculations.
Distinguish measurements, calculated descriptors, heuristic estimates and model predictions.
Do not invent evidence, sources, completed results or validated prediction accuracy.
If a research question is already fixed, prepare its executable investigation plan;
otherwise offer distinct testable ideas. Training, benchmark comparisons and ablations
are appropriate only when the question requires them. Return the requested JSON schema.
"""

CHEMISTRY_PROPOSAL_PROMPT = """Prepare {{num_proposals}} chemistry proposals or plans.
Question, hypothesis or limitation: {{bit}}
Available inputs and constraints: {{additional_context}}
Literature evidence: {{literature_summary}}
For each proposal provide proposal_id, flip (approach and rationale in two sentences),
spark (short title), abstract (problem, method, planned evidence and relevance),
experiments (the necessary analytical or experimental steps) and risks_and_limitations.
Specify relevant source files, molecule identifiers, units, transformations, calculation
method and a checkable criterion answering the question. State missing inputs explicitly.
Match the number of steps to the question; a descriptive comparison can be sufficient.
Return a JSON object with a proposals list. Do not claim planned work has been performed.
"""

CHEMISTRY_SCORE_SYSTEM_PROMPT = """You evaluate chemistry research proposals fairly against
the stated question, available molecular or laboratory inputs and the planned evidence.
Assess scientific merit and feasibility independently. Distinguish heuristic estimates
from measured values and model predictions; ask for validation when claims require it.
Do not penalize a proposal for omitting training or ablations when they are irrelevant.
Use the existing scoring schema and ground each score in specific strengths and gaps.
"""

CHEMISTRY_SCORE_PROMPT = """Evaluate these proposals:
{% for proposal in proposals %}
{{proposal}}
{% endfor %}
Score novelty, clarity, significance, feasibility and soundness independently on 1-4
(poor, fair, good, excellent); overall on 1-5 (reject, weak reject, borderline, accept,
strong accept). Feasibility concerns available data, local tools, units, chemical
representations and an executable plan. Soundness concerns whether evidence can answer
the question, appropriate controls, uncertainty and limitations. Original methods are
not required for a useful analysis of supplied data; score originality honestly.
Return {"proposals": [...]} with proposal_id, spark, each criterion's *_reasoning and
integer score, strengths, weaknesses, improvement_suggestions, overall_reasoning and
overall. Provide specific reasoning and actionable suggestions without inventing evidence.
"""


class BitFlipInfo(BaseModel):  # type: ignore
    bit: str = Field(description="Technical limitation or conventional approach")
    flip: str = Field(description="Innovative approach or solution")
    spark: str = Field(description="4-6 word summary")


async def extract_bitflip_info(
    arxiv_id: str,
    *,
    model_name: Optional[str] = None,
    prompt_template: Optional[str] = None,
    temperature: Optional[float] = None,
) -> BitFlipInfo:
    """
    Extracts the Bit-Flip information from the arXiv paper.

    A Bit-Flip is a technique that inverts a commonly held assumption,
    questioning existing constraints or reapplying techniques to new domains/scales.
    The "Bit" is the prevailing belief, and the "Flip" is the counterargument.

    Args:
        arxiv_id: The arXiv ID of the paper to extract the Bit-Flip information from.
    """
    model_name = model_name or settings.BITFLIP_MODEL_NAME
    template = prompt_template or EXTRACT_PROMPT
    paper = arxiv_download(arxiv_id)
    # Use full paper text (abstract + sections) for richer analysis, not just abstract
    text = paper.abstract
    if paper.sections:
        full = "\n\n".join(paper.sections)
        # Truncate to ~8000 chars to fit LLM context while keeping key content
        text = f"{paper.abstract}\n\n{full[:8000]}"
    prompt = encode_prompt(template, abstract=text)
    bitflip_info: BitFlipInfo = await llm_acall_structured(
        model_name=model_name,
        messages=[ChatMessage(role="user", content=prompt)],
        temperature=temperature if temperature is not None else 0.0,
        response_format=BitFlipInfo,
    )
    return bitflip_info


class ResearchProposal(BaseModel):  # type: ignore
    proposal_id: int = Field(default=0, description="ID of the proposal")
    chain_of_reasoning: str = Field(
        default="",
        description="Reasoning that led to the proposal, if the prompt requests it",
    )
    flip: str = Field(
        description="Innovative approach or solution, in at least two sentences"
    )
    spark: str = Field(description="4-6 word summary")
    abstract: str = Field(
        description="An abstract that summarizes the proposal in conference format."
    )
    experiments: List[str] = Field(
        description="A list of experiments that would be conducted to validate the proposal."
    )
    risks_and_limitations: List[str] = Field(
        description="A list of potential risks and limitations of the proposal."
    )


class GenerateResearchProposalResponse(BaseModel):  # type: ignore
    proposals: List[ResearchProposal] = Field(
        description="A list of research proposals"
    )

    @field_validator("proposals", mode="before")
    @classmethod
    def coerce_proposals(cls, value: Any) -> Any:
        """Accept common LLM shape slips before Pydantic validates the list.

        Some models occasionally return a single proposal object as
        {"proposals": {"chain_of_reasoning": "...", ...}} instead of
        {"proposals": [{...}]}. Without this coercion, Pydantic iterates the
        dict keys as list items and raises errors like input_value='chain_of_reasoning'.
        """
        if isinstance(value, dict):
            if "proposals" in value:
                return value["proposals"]
            return [value]
        return value


async def generate_research_proposals(
    bit: str,
    num_proposals: int = 3,
    additional_context: str = "",
    literature_summary: str = "",
    *,
    research_profile: Literal["general", "chemistry"] = "general",
    model_name: Optional[str] = None,
    prompt_template: Optional[str] = None,
    system_prompt: Optional[str] = None,
    temperature: Optional[float] = None,
) -> GenerateResearchProposalResponse:
    """
    Proposes improvement ideas for the Bit.

    Args:
        bit: The Bit to propose improvement ideas for. The bit is a technical limitation or conventional approach of some paper.
        num_proposals: The number of proposals to generate.
        additional_context: Additional context to use when proposing the improvement idea.
        literature_summary: Full literature review context (paper titles, key findings, gaps). Passed verbatim into the LLM prompt alongside the bit. Use this to ground proposals in actual research.
        research_profile: Select general or chemistry instructions. Chemistry generation skips the ML example dataset.
        model_name: Override LLM (default: settings.BITFLIP_MODEL_NAME).
        prompt_template: Override user-side Jinja template (default: bundled IMPROVEMENT_PROMPT).
        system_prompt: Override system prompt (default: bundled SYSTEM_IMPROVEMENT_PROMPT).
        temperature: Override sampling temperature (default: 1.0).
    """
    model_name = model_name or settings.BITFLIP_MODEL_NAME
    chemistry = research_profile == "chemistry"
    user_template = prompt_template or (
        CHEMISTRY_PROPOSAL_PROMPT if chemistry else IMPROVEMENT_PROMPT
    )
    system_template = system_prompt or (
        CHEMISTRY_SYSTEM_PROMPT if chemistry else SYSTEM_IMPROVEMENT_PROMPT
    )
    max_completion_tokens = int(settings.BITFLIP_MAX_COMPLETION_TOKENS)
    examples = [] if chemistry else random.choices(ProposalDataset.get_dataset()[:], k=2)

    prompt = encode_prompt(
        user_template,
        bit=bit,
        examples=examples,
        num_proposals=num_proposals,
        additional_context=additional_context,
        literature_summary=literature_summary,
    )
    result: GenerateResearchProposalResponse = await llm_acall_structured(
        model_name=model_name,
        messages=[
            ChatMessage(role="system", content=system_template),
            ChatMessage(role="user", content=prompt),
        ],
        response_format=GenerateResearchProposalResponse,
        max_completion_tokens=max_completion_tokens,
        temperature=temperature if temperature is not None else 1.0,
    )
    return result


class ScoredProposal(BaseModel):  # type: ignore
    proposal_id: int = Field(default=0, description="ID of the proposal")
    spark: str = Field(description="4-6 word summary")
    novelty_reasoning: str = Field(
        default="", description="Reasoning for novelty score"
    )
    novelty: int = Field(description="Novelty rating from 1 to 4")
    clarity_reasoning: str = Field(
        default="", description="Reasoning for clarity score"
    )
    clarity: int = Field(description="Clarity rating from 1 to 4")
    significance_reasoning: str = Field(
        default="", description="Reasoning for significance score"
    )
    significance: int = Field(description="Significance rating from 1 to 4")
    feasibility_reasoning: str = Field(
        default="", description="Reasoning for feasibility score"
    )
    feasibility: int = Field(description="Feasibility rating from 1 to 4")
    soundness_reasoning: str = Field(
        default="", description="Reasoning for soundness score"
    )
    soundness: int = Field(description="Soundness rating from 1 to 4")
    strengths: List[str] = Field(description="A list of strengths of the proposal")
    weaknesses: List[str] = Field(description="A list of weaknesses of the proposal")
    improvement_suggestions: List[str] = Field(
        default_factory=list,
        description="Actionable suggestions to improve this proposal",
    )
    overall_reasoning: str = Field(
        default="", description="Reasoning for overall score"
    )
    overall: int = Field(description="Overall rating from 1 to 5")

    @field_validator(
        "novelty",
        "clarity",
        "significance",
        "feasibility",
        "soundness",
        mode="after",
    )
    @classmethod
    def clamp_subscore(cls, value: int) -> int:
        return max(1, min(4, int(value)))

    @field_validator("overall", mode="after")
    @classmethod
    def clamp_overall(cls, value: int) -> int:
        return max(1, min(5, int(value)))


class ScoreResearchProposalsResponse(BaseModel):  # type: ignore
    proposals: List[ScoredProposal] = Field(description="List of scored proposals")

    @field_validator("proposals", mode="before")
    @classmethod
    def coerce_proposals(cls, value: Any) -> Any:
        if isinstance(value, dict):
            if "proposals" in value:
                return value["proposals"]
            return [value]
        return value


async def score_research_proposals(
    proposals: str
    | List[Dict[str, Any]]
    | List[Any]
    | List[str]
    | List[ResearchProposal],
    *,
    research_profile: Literal["general", "chemistry"] = "general",
    model_name: Optional[str] = None,
    prompt_template: Optional[str] = None,
    system_prompt: Optional[str] = None,
    temperature: Optional[float] = None,
) -> ScoreResearchProposalsResponse:
    """
    Scores a list of research proposals.
    Use proposals obtained with the `generate_research_proposal` tool.

    Args:
        proposals: A list of JSON strings with research proposals.
        research_profile: Select general or chemistry instructions. Chemistry generation skips the ML example dataset.
        model_name: Override LLM (default: settings.BITFLIP_MODEL_NAME).
        prompt_template: Override Jinja template (default: bundled SCORE_PROMPT).
        temperature: Override sampling temperature (default: 0.0).
    """
    model_name = model_name or settings.BITFLIP_MODEL_NAME
    chemistry = research_profile == "chemistry"
    template = prompt_template or (CHEMISTRY_SCORE_PROMPT if chemistry else SCORE_PROMPT)
    system_template = system_prompt or (
        CHEMISTRY_SCORE_SYSTEM_PROMPT if chemistry else SCORE_SYSTEM_PROMPT
    )
    if isinstance(proposals, str):
        proposals = json.loads(proposals)
    assert isinstance(proposals, list), "Proposals should be a list"
    formatted: List[str] = []
    for p in proposals:
        if isinstance(p, BaseModel):
            formatted.append(json.dumps(p.model_dump(), indent=2, ensure_ascii=False))
        elif isinstance(p, dict):
            formatted.append(json.dumps(p, indent=2, ensure_ascii=False))
        else:
            formatted.append(str(p))
    prompt = encode_prompt(template, proposals=formatted)
    result: ScoreResearchProposalsResponse = await llm_acall_structured(
        model_name=model_name,
        messages=[
            ChatMessage(role="system", content=system_template),
            ChatMessage(role="user", content=prompt),
        ],
        response_format=ScoreResearchProposalsResponse,
        temperature=temperature if temperature is not None else 0.0,
    )
    return result
