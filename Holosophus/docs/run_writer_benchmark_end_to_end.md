# Run Writer Benchmark End to End

This guide explains how to run the full Writer benchmark pipeline step by step with the current scripts and metadata registry.

For focused CLI usage of `scripts/run_writer_pack.py` and `scripts/run_writer_packs.py`, see `docs/run_writer_pack_scripts.md`.

It covers:

1. original/gold paper registration,
2. distilled-pack registration,
3. Holosophos stack startup,
4. Writer execution on one distilled pack,
5. evaluation,
6. metadata verification,
7. index rebuilding for analysis.

This guide assumes:

- original papers are under `benchmark/data/gold/...`;
- distilled packs are under `benchmark/data/benchmark_distilled/...`;
- metadata registry lives under `benchmark/data/metadata/...`;
- Writer runs use `Holosophus/workdir/<session_id>/`;
- metadata must stay outside `workdir` so the Writer cannot read it.

---

## 1. Repository State

From `Holosophus/`, the important directories are:

```text
benchmark/data/
  gold/
  benchmark_distilled/
  metadata/

workdir/

scripts/
  register_original_papers.py
  register_distilled_packs.py
  run_writer_pack.py
  run_writer_packs.py
  build_metadata_index.py
```

The metadata registry layout is:

```text
benchmark/data/metadata/
  papers/
  distilled/
  runs/
  evaluations/
  index/
```

---

## 2. Register Original Papers

Run this first so the registry knows about the gold/source papers.

```bash
cd /home/borist/projects/ai_scientists/Holosophus

python3 scripts/register_original_papers.py
```

What it does:

- scans `benchmark/data/gold/acl_accepted/2024_acl/`;
- finds likely root `.tex` files;
- extracts the original title;
- creates one JSON per paper in `benchmark/data/metadata/papers/`.

Verify:

```bash
ls benchmark/data/metadata/papers | head
```

You should see files like:

```text
acl__2024__quantized_side_tuning_fast_and_memory_efficient_tuning_of_quantized_large_language_models.json
```

---

## 3. Register Distilled Packs

Run this after distilled packs are copied to the machine.

```bash
python3 scripts/register_distilled_packs.py
```

What it does:

- scans `benchmark/data/benchmark_distilled/`;
- finds pack directories whose names match:
  `{conference}__{year}__{paper_slug}__{distillation_level}`;
- creates one JSON per distilled pack in `benchmark/data/metadata/distilled/`;
- links each pack back to its paper record.

Verify:

```bash
ls benchmark/data/metadata/distilled | head
```

You should see files like:

```text
acl__2024__quantized_side_tuning_fast_and_memory_efficient_tuning_of_quantized_large_language_models__oracle.json
```

---

## 4. Build Analysis Indexes

This step is optional but recommended after every registration phase.

```bash
python3 scripts/build_metadata_index.py
```

What it does:

- rebuilds JSONL indexes under `benchmark/data/metadata/index/`;
- makes metadata easy to load into pandas or polars later.

Verify:

```bash
ls benchmark/data/metadata/index
```

Expected files:

```text
papers.jsonl
distilled.jsonl
runs.jsonl
evaluations.jsonl
```

---

## 5. Start the Holosophos Stack

Bring up the app, executor, Langfuse, Phoenix, and MCP services.

```bash
docker compose up --build -d
```

Check status:

```bash
docker compose ps
```

Useful endpoints:

```text
Holosophos app: http://127.0.0.1:5055
Langfuse UI:   http://127.0.0.1:3000
Phoenix UI:    http://127.0.0.1:6006
```

If you changed `Holosophus/.env`, recreate the app:

```bash
docker compose up -d --build --no-deps --force-recreate app
```

---

## 6. Run Writer on One Distilled Pack

Choose one distilled pack.

Example:

```bash
PACK="benchmark/data/benchmark_distilled/paper_1/acl__2024__quantized_side_tuning_fast_and_memory_efficient_tuning_of_quantized_large_language_models__oracle"
SESSION_ID="writerbench__acl__2024__quantized_side_tuning_fast_and_memory_efficient_tuning_of_quantized_large_language_models__oracle"
```

Run:

```bash
python3 scripts/run_writer_pack.py \
  "${PACK}" \
  --session-id "${SESSION_ID}" \
  --overwrite \
  --evaluator acl
```

What it does:

1. copies the distilled pack into `workdir/<session_id>/`;
2. reads `writer_handoff.md`;
3. calls `POST /agents/writer`;
4. saves `writer_events.jsonl` in the workdir;
5. saves run metadata in `benchmark/data/metadata/runs/<session_id>.json`;
6. if `--evaluator` is set, writes:
   - evaluation output into the workdir,
   - evaluation metadata into `benchmark/data/metadata/evaluations/`.

---

## 7. Verify Writer Outputs

Check the workdir:

```bash
ls -lah "workdir/${SESSION_ID}"
```

Expected files:

```text
workdir/<session_id>/
  writer_handoff.md
  started_prompt.md
  writer_events.jsonl
  paper.tex
  references.bib
  paper.pdf
```

Optional extras:

```text
evaluation_acl.json
figures/
```

Quick output check:

```bash
test -f "workdir/${SESSION_ID}/paper.tex"
test -f "workdir/${SESSION_ID}/references.bib"
test -f "workdir/${SESSION_ID}/paper.pdf"
```

Inspect the event stream tail:

```bash
python3 - <<'PY'
import json
from pathlib import Path

session_id = "writerbench__acl__2024__quantized_side_tuning_fast_and_memory_efficient_tuning_of_quantized_large_language_models__oracle"
events_path = Path("workdir") / session_id / "writer_events.jsonl"

for line in events_path.read_text(encoding="utf-8").splitlines()[-20:]:
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        continue
    if event.get("content"):
        print(event["content"])
PY
```

---

## 8. Verify Metadata Records

### Paper record

```bash
ls benchmark/data/metadata/papers | head
```

### Distilled record

```bash
ls benchmark/data/metadata/distilled | head
```

### Run record

```bash
ls "benchmark/data/metadata/runs/${SESSION_ID}.json"
```

### Evaluation record

If you ran with `--evaluator acl`, the evaluation record should be:

```bash
ls "benchmark/data/metadata/evaluations/eval__${SESSION_ID}__acl.json"
```

Inspect run metadata:

```bash
python3 - <<'PY'
import json
from pathlib import Path

session_id = "writerbench__acl__2024__quantized_side_tuning_fast_and_memory_efficient_tuning_of_quantized_large_language_models__oracle"
path = Path("benchmark/data/metadata/runs") / f"{session_id}.json"
data = json.loads(path.read_text(encoding="utf-8"))
print(json.dumps({
    "paper_uid": data["paper_uid"],
    "distilled_variant_id": data["distilled_variant_id"],
    "status": data["status"],
    "model": data["model"],
    "output_status": data["output_status"],
}, indent=2, ensure_ascii=False))
PY
```

Inspect paper record linkage:

```bash
python3 - <<'PY'
import json
from pathlib import Path

paper_uid = "acl__2024__quantized_side_tuning_fast_and_memory_efficient_tuning_of_quantized_large_language_models"
path = Path("benchmark/data/metadata/papers") / f"{paper_uid}.json"
data = json.loads(path.read_text(encoding="utf-8"))
print(json.dumps({
    "paper_uid": data["paper_uid"],
    "distilled_variant_ids": data["distilled_variant_ids"],
    "run_ids": data["run_ids"],
    "evaluation_ids": data["evaluation_ids"],
}, indent=2, ensure_ascii=False))
PY
```

---

## 9. Rebuild Metadata Indexes Again

After runs/evaluations, rebuild the JSONL analysis indexes:

```bash
python3 scripts/build_metadata_index.py
```

This updates:

```text
benchmark/data/metadata/index/papers.jsonl
benchmark/data/metadata/index/distilled.jsonl
benchmark/data/metadata/index/runs.jsonl
benchmark/data/metadata/index/evaluations.jsonl
```

These files are the easiest input for downstream metrics notebooks and analysis scripts.

---

## 10. Verify Langfuse

Search in Langfuse UI:

```text
http://127.0.0.1:3000
session_id = <SESSION_ID>
```

You should see:

- `writer:run` trace;
- step spans;
- tool execution spans;
- the same `session_id` as in `metadata/runs/<session_id>.json`.

Database check:

```bash
docker compose exec -T langfuse-db psql \
  -U ${LANGFUSE_POSTGRES_USER:-postgres} \
  -d ${LANGFUSE_POSTGRES_DB:-postgres} \
  -c "SELECT id, name, session_id, timestamp FROM traces WHERE session_id = '${SESSION_ID}' ORDER BY timestamp DESC LIMIT 20;"
```

---

## 11. Repeat for More Packs

Once one pack works, repeat the same run command for:

- the `realistic` variant of the same paper;
- the `incomplete` variant;
- then more papers.

Recommended progression:

1. one paper, `oracle` only;
2. same paper, all three levels;
3. two or three papers, one level each;
4. batch runs.

Keep concurrency low initially because the Writer may call compilation and figure-generation tools.

---

## 12. Common Failure Checks

If `paper.pdf` is missing:

- inspect `workdir/<session_id>/writer_events.jsonl`;
- inspect `workdir/<session_id>/paper.tex`;
- inspect Langfuse tool spans for `compile_latex`.

If run metadata is missing:

- confirm `scripts/run_writer_pack.py` completed successfully;
- confirm `benchmark/data/metadata/runs/` exists;
- rerun `python3 scripts/build_metadata_index.py`.

If evaluation metadata is missing:

- confirm you passed `--evaluator acl` or `--evaluator base`;
- check whether `evaluation_acl.json` or `evaluation_base.json` exists in the workdir;
- inspect the run metadata for `evaluation_error`.

If original-paper linkage is missing:

- rerun `python3 scripts/register_original_papers.py`;
- rerun `python3 scripts/register_distilled_packs.py`;
- rebuild indexes.

---

## Minimal Checklist

```bash
cd /home/borist/projects/ai_scientists/Holosophus

python3 scripts/register_original_papers.py
python3 scripts/register_distilled_packs.py
python3 scripts/build_metadata_index.py

docker compose up --build -d

PACK="benchmark/data/benchmark_distilled/paper_1/acl__2024__quantized_side_tuning_fast_and_memory_efficient_tuning_of_quantized_large_language_models__oracle"
SESSION_ID="writerbench__acl__2024__quantized_side_tuning_fast_and_memory_efficient_tuning_of_quantized_large_language_models__oracle"

python3 scripts/run_writer_pack.py \
  "${PACK}" \
  --session-id "${SESSION_ID}" \
  --overwrite \
  --evaluator acl

python3 scripts/build_metadata_index.py
```
