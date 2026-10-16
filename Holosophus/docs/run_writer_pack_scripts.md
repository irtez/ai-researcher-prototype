# Run Writer Pack Scripts

This guide explains how to run the Writer benchmark scripts:

- `scripts/run_writer_pack.py` for one distilled pack;
- `scripts/run_writer_packs.py` for many distilled packs.

Run all commands from the `Holosophus/` directory:

```bash
cd /home/borist/projects/ai_scientists/Holosophus
```

Before running Writer, make sure the Holosophos app is up:

```bash
docker compose up --build -d
docker compose ps
```

The Writer app is expected at:

```text
http://127.0.0.1:5055
```

---

## Run One Pack

Use `scripts/run_writer_pack.py` when you want to run Writer on exactly one distilled evidence pack.

Example:

```bash
PACK="benchmark/data/benchmark_distilled/paper_1/acl__2024__quantized_side_tuning_fast_and_memory_efficient_tuning_of_quantized_large_language_models__oracle"

python3 scripts/run_writer_pack.py "${PACK}" --overwrite
```

What it does:

1. copies the pack to `workdir/<session_id>/`;
2. sends `writer_handoff.md` to `/agents/writer`;
3. writes `workdir/<session_id>/writer_events.jsonl`;
4. checks expected outputs:
   - `paper.tex`
   - `references.bib`
   - `paper.pdf`
5. writes run metadata to `benchmark/data/metadata/runs/<session_id>.json`;
6. links the run back into paper/distilled metadata records.

If Writer finishes but these required output files are missing, the run metadata is still written with `status: "missing_outputs"`, and the script exits with a non-zero code.

By default, the script infers `session_id` from the pack folder name:

```text
writerbench__{paper_uid}__{distillation_level}
```

You can provide it explicitly:

```bash
python3 scripts/run_writer_pack.py "${PACK}" \
  --session-id "writerbench__paper_1__oracle" \
  --overwrite
```

---

## Run One Pack With Evaluation

Use `--evaluator acl` or `--evaluator base`.

```bash
python3 scripts/run_writer_pack.py "${PACK}" \
  --overwrite \
  --evaluator acl
```

This additionally writes:

```text
workdir/<session_id>/evaluation_acl.json
benchmark/data/metadata/evaluations/eval__<session_id>__acl.json
```

If evaluation fails, the Writer run metadata is still saved and gets an `evaluation_error` field.

---

## Useful One-Pack Options

```bash
--app-url http://127.0.0.1:5055
```

Use another running Holosophos app instance.

```bash
--workdir-root workdir
```

Change where session workdirs are created.

```bash
--metadata-root benchmark/data/metadata
```

Change where benchmark metadata is written.

```bash
--timeout-seconds 3600
```

Change HTTP timeout for the Writer request.

```bash
--overwrite
```

Delete an existing `workdir/<session_id>/` before copying the pack. For multi-pack runs, omitting `--overwrite` makes existing workdirs get skipped.

```bash
--no-print-output
```

Do not print streamed Writer output to the terminal.

---

## Run Multiple Packs

Use `scripts/run_writer_packs.py` when you want to run Writer sequentially on many packs.

This script is a thin wrapper around `scripts/run_writer_pack.py`. It does not reimplement Writer logic; it resolves pack directories and calls the one-pack runner for each.

### Explicit Pack List

Pass pack directories as positional arguments:

```bash
python3 scripts/run_writer_packs.py \
  benchmark/data/benchmark_distilled/paper_1/acl__2024__quantized_side_tuning_fast_and_memory_efficient_tuning_of_quantized_large_language_models__oracle \
  benchmark/data/benchmark_distilled/paper_2/acl__2024__unsupervised_multimodal_clustering_for_semantics_discovery_in_multimodal_utterances__oracle \
  --overwrite
```

### Scan Folders Automatically

If you do not pass explicit pack paths, the script scans:

```text
benchmark/data/benchmark_distilled/
```

Run all levels:

```bash
python3 scripts/run_writer_packs.py --overwrite
```

Run only oracle packs:

```bash
python3 scripts/run_writer_packs.py \
  --levels oracle \
  --overwrite
```

Run oracle and realistic packs:

```bash
python3 scripts/run_writer_packs.py \
  --levels oracle realistic \
  --overwrite
```

---

## Dry Run

Always start with `--dry-run` before launching a large batch:

```bash
python3 scripts/run_writer_packs.py \
  --levels oracle \
  --limit 3 \
  --dry-run \
  --no-print-output
```

This prints resolved packs and the exact `run_writer_pack.py` commands without running Writer.

`--limit` counts resolved packs. Distillation levels are separate packs, so
`oracle`, `realistic`, and `incomplete` for the same paper count as three.
Use `--limit-papers` when you want all selected levels for the first N paper
directories:

```bash
python3 scripts/run_writer_packs.py \
  --limit-papers 5 \
  --dry-run \
  --no-print-output
```

---

## Batch With Evaluation

Run all oracle packs and evaluate each with ACL evaluator:

```bash
python3 scripts/run_writer_packs.py \
  --levels oracle \
  --overwrite \
  --evaluator acl
```

For a first smoke run, limit the batch:

```bash
python3 scripts/run_writer_packs.py \
  --levels oracle \
  --limit 1 \
  --overwrite \
  --evaluator acl
```

Evaluate already generated Writer workdirs without rerunning Writer:

```bash
python3 scripts/evaluate_writer_runs.py \
  --profile acl
```

By default this scans registered runs, skips incomplete workdirs, writes
`workdir/<session_id>/evaluation_acl.json`, writes
`benchmark/data/metadata/evaluations/eval__<session_id>__acl.json`, updates the
corresponding run record, and rebuilds JSONL metadata indexes.

Evaluate only selected runs:

```bash
python3 scripts/evaluate_writer_runs.py \
  writerbench__acl__2024__quantized_side_tuning_fast_and_memory_efficient_tuning_of_quantized_large_language_models__incomplete \
  --profile acl
```

---

## Failure Behavior

By default, the batch stops at the first failed pack.

A pack is failed when `run_writer_pack.py` exits with a non-zero code. This includes cases where the Writer request completed but required files (`paper.tex`, `references.bib`, `paper.pdf`) are missing.

Existing workdirs are not failures in multi-pack runs. If `workdir/<session_id>/` already exists and `--overwrite` is not set, `run_writer_packs.py` skips that pack and counts it under `Skipped`.

To continue running later packs:

```bash
python3 scripts/run_writer_packs.py \
  --levels oracle \
  --overwrite \
  --keep-going
```

At the end, it prints:

```text
Completed batch. Successes: <N>. Failures: <M>.
```

With skipped packs, the final summary also includes:

```text
Skipped: <K>
```

During non-dry runs, the batch prints progress after each pack finishes or is
skipped:

```text
Progress: <done>/<total> packs (<percent>%) | papers touched: <done>/<total> | ok: <N>, failed: <M>, skipped: <K> | elapsed: <time> | ETA: <time>
```

ETA is estimated from completed/skipped packs. It updates between packs rather
than during a single Writer request.

If any pack failed, the script exits with code `1`.

---

## Recommended Workflow

1. Register original and distilled metadata:

```bash
python3 scripts/register_original_papers.py
python3 scripts/register_distilled_packs.py
python3 scripts/build_metadata_index.py
```

2. Start the app:

```bash
docker compose up --build -d
```

3. Dry-run one or a few packs:

```bash
python3 scripts/run_writer_packs.py \
  --levels oracle \
  --limit 2 \
  --dry-run
```

4. Run one real pack:

```bash
python3 scripts/run_writer_packs.py \
  --levels oracle \
  --limit 1 \
  --overwrite \
  --evaluator acl
```

5. Rebuild metadata indexes:

```bash
python3 scripts/build_metadata_index.py
```

6. Check outputs:

```bash
ls workdir
ls benchmark/data/metadata/runs
ls benchmark/data/metadata/evaluations
```

---

## Notes About Models And Settings

These scripts do not set the Writer model per run.

The model and token/settings configuration are determined by the running Holosophos app. To change them, update the app configuration or start another app instance with a different configuration, then pass its URL with:

```bash
--app-url http://127.0.0.1:<port>
```

`run_writer_pack.py` records model/settings metadata for provenance, but it does not override the live Writer server configuration.
