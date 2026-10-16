# Agent Results

Current numbers for every Holosophos sub-agent. For **how to run** the evals, see
[`README.md`](README.md).

## 🏆 Current best metrics (latest & best per sub-agent)

Best observed result per agent on the current test sets. `auto` = produced by a
reproducible script; the **Source** column points to where the number lives.

| Sub-agent | Metric | Baseline | Best | n / dataset | Type | Source |
|---|---|---|---|---|---|---|
| **Librarian** | Recall (overall) | 35.8% / 86 min | **75%** (93 / 64 / 83) / 23 min | 95 (3 sub-benches) | auto | `results/librarian.md` |
| **GAIA** | Exact match | — | **60%** (6/10) | 10, gemini-3-flash | auto | `results/gaia.md` |
| **MLE Solver** | Threshold pass-rate | 5/7 | **7/7** (CPU suite) | 7 tasks | auto | `results/mle_solver.md` |
| **Proposer** — scoring | Range utilization (discrimination) | 33% | **75%**, all criteria GOOD (Cohen's d) | 9 × 3 tiers | auto | `results/proposer.md` |
| **Proposer** — idea-gen | BitFlip pass / grounding / ELO | 0.35 / 3.43 / 936 | **0.43 / 4.06 / 1063** | 24 tasks | auto | `results/proposer.md` |
| **Writer** | Compliance pass-rate over 24 deterministic checks | n/a (no before/after) | **~49–56%** overall · **~63–70%** hard | ACL-2024 runs | auto* | **`benchmark/`** |
| **Reviewer** | Spearman agreement w/ human (ICLR) | 0.55 (single) | **0.78** (a3pr) | ~10 papers | auto* | **`reviewer_patches/`** |

Notes:
- Small-n benchmarks (GAIA 10, Reviewer ~10) are smoke-level — good for
  regression checks, not statistical significance.
- *Writer and *Reviewer numbers come from **runs that are not committed**: the
  harness/metric code is real and re-runnable, but no result artifact backs the
  cited numbers yet (see the two subsections below). Treat them as "reproducible,
  not yet pinned in the repo".
- Writer has **no baseline → best** story: the benchmark is a one-shot compliance
  snapshot, not a v1→v2 improvement timeline (it groups by *input* quality, not
  agent version). The `~49–56% / ~63–70%` are **pass-rates of mechanical checks**,
  not a quality grade comparable to a human score.

## Where each agent is evaluated (three trees)

Eval infrastructure grew in three separate places. `evals_and_reports/` (this
dir) indexes the first; the other two are referenced, not duplicated:

| Tree | Agents | What |
|---|---|---|
| `holosophos/evals_and_reports/` (this dir) | Librarian, Proposer, MLE Solver, GAIA | scripts + versioned `results/*.md` |
| `benchmark/` | Writer only | 18 base + 6 ACL = 24 deterministic LaTeX/content metrics, `metadata_registry/`, `notebooks/analyze_writer_metrics.ipynb` |
| `reviewer_patches/` | Reviewer only | ICLR benchmark: `eval_pipeline.py` (MAE/RMSE/Spearman/Pearson vs human golden), `benchmark/{iclr_dataset,iclr_adapter,run_iclr_benchmark}.py` |

### Writer (`benchmark/`)

**What it is.** A *deterministic submission-compliance checker* for generated
LaTeX paper packages — **not** a quality judge. It runs **24 mechanical metrics**
(18 base + 6 ACL-specific; `benchmark/writer/{base,acl}/evaluator.py`,
documented in `benchmark/writer/acl/README.md`) and reports pass/fail per metric.
Runs are tracked via `benchmark/metadata_registry/` and analyzed in
`benchmark/notebooks/analyze_writer_metrics.ipynb`. The `run_writer_eval.py` stub
in `evals_and_reports/` computes no metrics (manual CodeArkt smoke only).

**Hard vs soft vs overall.** The 24 metrics split into **11 hard** (submission
blockers: required sections, citation integrity, captions, dangling refs, ACL
page/abstract limits, format, anonymization, Responsible-NLP checklist) and
**13 soft** (quality signals: spelling/grammar, slot coverage, notation
consistency, …). `hard_rate` = fraction of the 11 hard checks passed;
`overall_rate` = fraction of all 24. So **"~63–70% hard"** ≈ 7–8 of 11 blocking
checks pass; **"~49–56% overall"** ≈ 12–13 of 24. These are pass-rates of
mechanical checks, not a score comparable to human review.

**What "coverage" means.** A *family* of ratio metrics ∈ [0,1] measuring whether
the expected evidence is present and cross-referenced — not one number:
`bibliography_usage_coverage` (≈54.5%, share of `.bib` entries actually cited),
`figure_table_reference_coverage` (≈18%, floats referenced in text),
`caption_coverage` (floats with captions, hard), and slot-coverage —
`abstract_slot_coverage` / `experiment_slot_coverage` /
`reproducibility_slot_coverage` (0–9%, whether task/method/result,
dataset/baseline/metric/setup, and code/data cues are present). This family is
the closest thing to the slide's "evidence-package coverage", but it is **lower
and split across several metrics — there is no single 0.60→0.82 number.**
Structural checks (section order, captions, citation integrity, dangling refs)
sit near ~100%.

**No improvement history.** The notebook groups runs by **input distillation
level** (`oracle` / `realistic` / `incomplete` — i.e. how complete the material
fed to the Writer was), *not* by agent version or date. It answers "how does the
Writer do given good/medium/poor input" and "which metrics fail most", not
"how much did the Writer improve". There is no v1→v2 before/after series.

**Not committed.** No result artifact is in the repo: `git ls-files benchmark`
returns **zero** eval JSONs (no `evaluation_acl.json`, no `manual_acl_*`, no
`eval__*.json`). The harness, metrics, tests and notebook are committed, but the
ACL-2024 run behind `~49–56% / ~63–70%` is reproducible-only — re-run
`scripts/evaluate_writer_runs.py --profile acl` and commit
`benchmark/data/metadata/evaluations/` to pin the numbers.

### Reviewer (`reviewer_patches/`)

ICLR benchmark: `eval_pipeline.py` computes **MAE / RMSE / Spearman / Pearson**
vs human golden ICLR reviews; `run_iclr_benchmark.py` compares **`single`**
(baseline single-pass) vs **`a3pr`** (improved self-critique pipeline) and reports
`spearman_single`, `spearman_a3pr`, and the improvement. "Agreement with human" =
Spearman correlation (target > 0.5). The `run_reviewer_eval.py` stub in
`evals_and_reports/` prints output for manual inspection only.

Caveat: only smoke artifacts are committed
(`reviewer_patches/benchmark/base/*.json`: 1 and 4 papers; `benchmark/output/` is
empty). The full ~10-paper run behind **0.55 → 0.78** is not in the repo.

## Slide ↔ repo (honesty map)

What the presentation slide ("Оптимизации субагентов") claims vs what the code
actually computes — for honest Q&A:

| Slide row | Slide number | Reality in code |
|---|---|---|
| Librarian recall / time | 35.8%→76.8% / ×3.7 | `results/librarian.md` (best 75% overall, time 86m→23m). Δ is in **percentage points**, not relative. |
| MLE Solver pass-rate | 5/7 → 7/7 | `results/mle_solver.md`, 7-task CPU suite (solver-v3) — all pass. |
| Proposer "LLM as a Judge scoring" | 33% → 75% | Actually **range utilization** of the scoring prompt (`results/proposer.md`, scoring-v1, V2 prompt). Number is honest; the slide label is imprecise. |
| Reviewer "agreement with human" | 0.55 → 0.78 | **Spearman** vs human ICLR scores, `single → a3pr` in `reviewer_patches/run_iclr_benchmark.py`. Code exists; the run artifact is **not committed** (smoke only). |
| Writer "evidence-package coverage" | 0.60 → 0.82 | No single such metric. `benchmark/` has a coverage family (closest: `bibliography_usage_coverage` ≈ 0.55), but **not** a 0.60→0.82 number. Also the slide Δ is mislabeled: 0.82 − 0.60 = **0.22**, printed as +0.23. |

## Current Gaps

- Eval infra is fragmented across **three trees** (`evals_and_reports/`,
  `benchmark/`, `reviewer_patches/`); only the first is indexed in `README.md`.
  The other two are referenced above but not unified.
- Writer metrics live in `benchmark/`; Reviewer metrics live in
  `reviewer_patches/`. Neither is wired into the `results/*.md` format.
- **Reviewer:** the ICLR run behind 0.55→0.78 is not committed — only smoke
  artifacts exist (`benchmark/output/` is empty). TODO: commit the ~10-paper run
  output next to the reported number so it is reproducible from the repo.
- **Writer:** same problem — `git ls-files benchmark` has **zero** eval JSONs, so
  the `~49–56% / ~63–70%` numbers have no committed artifact either. TODO: re-run
  `scripts/evaluate_writer_runs.py --profile acl` and commit
  `benchmark/data/metadata/evaluations/`.
- Agent-specific datasets here are small (3 mle_solver, 3 proposer). Good for
  smoke tests, not for statistical significance.
- MLE Solver Claude Code mode has no batch runner — all results in
  `results/mle_solver.md` are from manual subagent invocations.
