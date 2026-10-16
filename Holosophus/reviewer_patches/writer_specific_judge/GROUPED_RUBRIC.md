# Grouped Rubric LLM Judge

> **Canonical entry point (updated)**: The grouped-rubric judge is now integrated
> into `reviewer_patches` as `review_mode="writer_specific_judge"`.
>
> Preferred usage:
> ```python
> from reviewer_patches.review import review_pdf_paper
> result = await review_pdf_paper(pdf_path, review_mode="writer_specific_judge")
> ```
>
> The `benchmark/writer/base/llm_judge` package remains the underlying
> implementation.  `reviewer_patches/writer_specific_judge/` is the canonical
> re-export layer; do **not** add new logic directly to `llm_judge` — extend
> through `reviewer_patches` instead.

This package keeps the grouped-rubric judge isolated as a pure implementation
library.  Orchestration and entry-point routing lives in `reviewer_patches`.

## Why Grouped Calls

The rubric is intentionally not sent as one giant prompt. Each LLM call evaluates one semantically coherent group of 3-6 criteria. This reduces cross-metric bias, where a model forms a holistic impression and copies that impression into unrelated metrics.

Rules for every group call:

- Evaluate only the current group.
- Do not judge the whole paper.
- Do not assign a group score.
- Do not let one criterion influence another.
- Return one score per `criterion_id`.
- Return JSON only.

## Metric Definitions

Metric meaning is defined in `llm_judge/definitions.py` and loaded from `rubrics/conference_reviewer_rubric.json`.
Each criterion definition contains:

- `criterion_id`
- `name`
- `meaning`
- `question`
- `score_descriptions`
- `prompt_guidance`
- `evidence_expectations`
- `missing_info_examples`
- reviewer examples
- `required_evidence`

The Pydantic models in `llm_judge/models.py` validate LLM output shape only. They are not the source of metric semantics.

## Score Scale

- `0`: absent, poor, or not satisfied
- `1`: partial, incomplete, or unclear
- `2`: good or sufficient

The default conference-reviewer rubric does not allow `N/A`; missing required reporting should receive `0` or `1`. Module scores are computed programmatically as:

```text
sum(criterion scores) / (2 * number of criteria)
```

Severity and confidence are recorded as metadata and are not converted into score points.

## Routing

Group selection is handled by `llm_judge/loader.py` and `llm_judge/paper_type.py`.
The default `conference_reviewer_rubric.json` runs all 5 distilled reviewer groups for most paper types:

- `D01_framing_positioning_claims`
- `D02_method_evidence_robustness`
- `D03_interpretation_limitations_discussion`
- `D04_reproducibility_artifacts`
- `D05_statistics_llm_and_writing`

Paper type resolution order:

1. Explicit `paper_type`, if provided.
2. Regex heuristic.
3. Optional OpenRouter classifier when `use_llm_paper_type=True`.

## Positional Bias Controls

`run_grouped_rubric_review` supports:

- `shuffle_criteria=True` to randomize criterion order inside each group prompt.
- `shuffle_seed` for deterministic shuffling.
- `repeat_count` for repeated sequential group calls.

Repeated calls are stored as separate group results. Criterion scores are not rewritten during final aggregation.

## Related Work Search

The judge can add related-work context before group calls:

- `auto_related_work=True` enables automatic related-work enrichment.
- `use_llm_keywords=False` uses local regex keyword extraction.
- `use_llm_keywords=True` asks OpenRouter to extract specific search terms first.

The related-work pipeline lives in `llm_judge/related_work.py` (and delegates to `writer_specific_judge/related_work.py`):

1. Extract search keywords from the paper.
2. Search arXiv through the local `academia_mcp` package using `academia_mcp.tools.arxiv_search` (wrapped in `asyncio.to_thread` to prevent event loop blocking).
3. Access fields from the structured `ArxivSearchResponse` (utilizing Pydantic models `ArxivSearchEntry` to extract `title`, `abstract`, `id`, and `published` safely).
4. Rank candidates by lexical similarity.
5. Inject a `# Related Work Context` block into each group prompt.

You can also pass `related_work="..."` directly to bypass search, or inject a custom `related_work_provider` in scripts/tests.

## Recommended Calls

- Default conference-reviewer rubric: 5 group calls.

Multiply by `repeat_count` when using repeated calls.

## Minimal Usage

```python
from writer.base.llm_judge import run_grouped_rubric_review

result = await run_grouped_rubric_review(
    paper_text,
    paper_type="llm_system",
    auto_related_work=True,
    use_llm_keywords=True,
    shuffle_criteria=True,
    repeat_count=1,
)
```

Tests use a mocked `group_caller`, so normal smoke tests do not require API keys. Real OpenRouter calls require `OPENROUTER_API_KEY`.
