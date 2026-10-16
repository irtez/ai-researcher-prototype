# Holosophus Data Topology

This document maps the main runtime data objects and where they live.

## Runtime-to-Storage Matrix

| Data | Runtime Location | Persistent Location | Canonical Source |
| --- | --- | --- | --- |
| Session artifacts | `workdir/<session_id>/` | same (host-mounted volume) | filesystem |
| Uploaded originals | `workdir/<session_id>/inputs/` | same | UI upload API |
| Session profile / model | `.holosophus_ui/research_profile`, `.holosophus_ui/model` in the session | same | shared `research_session.py` |
| Agent traces | in-memory → Langfuse | `langfuse-db` postgres | Langfuse UI / API |
| Benchmark predictions | in-memory → JSONL | `reports/*.jsonl` | filesystem |
| Agent config | `settings.py` fields | `.env` overrides | `settings.py` defaults |
| MCP config | `MCP_CONFIG` dict | `docker-compose.yml` env | `main_agent.py` |
| Prompts | `prompts/*.yaml` | same | filesystem |
| Figures | PaperBanana output | `workdir/figures/paperbanana/` | filesystem |
| Writer check state | `/state/checks.sqlite3` | `writer_tools_state` volume | Writer MCP SQLite store |

## Repository Data Areas

- `Holosophus/.env`
  Integration-layer runtime configuration.

- `Holosophus/workdir/`
  Per-session runtime artifacts and generated files.

- `Holosophus/holosophos/reports/data/`
  Small benchmark and evaluation datasets checked into the repo.

- `Holosophus/holosophos/reports/*.jsonl`
  Benchmark prediction outputs produced by eval runs.

- `Holosophus/docs/`
  Factual maps and investigation notes for the integrated stack.

## Session and Workspace Model

The main runtime key is `session_id`.

For the main app:
- the client session id
- the workspace folder name under `workdir/`
- the Langfuse session id

are intentionally aligned.

Typical research ids look like:
- `research__topic__model__001`

At session start, the initial prompt is persisted as:
- `workdir/<session_id>/started_prompt.md`

Generated artifacts such as notes, scripts, figures, and papers are stored under the same workspace folder.

An upload claims the existing owner marker without starting the run. The research
profile is fixed when execution starts, so an upload-only session can still select
chemistry. Legacy started sessions without a profile marker use `general`.
See [chemistry operations](operations/chemistry-local.md) for input formats and tools.

## Writer Quality-Check State

The `writer-tools` service owns the coordination state for deterministic
Writer checks. Its SQLite database is `/state/checks.sqlite3`, persisted in
the Compose volume `writer_tools_state`. Paper inputs remain under
`workdir/<session_id>/`; the service mounts that tree read-only.

Each check is keyed by an opaque `check_run_id` and owned by the pair
`(session_id, agent_name)`. A caller must provide all three values on later
calls, preventing another Writer session or agent from resuming the run by
accident. The store records status, current and completed pass numbers,
per-tool successes, input fingerprints, and per-tool failure counts.

A pass completes only when `section_order`, `content_placement`, and
`paper_length` have all succeeded. `quality_reruns_used` is therefore derived
from completed passes and never advances on a failed MCP/analyzer call. The
defaults allow 4 quality reruns and 10 failures per analyzer. Reaching an
analyzer's failure limit marks the check `blocked`. Finished or inactive stale
runs are retained for 168 hours by default; cleanup runs when a new check is
started.

The split smoke phases persist only restart metadata at
`workdir/smoke__writer_tools/check.json`; the authoritative state stays in
SQLite. Run `--phase start`, restart `writer-tools`, then run `--phase resume`
to validate volume persistence.

## Benchmark Session Model

Benchmarks use two ids:

- `langfuse_session_id`
  One shared id for the whole benchmark run.

- `workspace_session_id`
  One per benchmark question.

Example:
- benchmark session: `benchmark__gaia__validation__gemini_3_flash_preview__20260315T235618Z`
- question workspace ids:
  - `benchmark__gaia__validation__gemini_3_flash_preview__20260315T235618Z__q001`
  - `benchmark__gaia__validation__gemini_3_flash_preview__20260315T235618Z__q002`

This gives:
- grouped observability in Langfuse
- isolated workspaces for per-question files
- stable `predictions.jsonl` rows keyed by per-question session id

## Langfuse Storage

Primary tables:
- `projects`
- `trace_sessions`
- `traces`
- `observations`

Useful mapping:
- one Langfuse `trace_session` groups one main app run or one whole benchmark run
- one `trace` usually represents one agent run such as `manager:run` or `librarian:run`
- `observations` store steps, tool calls, and model generations inside the trace

Usage and cost fields live on `observations`, especially generation observations.

## Benchmark Output Files

GAIA-style runs write JSONL rows with fields such as:
- `query`
- `result`
- `session_id`
- `token_usage`
- `duration`

The canonical in-repo location for benchmark runner docs and recorded benchmark snapshots is:
- `Holosophus/holosophos/reports/README.md`
- `Holosophus/holosophos/reports/results.md`

## Configuration Sources

Configuration is intentionally split:

- `Holosophus/.env`
  Runtime env, providers, API keys, observability, compose stack config.

- `Holosophus/holosophos/reports/config.yaml`
  Local defaults for benchmark runners.

- `Holosophus/holosophos/holosophos/settings.py`
  App-level settings, tool allowlists, and agent defaults.

### Resolution Chain

Env var resolution order: `.env` → `docker-compose.yml` defaults → `settings.py` defaults.

Notable: `MODEL_NAME` defaults differ between layers:
- `docker-compose.yml`: `deepseek/deepseek-chat-v3.1`
- `settings.py`: `deepseek/deepseek-chat-v3-0324`

At runtime in Docker, the compose default wins because it sets the env var before `settings.py` reads it.

## Important Entities

| Entity | ID Pattern | Where Stored |
| --- | --- | --- |
| workspace session | `research__<topic>__<model>__001` | `workdir/`, Langfuse trace metadata |
| benchmark session | `benchmark__<name>__<split>__<model>__<ts>` | Langfuse `trace_sessions` |
| per-question session | `<benchmark_session>__q001` | `workdir/`, JSONL rows |
| trace | UUID | Langfuse `traces` |
| observation | UUID | Langfuse `observations` |
| smoke session | `smoke__pipeline__<model>__<counter>` | `workdir/`, Langfuse |
| Writer check run | opaque hexadecimal ID | `writer_tools_state` SQLite volume |

## Connection Entry Points

- `.env` — API keys, ports, model names, Langfuse credentials
- `settings.py` — tool allowlists, agent defaults, MCP URLs
- `docker-compose.yml` — service wiring, env var defaults, volume mounts
- `WRITER_CHECK_MAX_RERUNS`, `WRITER_CHECK_TOOL_MAX_RETRIES`, and
  `WRITER_CHECK_STATE_RETENTION_HOURS` — Writer check lifecycle limits
- `reports/config.yaml` — benchmark runner defaults

## Local Inspection Recipes

```bash
# List recent workdir sessions
ls -lt workdir/ | head -20

# Check model name resolution in container
docker compose exec app python -c "from holosophos.settings import settings; print(settings.MODEL_NAME)"

# View benchmark predictions
cat holosophos/reports/predictions.jsonl | python -m json.tool

# Exercise Writer MCP state persistence around a service restart
python scripts/smoke_test_writer_tools_mcp.py --phase start
docker compose restart writer-tools
python scripts/smoke_test_writer_tools_mcp.py --phase resume

# Langfuse: latest trace sessions
docker compose exec -T langfuse-db psql -U postgres -d postgres -c \
  "SELECT id, created_at FROM trace_sessions ORDER BY updated_at DESC LIMIT 10;"

# Langfuse: traces for a session
docker compose exec -T langfuse-db psql -U postgres -d postgres -c \
  "SELECT name, timestamp, metadata FROM traces WHERE session_id = '<session_id>' ORDER BY timestamp;"
```
