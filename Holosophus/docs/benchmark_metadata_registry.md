# Benchmark Metadata Registry

This note explains the metadata layer used by the writer benchmark pipeline.

For the full step-by-step execution flow, see `docs/run_writer_benchmark_end_to_end.md`.

The goal is to keep benchmark identity and analysis metadata outside the Writer workdir. The Writer should only see distilled evidence packs; original-paper metadata, gold paths, model/run metadata, and evaluation records are stored separately under:

```text
benchmark/data/metadata/
```

## Directory Layout

```text
benchmark/data/metadata/
  papers/
    <paper_uid>.json
  distilled/
    <distilled_variant_id>.json
  runs/
    <session_id>.json
  evaluations/
    <evaluation_id>.json
  index/
    papers.jsonl
    distilled.jsonl
    runs.jsonl
    evaluations.jsonl
```

Primary records are the individual JSON files. The JSONL files in `index/` are derived analysis indexes and can be rebuilt at any time.

## Identifiers

Use portable IDs rather than absolute paths.

```text
paper_uid = {conference}__{year}__{paper_title_slug}
distilled_variant_id = {paper_uid}__{distillation_level}
session_id = writerbench__{paper_uid}__{distillation_level}
```

Example:

```text
acl__2024__quantized_side_tuning_fast_and_memory_efficient_tuning_of_quantized_large_language_models
acl__2024__quantized_side_tuning_fast_and_memory_efficient_tuning_of_quantized_large_language_models__oracle
```

Absolute paths are not used as primary identifiers because distillation may happen on another machine.

## Metadata Modules

Code lives in:

```text
benchmark/metadata_registry/
```

Responsibilities:

- `naming.py`: stable IDs, slugging, distilled variant parsing, root TeX/title discovery.
- `builders.py`: constructors for paper, distilled variant, and writer-run records.
- `store.py`: deterministic JSON reads/writes, link merging, JSONL index generation.

Scripts should use these modules instead of hand-writing metadata JSON.

## Scripts

Register original/gold papers:

```bash
python scripts/register_original_papers.py
```

Register distilled packs:

```bash
python scripts/register_distilled_packs.py
```

Run Writer on one pack and register the run:

```bash
python scripts/run_writer_pack.py \
  benchmark/data/benchmark_distilled/paper_1/acl__2024__quantized_side_tuning_fast_and_memory_efficient_tuning_of_quantized_large_language_models__oracle \
  --overwrite
```

If you also want evaluation metadata registered, pass an evaluator:

```bash
python scripts/run_writer_pack.py \
  benchmark/data/benchmark_distilled/paper_1/acl__2024__quantized_side_tuning_fast_and_memory_efficient_tuning_of_quantized_large_language_models__oracle \
  --overwrite \
  --evaluator acl
```

Rebuild analysis indexes:

```bash
python scripts/build_metadata_index.py
```

## Update Pattern

Use the registry as layered metadata:

1. Original download/import writes `metadata/papers/<paper_uid>.json`.
2. Distillation writes `metadata/distilled/<distilled_variant_id>.json` and links it from the paper record.
3. Writer runs write `metadata/runs/<session_id>.json` and link the run from paper/distilled records.
4. Evaluation writes `metadata/evaluations/<evaluation_id>.json` and can link back to run/paper records.

Run and evaluation records should be append-style: prefer new IDs for new attempts instead of mutating old records.

Paper and distilled records can be rewritten deterministically by registration scripts.

## Writer Visibility

The metadata registry is intentionally outside `workdir/<session_id>/`.

Writer-visible files:

```text
workdir/<session_id>/
  writer_handoff.md
  evidence/
  results/
  refs/
  figures/
  provenance/
```

Writer-hidden metadata:

```text
benchmark/data/metadata/
```

This prevents original-paper metadata and gold-source paths from leaking into the Writer prompt or tools.
