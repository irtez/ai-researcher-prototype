# Writer Benchmark

Deterministic evaluation framework for the Holosophus Writer agent.
The canonical layout now lives under `benchmark/writer/`, where `writer/base`
and `writer/acl` each have their own `metrics/`, `tests/`, `scripts/`,
`notebooks/`, and `data/` directories.

## Directory Layout

```text
benchmark/
  requirements.txt
  writer/
    README.md
    base/
      README.md
      data/
      metrics/
      notebooks/
      scripts/
      tests/
    acl/
      README.md
      data/
      metrics/
      notebooks/
      scripts/
      tests/
```

## Metrics

| Metric | What it checks | Output | Fail type |
|--------|---------------|--------|-----------|
| Spelling Errors | Spelling mistakes in paper text | integer | soft |
| Grammar Errors | Grammar mistakes in paper text | integer | soft |
| Required Section Presence | ACL-required sections exist (`abstract`, `introduction`, `limitations`, `references`) | ratio | hard |
| Required Section Non-Emptiness | ACL-required sections are not stubs | ratio | hard |
| Section Order Validity | Canonical ACL section order is respected when sections are present | binary | soft |
| Abstract Slot Coverage | Abstract mentions task/method/result | ratio | soft |
| Contribution Statement Presence | Explicit contributions description | binary | soft |
| Experiment Slot Coverage | Experiments has datasets/baselines/metrics/setup | ratio | soft |
| Reproducibility Slot Coverage | Has code/data/training/hyperparameter details | ratio | soft |
| Citation Integrity | All \\cite keys resolve in bibliography | ratio | hard |
| Bibliography Usage Coverage | All bib entries are cited in text | ratio | soft |
| Figure/Table Reference Coverage | All figures/tables referenced in text | ratio | soft |
| Dangling Figure/Table Reference Count | References to non-existent figures/tables | integer | hard |
| Numbering Consistency | No gaps/conflicts in numbering | integer | soft |
| Caption Coverage | Every figure/table has a caption | ratio | hard |
| Metric Value Presence | Numeric values for claimed eval metrics | ratio | soft |
| Section Length Adequacy | Required sections not too short | ratio | soft |
| Notation Consistency | Consistent terms/symbols/notation | integer | soft |
| ACL Page Limit Compliance | Heuristic check that content stays within ACL long-paper limits | binary | hard |
| ACL Abstract Length Compliance | Abstract is at most 200 words | binary | hard |
| ACL Format Compliance | ACL template markers and compatible article class are present | binary | hard |
| ACL Review Version Compliance | Review-mode ACL submission is enabled and final-copy markers are absent | binary | hard |
| ACL Anonymization Compliance | No author identity leaks, acknowledgements, or self-identifying links | binary | hard |
| ACL Responsible NLP Checklist Compliance | Checklist artifact is included in the package | binary | hard |

### Metric Types

- **hard**: structural correctness; any failure indicates a clear Writer defect.
- **soft**: quality signal; imperfect scores may be acceptable but indicate areas for improvement.

## Quick Start

```bash
cd Holosophus/benchmark
pip install -r requirements.txt

# 1. Validate the generic writer package
pytest writer/base/tests -v

# 2. Validate the ACL writer package
pytest writer/acl/tests -v

# 3. Run the generic writer evaluator
python writer/base/scripts/run_evaluation.py ../workdir/<session_id>/paper/

# 4. Run the ACL evaluator
python writer/acl/scripts/run_evaluation.py ../workdir/<session_id>/paper/

# 5. Or use the root router script
python scripts/run_evaluation.py --profile acl ../workdir/<session_id>/paper/

# Legacy entrypoint remains available as a shim
python -m metrics.evaluator ../workdir/<session_id>/paper/
```

## Architecture Notes

- `writer/base/metrics` is the canonical generic metric package.
- `writer/acl/metrics` mirrors the same shape and adds ACL-specific extensions.
- Root-level `metrics`, `scripts`, `tests`, and `notebooks` remain as compatibility layers while the new layout settles.

## Three-Stage Validation Plan

### Stage 1: Validate metrics on gold papers
Run all metrics on curated human-written papers.
Expected: hard failures near zero, soft metrics in good ranges.

### Stage 2: Synthetic corruptions
Auto-generate corrupted papers (missing sections, broken refs, etc.).
Verify each metric detects the corresponding defect.

### Stage 3: Evaluate Writer
Run Writer with standard inputs, evaluate generated package.
Compare against gold baselines.

## Data Sources

| Source | Best for | Access |
|--------|----------|--------|
| arXiv LaTeX source | section/citation/figure structure | S3 requester-pays bulk |
| SurveyBench | compact curated gold set (~100 papers) | Hugging Face |
| S2ORC | structured full text, inline citations | Semantic Scholar |
| SurveyGen | large-scale survey corpus (4200+) | paper dataset |

## Related Docs

- [Writer Agent Reference](../docs/writer_agent_input_output_examples.md)
- [Manager-to-Writer Handoff](../docs/manager_to_writer_handoff.md)
- [Writer prompt](../holosophos/holosophos/prompts/writer.yaml)
