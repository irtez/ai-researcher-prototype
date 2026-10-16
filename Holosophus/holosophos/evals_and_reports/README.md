# Evals and Reports

How to run agent evals. **Numbers, scoreboard, the three eval trees, and the
slide↔repo honesty map → [`AGENT_RESULTS.md`](AGENT_RESULTS.md).**

## Layout

```
evals_and_reports/
├── data/                          # test datasets (versioned with eval code)
├── results/<agent>.md             # versioned results: librarian, gaia, mle_solver, proposer
├── run_scoring_eval.py            # Proposer — scoring prompt quality (standalone)
├── run_proposer_ablation_eval.py  # Proposer — idea-generation, variant-matrix ablation
├── run_proposer_eval.py           # Proposer — e2e (CodeArkt, legacy 3-task smoke)
├── proposer_variants.yaml         # ablation variant matrix
├── run_librarian_eval.py          # Librarian retrieval (CodeArkt)
├── run_gaia.py / get_gaia_metrics.py   # GAIA pipeline + metrics post-processing
├── run_mle_solver_eval.py         # MLE Solver (CodeArkt)
├── run_{writer,reviewer}_eval.py  # stubs — real metrics in benchmark/ and reviewer_patches/
├── AGENT_RESULTS.md               # 📊 scoreboard
└── README.md                      # this file
```

## Two execution modes

- **CodeArkt** — runners `evals_and_reports/run_*_eval.py`. In-process agent via
  CodeArkt `run_batch(...)` (a real CodeAct pipeline with MCP tools and subagents,
  not a mock); does not touch the running `holosophos.server`. `run_gaia.py`
  restricts agents to `librarian`+`mle_solver`; others use the full manager.
- **Claude Code** — runners `scripts/run_*_eval.py`. `ClaudeSDKClient` worker pool
  (`--workers 8`); MCP must be up (`make up-mcp`). One shared Langfuse session per
  run, isolated workspace per question (`<run_id>__q001`).

**Model** resolves CLI `--model_name` → `settings.MODEL_NAME` (`.env`), no
hardcoded default. **GAIA nrows**: CLI `--nrows` → `config.yaml gaia.nrows` →
unlimited. **Langfuse**: `--enable_langfuse=true`; tag arms via
`--langfuse_session_id`.

## Quick reference

All commands assume `cwd = Holosophus/`; CodeArkt ones cd into `holosophos/` first.

```bash
# Proposer — scoring prompt (~$0.10, ~2 min)
cd holosophos && uv run python evals_and_reports/run_scoring_eval.py discrimination --config v2
cd holosophos && uv run python evals_and_reports/run_scoring_eval.py ab --config-a current --config-b v2

# Proposer — idea-generation ablation (smoke split = cheap check)
cd holosophos && uv run python evals_and_reports/run_proposer_ablation_eval.py --split dev --variants v0_current,v_improved_generation_default_scorer --seeds 3 --output_dir evals_and_reports/proposer_eval_results/<name>

# Librarian — Claude Code (95q, ~30 min, 8 workers) | --max-questions N smoke | --dataset librarian = 15q
uv run python scripts/run_librarian_eval.py --workers 8
# Librarian — CodeArkt (15q)
cd holosophos && uv run python evals_and_reports/run_librarian_eval.py evals_and_reports/data/librarian_test.jsonl --enable_langfuse=true

# GAIA subset (20q, ~2h, ~$5-10) — see Parallel & A/B for sharding
cd holosophos && uv run python evals_and_reports/run_gaia.py --split validation --nrows 20 --enable_langfuse=true --predictions-path gaia_20.jsonl
cd holosophos && uv run python evals_and_reports/get_gaia_metrics.py gaia_20.jsonl

# MLE Solver — CodeArkt (3q, hours). Claude Code: invoke the mle-solver subagent manually (no batch runner).
cd holosophos && uv run python evals_and_reports/run_mle_solver_eval.py evals_and_reports/data/mle_solver_test.jsonl --enable_langfuse=true

# Proposer e2e — CodeArkt legacy smoke (3q)
cd holosophos && uv run python evals_and_reports/run_proposer_eval.py evals_and_reports/data/proposer_test.jsonl --enable_langfuse=true
```

## Benchmarks at a glance

| Agent | Tests | Dataset (n) | Runner | Cost | Re-run after |
|---|---|---|---|---|---|
| Librarian | retrieval: find paper/answer from NL query | librarian 15 + litsearch 50 + infodeepseek 30 = **95** | `scripts/run_librarian_eval.py` (CC) / `run_librarian_eval.py` (CA) | ~$2–5 | prompts, tool routing, academia MCP, context mgmt |
| GAIA | full manager→librarian+mle_solver pipeline | `gaia-benchmark/GAIA` (val 165 / test ~300) | `run_gaia.py` + `get_gaia_metrics.py` | ~$0.3–1/q | agent composition, CodeArkt runtime, trimming, MCP |
| MLE Solver | train models to quality thresholds | `mle_solver_test.jsonl` (3: House Prices RMSE<0.13, CIFAR-10 acc≥0.85, Path-star loss<0.1) | `run_mle_solver_eval.py` | hours/q (GPU) | prompts, remote GPU, bash/editor tools |
| Proposer · scoring | scoring prompt separates weak/med/strong | `scoring_testset.json` (9 = 3 tiers) | `run_scoring_eval.py` | ~$0.05–0.2 | `SCORE_PROMPT`, scoring model/criteria |
| Proposer · idea-gen | proposal quality across model/prompt/tool axes | `proposer_ablation_tasks.jsonl` (smoke 2 / dev 10 / bench 12) | `run_proposer_ablation_eval.py` + `proposer_variants.yaml` | varies | proposer prompts, generation, scoring, A/B |
| Writer | 24 deterministic LaTeX/content metrics | — | **`benchmark/`** (separate tree) | — | writer prompts, LaTeX/PDF tools |
| Reviewer | Spearman/MAE vs human ICLR, `single` vs `a3pr` | — | **`reviewer_patches/`** (separate tree) | — | reviewer prompts/pipeline (`review_mode="general"` default; `review_mode="writer_specific_judge"` for grouped-rubric LLM judge) |

**Metric per agent** — Librarian: recall (target id in response). GAIA: exact
match after normalization. MLE: threshold pass/fail. Proposer scoring:
discrimination (Cohen's d), stability (ICC), cross-model Spearman. Proposer
idea-gen: BitFlip pass + best-of-N, pairwise ELO (multi-judge, forward+reverse),
diversity (jaccard), grounding, order-consistency. Writer/Reviewer: see their
trees and `AGENT_RESULTS.md`. The `run_{writer,reviewer}_eval.py` stubs here only
print output for manual inspection.

## Datasets

| File | n | Schema / note |
|---|---|---|
| `data/librarian_test_sample.jsonl` | 15 | `{query, target: [arxiv_id]}` |
| `data/litsearch_sample.jsonl` | 50 | `{query, target: [s2_corpus_ids]}` |
| `data/infodeepseek_sample.jsonl` | 30 | `{query, target_answer}` |
| `data/mle_solver_test.jsonl` | 3 | `{query, field, target, mode}` |
| `data/scoring_testset.json` | 9 | proposals × 3 quality tiers |
| `data/proposer_ablation_tasks.jsonl` | 24 | `{task_id, split, topic, domain, instruction, bit, literature_context, reference_papers}` |
| `data/gaia_validation_30.json` | 30 | GAIA stratified subset |
| `data/proposer_test.jsonl` | 3 | *legacy* — `{query, field, target}` for `run_proposer_eval.py` |
| `data/librarian_test{,_additional}.jsonl` | 9, 6 | *legacy* — superseded by `librarian_test_sample.jsonl` |

## Versioning & results

Each agent has sub-benchmarks (e.g. proposer = `scoring` + `idea_generation`),
each versioned in `results/<agent>.md` (versions table + per-run results +
changelog; see `results/proposer.md` for the full shape). **Bump the version**
when eval code, dataset, or scoring criteria change — **not** when the agent
itself changes (that's just a new run). Partial re-eval: copy unaffected
sub-benchmarks, re-run only the changed one.

Raw outputs: Claude Code librarian → `results/raw/claude_librarian_<ts>/`
(`summary.json` with `per_benchmark` accuracy + `worker_<N>.jsonl`); CodeArkt →
flat `results/raw/<tag>_raw.jsonl`.

## Parallel & A/B

- **Librarian (CC):** in-process workers (`--workers 8`), no sharding needed.
- **GAIA:** shard by `--start_index`/`--nrows` into separate `--predictions-path`
  files, `cat` them, then run `get_gaia_metrics.py` on the merge.
- **A/B:** run each arm with identical settings but a distinct
  `--langfuse_session_id` (e.g. `benchmark__librarian__masking_v1_<date>`);
  compare accuracy + `prompt_tokens` + steps/q + duration in Langfuse.

---

**📊 Scoreboard, three eval trees & slide↔repo honesty map →
[`AGENT_RESULTS.md`](AGENT_RESULTS.md).**
