# Run Writer on Distilled Benchmark Packs

This guide explains how to run the Holosophos `writer` agent directly on distilled benchmark packs and keep the normal workspace artifacts, Langfuse traces, and optional evaluation reports.

Use this when you already have packs like:

```text
benchmark_distilled/<paper_id>/<level>/
  writer_handoff.md
  evidence/
  results/
  refs/
  figures/
  provenance/
  manifest.json
  validation_report.json
```

The core idea is:

1. Copy one distilled pack into `Holosophus/workdir/<session_id>/`.
2. Send the text of `writer_handoff.md` directly to `/agents/writer`.
3. Inspect generated files in `Holosophus/workdir/<session_id>/`.
4. Inspect Langfuse traces using the same `session_id`.
5. Optionally run the writer benchmark evaluator on the output directory.

---

## 1. Choose Session IDs

Use deterministic session IDs so workspace folders and Langfuse traces are easy to match.

Recommended format:

```text
writerbench__<paper_id_slug>__<level>
```

Examples:

```text
writerbench__2024_acl_long_10__oracle
writerbench__2024_acl_long_10__realistic
writerbench__2024_acl_long_10__incomplete
```

Use the same value for:

- the folder under `Holosophus/workdir/`;
- the `session_id` field in the writer request;
- later Langfuse lookup.

---

## 2. Start the Holosophus Stack

Run from `Holosophus/`:

```bash
docker compose up --build -d
```

Check services:

```bash
docker compose ps
```

Useful endpoints:

```text
Holosophus app: http://127.0.0.1:5055
Langfuse UI:   http://127.0.0.1:3000
Phoenix UI:    http://127.0.0.1:6006
```

The app container mounts:

```text
Holosophus/workdir -> /workdir
```

That is why copying files to `Holosophus/workdir/<session_id>/` makes them visible to the Writer tools.

---

## 3. Prepare One Writer Workdir

Example variables:

```bash
cd Holosophus

PACK="../benchmark_distilled/2024.acl-long.10/oracle"
SESSION_ID="writerbench__2024_acl_long_10__oracle"
WORKDIR="workdir/${SESSION_ID}"
```

If your `benchmark_distilled` directory is inside `Holosophus/`, use:

```bash
PACK="benchmark_distilled/2024.acl-long.10/oracle"
```

Create a clean workdir and copy the pack:

```bash
rm -rf "${WORKDIR}"
mkdir -p "${WORKDIR}"
cp -a "${PACK}/." "${WORKDIR}/"
```

Verify required input files:

```bash
test -f "${WORKDIR}/writer_handoff.md"
test -f "${WORKDIR}/refs/references.bib"
test -f "${WORKDIR}/manifest.json"
```

Do not copy the original PDF or original full LaTeX source into this workdir unless the benchmark condition explicitly allows it. The Writer benchmark should use distilled evidence, not the source paper.

---

## 4. Call the Writer Directly

The server exposes each managed agent under `/agents/<agent_name>`.

For this benchmark, call:

```text
POST http://127.0.0.1:5055/agents/writer
```

Use `writer_handoff.md` as the user message and pass the deterministic `session_id`.

```bash
python - <<'PY'
import json
from pathlib import Path

import httpx

session_id = "writerbench__2024_acl_long_10__oracle"
workdir = Path("workdir") / session_id
handoff = (workdir / "writer_handoff.md").read_text(encoding="utf-8")

payload = {
    "messages": [{"role": "user", "content": handoff}],
    "session_id": session_id,
    "stream": True,
}

events_path = workdir / "writer_events.jsonl"
url = "http://127.0.0.1:5055/agents/writer"

with httpx.stream("POST", url, json=payload, timeout=None) as response:
    response.raise_for_status()
    with events_path.open("w", encoding="utf-8") as out:
        for chunk in response.iter_text():
            if not chunk.strip():
                continue
            out.write(chunk)
            if not chunk.endswith("\n"):
                out.write("\n")
            try:
                event = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            content = event.get("content")
            if content:
                print(content, end="", flush=True)

print(f"\n\nSaved event stream to {events_path}")
PY
```

Expected generated files:

```text
workdir/<session_id>/
  started_prompt.md
  writer_events.jsonl
  paper.tex
  references.bib
  paper.pdf
  figures/
```

Writer runs use the canonical `paper.tex`, `references.bib`, and `paper.pdf`
filenames and update those files in place.

---

## 5. Check Workspace Artifacts

After the run:

```bash
ls -lah "${WORKDIR}"
```

Check key files:

```bash
test -f "${WORKDIR}/paper.tex"
test -f "${WORKDIR}/references.bib"
test -f "${WORKDIR}/paper.pdf"
```

Inspect the final event stream if needed:

```bash
python - <<'PY'
import json
from pathlib import Path

session_id = "writerbench__2024_acl_long_10__oracle"
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

## 6. Check Langfuse Traces

Open:

```text
http://127.0.0.1:3000
```

Search for the same session ID:

```text
writerbench__2024_acl_long_10__oracle
```

The Langfuse patch should record:

- top-level `writer:run` trace;
- LLM generation observations;
- step spans;
- tool execution spans;
- metadata such as `agent_name`, `session_id`, `step_number`, `called_tools`, and `workspace_subdir`.

You can also query the local Langfuse database:

```bash
docker compose exec -T langfuse-db psql \
  -U ${LANGFUSE_POSTGRES_USER:-postgres} \
  -d ${LANGFUSE_POSTGRES_DB:-postgres} \
  -c "SELECT id, name, session_id, timestamp FROM traces WHERE session_id = 'writerbench__2024_acl_long_10__oracle' ORDER BY timestamp DESC LIMIT 20;"
```

---

## 7. Run the Existing Writer Evaluator

For ACL-style papers:

```bash
python benchmark/writer/acl/scripts/run_evaluation.py \
  "workdir/${SESSION_ID}" \
  --json \
  --output "workdir/${SESSION_ID}/evaluation_acl.json"
```

For the generic writer evaluator:

```bash
python benchmark/writer/base/scripts/run_evaluation.py \
  "workdir/${SESSION_ID}" \
  --json \
  --output "workdir/${SESSION_ID}/evaluation_base.json"
```

The evaluator expects a generated paper package, usually including:

```text
paper.tex
references.bib
paper.pdf
```

---

## 8. Batch Runner Shape

Once the single-pack flow works, automate it with a small script that does:

```text
for each benchmark_distilled/<paper_id>/<level>/:
  session_id = writerbench__<paper_id_slug>__<level>
  copy pack to Holosophus/workdir/<session_id>/
  read writer_handoff.md
  POST to /agents/writer with session_id
  save stream to writer_events.jsonl
  optionally run evaluator
  write run_metadata.json
```

Recommended `run_metadata.json`:

```json
{
  "paper_id": "2024.acl-long.10",
  "level": "oracle",
  "session_id": "writerbench__2024_acl_long_10__oracle",
  "pack_dir": "../benchmark_distilled/2024.acl-long.10/oracle",
  "workdir": "workdir/writerbench__2024_acl_long_10__oracle",
  "writer_endpoint": "http://127.0.0.1:5055/agents/writer",
  "events_path": "workdir/writerbench__2024_acl_long_10__oracle/writer_events.jsonl",
  "expected_outputs": [
    "paper.tex",
    "references.bib",
    "paper.pdf"
  ]
}
```

Keep batch concurrency low at first. The Writer may call LaTeX compilation, figure generation, and other tools, so concurrent runs can be expensive.

Recommended initial settings:

```text
max_concurrency = 1
levels = oracle only
papers = 1 or 2
```

Then scale to:

```text
levels = oracle, realistic, incomplete
max_concurrency = 2 or 3
```

---

## 9. Troubleshooting

If `paper.pdf` is missing:

- inspect `writer_events.jsonl`;
- inspect Langfuse tool spans for `compile_latex`;
- inspect `paper.tex`;
- check whether `references.bib` exists;
- check whether the Writer used a non-standard output filename.

If Writer cannot see input files:

- confirm the pack was copied to `Holosophus/workdir/<session_id>/`;
- confirm the request used the same `session_id`;
- confirm `WORKSPACE_DIR=/workdir` in the app container;
- confirm `writer_handoff.md` tells Writer to read relative paths such as `evidence/problem_statement.md`.

If Langfuse traces are missing:

- confirm `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set in `Holosophus/.env`;
- restart the app container after changing `.env`;
- confirm the run went through `holosophos.server`, not a custom in-process script that skipped `patch_codearkt_openai_for_langfuse()`;
- search Langfuse by exact `session_id`.

If the Writer hallucinates missing information:

- check whether the pack marked missing fields as `unknown`, `null`, or empty lists;
- check whether `writer_handoff.md` explicitly forbids inventing missing results and citations;
- compare behavior across `oracle`, `realistic`, and `incomplete`.

---

## 10. Minimal Command Checklist

```bash
cd Holosophus
docker compose up --build -d

PACK="../benchmark_distilled/2024.acl-long.10/oracle"
SESSION_ID="writerbench__2024_acl_long_10__oracle"
WORKDIR="workdir/${SESSION_ID}"

rm -rf "${WORKDIR}"
mkdir -p "${WORKDIR}"
cp -a "${PACK}/." "${WORKDIR}/"

python - <<'PY'
import json
from pathlib import Path

import httpx

session_id = "writerbench__2024_acl_long_10__oracle"
workdir = Path("workdir") / session_id
handoff = (workdir / "writer_handoff.md").read_text(encoding="utf-8")
payload = {
    "messages": [{"role": "user", "content": handoff}],
    "session_id": session_id,
    "stream": True,
}
with httpx.stream("POST", "http://127.0.0.1:5055/agents/writer", json=payload, timeout=None) as response:
    response.raise_for_status()
    with (workdir / "writer_events.jsonl").open("w", encoding="utf-8") as out:
        for chunk in response.iter_text():
            if chunk.strip():
                out.write(chunk if chunk.endswith("\n") else chunk + "\n")
                try:
                    event = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                if event.get("content"):
                    print(event["content"], end="", flush=True)
PY

python benchmark/writer/acl/scripts/run_evaluation.py \
  "${WORKDIR}" \
  --json \
  --output "${WORKDIR}/evaluation_acl.json"
```
