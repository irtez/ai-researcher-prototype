# Writer ACL Benchmark

ACL-specific evaluation profile for papers produced by the Holosophus Writer
agent. This package extends the generic Writer benchmark in
`benchmark/writer/base/` with ACL-style structure, formatting, anonymity, and
submission-compliance checks.

Use this profile when the generated paper is expected to look like an ACL long
paper review submission: ACL template, anonymous review mode, required sections,
reasonable page/abstract limits, valid citations, and a Responsible NLP
checklist.

## Directory Layout

```text
benchmark/writer/acl/
  README.md
  evaluator.py              # ACL metric list and CLI entrypoint
  profile.py                # ACL policies: sections, aliases, slots, limits
  formatting.py             # ACL-only formatting/submission metrics
  structural.py             # base structural metrics configured with ACL policy
  content.py                # base content metrics configured with ACL policy
  citation.py               # ACL aliases for shared citation metrics
  figures.py                # ACL aliases for shared figure/table metrics
  text_quality.py           # ACL aliases for shared text-quality metrics
  metrics/                  # compatibility import shims
  tests/                    # ACL-specific tests and compliance regressions
  scripts/                  # ACL benchmark runners
  notebooks/                # ACL-oriented analysis notebooks
  data/                     # ACL benchmark data location
```

## Relationship To Base Metrics

Most metrics are implemented once in `benchmark/writer/base/` and reused here.
The ACL package configures those shared metrics with ACL-specific policies from
`profile.py`:

- required sections: `abstract`, `introduction`, `limitations`, `references`;
- expected section order: introduction, related work, method, experiments,
  results, discussion/conclusion, limitations;
- ACL section aliases such as `methodology -> method` and
  `evaluation -> experiments`;
- ACL slot definitions for abstract, experiments, reproducibility, and notation.

The following ACL modules are mostly policy wrappers around base primitives:

- `structural.py`
- `content.py`
- `citation.py`
- `figures.py`
- `text_quality.py`

See `benchmark/writer/base/` for the reusable parsing and metric logic.

## Metric Set

The ACL evaluator runs the metric list in `evaluator.py`.

| Metric | Source | What it checks | Output | Fail type |
|--------|--------|----------------|--------|-----------|
| `spelling_errors` | base | Spelling mistakes in extracted paper text | integer | soft |
| `grammar_errors` | base | Grammar mistakes in extracted paper text | integer | soft |
| `required_section_presence` | base + ACL policy | ACL-required sections exist | ratio | hard |
| `required_section_non_emptiness` | base + ACL policy | Required sections are not empty stubs | ratio | hard |
| `section_order_validity` | base + ACL policy | Expected ACL section order is respected when sections are present | binary | soft |
| `abstract_slot_coverage` | base + ACL policy | Abstract mentions task, method, and result | ratio | soft |
| `contribution_statement_presence` | base + ACL policy | Introduction contains an explicit contribution statement | binary | soft |
| `experiment_slot_coverage` | base + ACL policy | Experiments/results mention datasets, baselines, metrics, and setup | ratio | soft |
| `reproducibility_slot_coverage` | base + ACL policy | Paper includes code/data/training/hyperparameter reproducibility cues | ratio | soft |
| `citation_integrity` | base | All LaTeX citation keys resolve in `references.bib` | ratio | hard |
| `bibliography_usage_coverage` | base | Bibliography entries are cited in text | ratio | soft |
| `figure_table_reference_coverage` | base | Figures/tables are referenced in text | ratio | soft |
| `dangling_figure_table_reference_count` | base | No references to missing figures/tables | integer | hard |
| `numbering_consistency` | base | No obvious numbering gaps/conflicts | integer | soft |
| `caption_coverage` | base | Every figure/table has a caption | ratio | hard |
| `metric_value_presence` | base | Claimed evaluation metrics include numeric values | ratio | soft |
| `section_length_adequacy` | base + ACL policy | Required sections are not too short | ratio | soft |
| `notation_consistency` | base + ACL policy | Terms/notation are used consistently | integer | soft |
| `acl_page_limit_compliance` | ACL | Measured PDF page count, or fallback page estimate, is within the ACL long-paper limit | binary | hard |
| `acl_abstract_length_compliance` | ACL | Abstract is at most 200 words | binary | hard |
| `acl_format_compliance` | ACL | ACL template markers and compatible `article` class are present | binary | hard |
| `acl_review_version_compliance` | ACL | Review-mode ACL submission is enabled and `\\aclfinalcopy` is absent | binary | hard |
| `acl_anonymization_compliance` | ACL | No obvious author identity leaks, acknowledgements, emails, or self-identifying links | binary | hard |
| `acl_responsible_nlp_checklist_compliance` | ACL | Responsible NLP checklist is present as a file or inline artifact | binary | hard |

## How The ACL Metrics Work

The ACL profile is not trying to replace human review. It checks whether a
generated paper package satisfies the mechanical and evidence-grounded
requirements that a reviewer or submission system would expect before judging
scientific novelty.

### Structure Metrics

These metrics reuse base structural checks with ACL-specific section policy from
`profile.py`.

- `required_section_presence`
  - **Why**: an ACL-style paper must contain the core sections needed for review.
  - **How**: parses LaTeX section headings, normalizes aliases, and checks for
    `abstract`, `introduction`, `limitations`, and `references`.
  - **Failure means**: the Writer omitted a required section or used a heading
    that the policy does not recognize.

- `required_section_non_emptiness`
  - **Why**: a generated paper can include section headers but leave them as
    empty stubs.
  - **How**: checks that required sections contain enough extracted text after
    parsing.
  - **Failure means**: at least one required section is missing real content.

- `section_order_validity`
  - **Why**: ACL papers should follow a readable scientific order.
  - **How**: compares discovered normalized sections against the expected order
    in `ACL_SECTION_POLICY`.
  - **Failure means**: sections are present but arranged in an unusual or
    confusing order.

- `section_length_adequacy`
  - **Why**: very short sections often signal placeholder writing.
  - **How**: checks section word counts against the ACL policy minimum.
  - **Failure means**: at least one important section is too thin to be useful.

### Content Coverage Metrics

These are base content metrics configured with ACL-oriented slot definitions.
They inspect text patterns rather than judging scientific correctness directly.

- `abstract_slot_coverage`
  - **Why**: an ACL abstract should state the task/problem, the method, and the
    main result.
  - **How**: searches the abstract for regex patterns representing `task`,
    `method`, and `result` slots.
  - **Failure means**: the abstract is probably vague or missing a key summary
    component.

- `contribution_statement_presence`
  - **Why**: reviewers need to quickly identify what the paper claims to
    contribute.
  - **How**: searches the introduction for contribution phrases such as
    "our contributions are" or "we make the following contributions".
  - **Failure means**: the contribution may be implicit rather than clearly
    stated.

- `experiment_slot_coverage`
  - **Why**: ACL empirical papers need enough experimental context to assess the
    results.
  - **How**: checks experiments/results sections for dataset, baseline, metric,
    and setup cues.
  - **Failure means**: the evaluation section is underspecified, even if some
    results are present.

- `reproducibility_slot_coverage`
  - **Why**: generated papers should include enough implementation and data
    information to support reproducibility.
  - **How**: searches the paper for code availability, data availability,
    training detail, and hyperparameter cues.
  - **Failure means**: the paper is missing reproducibility information expected
    in an ACL submission.

- `metric_value_presence`
  - **Why**: claims like "improves accuracy" should be backed by actual numbers.
  - **How**: scans evaluation-related text for metric claims and nearby numeric
    values.
  - **Failure means**: the paper may make qualitative result claims without
    quantitative support.

### Citation And Bibliography Metrics

These metrics are shared with the base profile because citation mechanics are
not ACL-specific.

- `citation_integrity`
  - **Why**: unresolved citation keys break LaTeX output and make references
    unreliable.
  - **How**: extracts `\cite{...}` keys from the paper and checks that they
    exist in `references.bib`.
  - **Failure means**: at least one citation key is dangling or misspelled.

- `bibliography_usage_coverage`
  - **Why**: generated bibliographies should not include irrelevant unused
    entries.
  - **How**: compares bibliography entries against citation keys used in text.
  - **Failure means**: `references.bib` contains unused entries, which can signal
    copied or padded references.

### Figure And Table Metrics

These metrics check whether floats are internally consistent and actually used
in the paper narrative.

- `figure_table_reference_coverage`
  - **Why**: figures and tables should be discussed in the text.
  - **How**: extracts float labels and checks whether they are referenced.
  - **Failure means**: at least one figure/table is present but never mentioned.

- `dangling_figure_table_reference_count`
  - **Why**: references to missing figures/tables confuse readers and can break
    cross-references.
  - **How**: extracts `\ref{...}`-style references and checks them against
    existing float labels.
  - **Failure means**: the paper refers to a float that does not exist.

- `caption_coverage`
  - **Why**: every figure/table should be understandable from its caption.
  - **How**: checks discovered floats for captions.
  - **Failure means**: at least one float is missing a caption.

- `numbering_consistency`
  - **Why**: inconsistent manual numbering is a common generated-LaTeX artifact.
  - **How**: looks for obvious gaps or conflicts in figure/table numbering.
  - **Failure means**: numbering may be manually written or inconsistent.

### Text Quality Metrics

These metrics are soft signals. They are useful for triage, but they can depend
on optional local tools.

- `spelling_errors`
  - **Why**: catches obvious typos in generated paper text.
  - **How**: uses the available spell-check backend; if the dependency is not
    installed, the result details say it was skipped.
  - **Failure means**: spelling quality may need manual cleanup.

- `grammar_errors`
  - **Why**: catches surface-level grammar issues.
  - **How**: uses the available grammar backend; if Java/language-tool is not
    installed, the result details say it was unavailable.
  - **Failure means**: the generated text may need editing.

- `notation_consistency`
  - **Why**: inconsistent terminology makes papers harder to read.
  - **How**: checks ACL policy variant groups such as `fine-tune/fine tune` and
    `state-of-the-art/state of the art`.
  - **Failure means**: the paper uses inconsistent variants of important terms.

## ACL-Only Formatting Metrics

The metrics in `formatting.py` are the main ACL-specific additions:

- `ACLPageLimitCompliance`
  - **Why**: ACL long papers have page limits; a generated paper that is too long
    is not submission-ready.
  - **How**: if `paper.pdf` exists, counts real PDF pages from the generated PDF.
    It uses `pypdf` when installed, then `pdfinfo` if available, then a raw PDF
    marker fallback for simple uncompressed PDFs. If no PDF page count is
    available, it falls back to a deterministic estimate from extracted word
    count, explicit page breaks, and floats.
  - **Failure means**: the paper likely needs compression before submission.

- `ACLAbstractLengthCompliance`
  - **Why**: ACL abstracts are limited to 200 words.
  - **How**: counts words in the parsed abstract and compares against
    `ACL_ABSTRACT_WORD_LIMIT`.
  - **Failure means**: the abstract is too long and should be shortened.

- `ACLFormatCompliance`
  - **Why**: ACL submissions should use the ACL style/template rather than an
    arbitrary LaTeX format.
  - **How**: searches the raw LaTeX for ACL package/template markers and a
    compatible `article` document class.
  - **Failure means**: the Writer probably used the wrong template or omitted
    ACL style setup.

- `ACLReviewVersionCompliance`
  - **Why**: benchmark outputs are evaluated as review submissions, not
    camera-ready papers.
  - **How**: checks that the ACL package is configured with review mode and that
    `\aclfinalcopy` is absent.
  - **Failure means**: the source looks like a final/camera-ready version or does
    not clearly enable ACL review mode.

- `ACLAnonymizationCompliance`
  - **Why**: ACL review submissions must avoid obvious author identity leaks.
  - **How**: scans raw LaTeX for non-anonymous `\author{...}` blocks, email
    addresses, acknowledgements sections, and self-identifying public links such
    as GitHub/GitLab/Google Drive URLs.
  - **Failure means**: the paper may violate anonymous-review requirements.

- `ACLResponsibleNLPChecklistCompliance`
  - **Why**: ACL-style submissions are expected to include a Responsible NLP
    checklist or equivalent artifact.
  - **How**: recursively searches the package for files with `checklist` in the
    filename, or checks whether the raw LaTeX contains the phrase
    `Responsible NLP Research Checklist`.
  - **Failure means**: the Writer generated a paper but did not assemble a full
    ACL-compatible submission package.

## Hard vs Soft Failures

- **Hard** failures indicate structural or submission-compliance defects that
  should generally fail the generated paper package.
- **Soft** failures are quality or completeness signals. They are useful for
  ranking outputs and diagnosing weak writing, but a generated paper may still be
  usable with some soft failures.

The evaluator returns both groups in one report, for example:

```json
{
  "summary": {
    "total_metrics": 24,
    "hard_passed": "9/11",
    "soft_passed": "8/13"
  },
  "results": [
    {
      "name": "acl_review_version_compliance",
      "value": 1.0,
      "fail_type": "hard",
      "passed": true,
      "details": {
        "has_review_option": true,
        "has_finalcopy": false
      }
    }
  ]
}
```

## Run The Evaluator

From `Holosophus/benchmark`:

```bash
python writer/acl/scripts/run_evaluation.py ../workdir/<session_id> --json
```

From the Holosophus repo root:

```bash
python benchmark/scripts/run_evaluation.py \
  --profile acl \
  --json \
  ../Holosophus/workdir/<session_id>
```

For already generated Writer runs with metadata registry integration, prefer:

```bash
python scripts/evaluate_writer_runs.py --profile acl
```

That command evaluates complete `workdir/<session_id>/` folders without
rerunning Writer and writes:

```text
workdir/<session_id>/evaluation_acl.json
benchmark/data/metadata/evaluations/eval__<session_id>__acl.json
```

## Run Tests

From `Holosophus/benchmark`:

```bash
pytest writer/acl/tests -v
```

The ACL tests include regression cases for missing ACL template markers,
camera-ready/final-copy mode, deanonymization leaks, overlong abstracts, page
limit violations, and missing checklist artifacts.

## Analysis Workflow

After evaluating generated runs, inspect aggregate results with:

```text
benchmark/notebooks/analyze_writer_metrics.ipynb
```

The notebook loads registry evaluation records, flattens per-metric results into
tables, compares pass rates across `oracle`, `realistic`, and `incomplete`
distillation levels, and highlights the most frequent metric failures.
