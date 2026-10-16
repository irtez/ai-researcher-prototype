# Observability

Holosophus has two orchestration paths and both now report to **Langfuse v3**
(`http://localhost:3051`). The legacy **v2** (`http://localhost:3050`) is kept
read-only for historical CodeArkt traces — it no longer receives new traces.

- Active UI (v3): `http://localhost:3051`
- Archive UI (v2 — historical CodeArkt traces only): `http://localhost:3050`
- Storage: v3 spans Postgres + ClickHouse + MinIO (`docker-compose.mcp.yml`,
  compose project `holosophus-lf3`); v2 is the single Postgres `langfuse-db`.

## Two paths, one Langfuse

| Path | Entry point | Tracing layer | Trace shape |
|---|---|---|---|
| **CodeArkt** | `codearkt.llm.LLM` driving `manager` / sub-agents | `holosophos/holosophos/langfuse_openai_patch.py` patches `openai.AsyncOpenAI` and the CodeAct loop | Top-level traces named `manager:run`, `librarian:run`, `mle_solver:run`, with step spans (`manager:step:N`) and tool/LLM child spans |
| **Claude Code** | `scripts/run_research.py` → `ClaudeSDKClient` from `claude-agent-sdk` | `scripts/langsmith_fork/` — forked langsmith→Langfuse bridge that wraps `ClaudeSDKClient.query()` and `receive_response()` | Top-level trace `claude.conversation`; per-LLM-call spans `claude.llm.*`; sub-agents emit their own child chain runs |

Both paths now write to the same v3 instance, so their traces sit side by side.
Differences only matter when you read traces, not when you query them.

## Phoenix for the Claude Code SDK path

The local Claude stack also runs Phoenix at `http://127.0.0.1:6007`, backed
by the separate `ai-researcher-claude_phoenix_data` volume. Set
`ENABLE_PHOENIX=true`, `PHOENIX_ENDPOINT=http://127.0.0.1:6007/v1/traces`
and `PHOENIX_PROJECT_NAME=ai-researcher` in `.env`, then restart the host runner.
The [deployment guide](operations/claude-local.md) covers startup and ports.

`scripts/cc_research_lib.py` initializes the existing `langsmith_fork` bridge
for both `cc_runner.py` and `run_research.py`. `scripts/phoenix_tracing.py`
adds a batch exporter to the same OTel provider, without replacing the
Langfuse processor. For Phoenix only, a copy of each span receives
OpenInference kinds, inputs/outputs, model and token fields where the bridge
supplies them. Trace/span IDs, parent links, timestamps and errors are retained.
The project is selected with the `openinference.project.name` resource field;
see [Phoenix project setup](https://arize.com/docs/phoenix/tracing/how-to-tracing/setup-tracing/setup-projects).

Token counts retain existing `llm.token_count.prompt|completion|total` fields.
Missing fields use `gen_ai.usage.input_tokens|output_tokens|total_tokens`, then
the Claude bridge's `langsmith.metadata.usage_metadata`. LangSmith sends this
metadata dictionary as a JSON string; the exporter reads that wire format as
well as flattened `langsmith.metadata.usage_metadata.*` attributes. Phoenix's
REST view flattens JSON, so inspecting its dotted keys alone does not establish
the original OTel attribute format.

The bridge already includes cache-read/cache-creation tokens in `input_tokens`.
Those details must not be added again. It replaces cumulative ResultMessage
updates on the current final LLM span, closing it once; the Phoenix exporter
does not spread usage over other messages or parent spans. Independent calls
with equal counters remain separate. If no total was supplied, it is derived
only when both prompt and completion counts are known. Missing or invalid
counts remain absent; genuine zero is retained and integral floats become ints.

These are SDK-reported token counters, including cached input, not a measured
subscription allowance or payment. The final span can carry a whole query's
aggregate; it is not a verified per-message breakdown. Cost mapping, historical
traces and the original spans sent to Langfuse are unchanged.

`trace_run_context` associates records with the run's session and optional
browser user. Child spans inherit these fields even when the bridge's worker
passes only a parent-span context. `flush_tracing` first drains LangSmith's
queue, then flushes Langfuse and Phoenix. Collector failures are nonfatal;
successful buffer flushing by itself is not a delivery acknowledgement.
Phoenix can remain enabled when Langfuse keys are absent. Configuration is
read at process startup; disabling export does not stop the Phoenix container.

These spans describe the orchestration and its LLM/tool calls, including MCP
calls. Internal operations inside every MCP server are not automatically
instrumented. This addition applies to the Python SDK UI/CLI path; the
interactive `research-console` hook/OTel modes below are separate mechanisms.

A telemetry-only check creates four explicitly synthetic records through
the real bridge and verifies their delivery, hierarchy, session, error status
and synthetic usage of 120 prompt / 7 completion / 127 total tokens. Only the
LLM record carries these counts. It never invokes Claude, an LLM API or an MCP tool:

```bash
uv run --locked --env-file .env python scripts/smoke_test_cc_phoenix.py \
  --output /tmp/phoenix-cc-dual-export.json
# A separate process with Langfuse credentials removed from its environment:
uv run --locked --env-file .env python scripts/smoke_test_cc_phoenix.py \
  --phoenix-only --output /tmp/phoenix-cc-only-export.json
```

The synthetic LLM record and intentional tool error check the display and
transport contract. They are not evidence of a completed research task.
For a separate verification project, set `PHOENIX_PROJECT_NAME=ai-researcher-usage-check`
in the test process. This does not change the runner's configured project.

## What ends up in Langfuse

### CodeArkt

- agent run trace, e.g. `manager:run`
- step spans `manager:step:N`
- LLM generations inside each step
- tool/Python executor spans
- normalized message history attached to the trace and to each step span (so
  Langfuse stays navigable without opening every child observation)

### Claude Code

- root trace `claude.conversation` (chain run)
- `trace_inputs["prompt"]` — the user prompt for the run
- `trace_inputs["system"]` — the manager `system_prompt` rendered from
  `holosophos/holosophos/prompts/system.yaml` by `load_manager_prompt()`.
  *Sub-agent system prompts (from `.claude/agents/<name>.md`) are not yet
  captured by the fork — only the manager's is visible.*
- LLM generations under `claude.llm.*`
- sub-agent invocations (from the `Task` tool) appear as nested chain runs
  with their own LLM/tool spans

If you can't find the manager's `system_prompt` in a Langfuse trace, look at
the **root** `claude.conversation` span's `Input` field, key `system` — it is
not duplicated into individual LLM-call message arrays.

## Session ids

- `langfuse_session_id` — grouping key in `trace_sessions`
- `workspace_session_id` — runtime id, also the folder name under
  `workdir/<session_id>/`

For an interactive run both ids are equal.

For benchmark / eval runs (CodeArkt path):
- one shared `langfuse_session_id` per benchmark run, e.g.
  `benchmark__gaia__validation__20260315T201500Z`
- per-question `workspace_session_id`, e.g. `…__q001`, `…__q002`

This keeps every benchmark question grouped under one Langfuse session while
each question still has an isolated workdir and its own JSONL output row.

Trace metadata also records:
`workspace_session_id`, `langfuse_session_id`, benchmark fields
(`benchmark_name`, `benchmark_split`, `benchmark_nrows`), tool error counters
(`tool_error_count`, `last_tool_error`).

## Error semantics

Tool failures surface at three levels:
- the tool span gets `level=ERROR`
- the enclosing step span gets `level=ERROR`
- the parent trace metadata records aggregate counters

Both paths use the same convention so dashboards work uniformly.

## Access

UI:

```bash
open http://localhost:3051   # v3 (active — all new traces)
open http://localhost:3050   # v2 (read-only archive of historical CodeArkt traces)
```

> The `psql` recipe below queries the **v2** Postgres (`langfuse-db`) — the
> historical CodeArkt archive. New traces now live in **v3**, which stores them
> in ClickHouse (not this Postgres); browse those in the v3 UI (`:3051`).

Postgres shell (from `Holosophus/`):

```bash
docker compose -f docker-compose.mcp.yml exec -T langfuse-db \
  psql -U ${LANGFUSE_POSTGRES_USER:-postgres} -d ${LANGFUSE_POSTGRES_DB:-postgres}
```

Useful queries:

```sql
-- Latest trace sessions
SELECT id, project_id, created_at, updated_at
FROM trace_sessions
ORDER BY updated_at DESC
LIMIT 20;

-- Latest traces
SELECT session_id, name, timestamp, metadata
FROM traces
ORDER BY timestamp DESC
LIMIT 20;

-- Observations for one trace
SELECT trace_id, name, type, model, status_message, start_time
FROM observations
WHERE trace_id = '<trace_id>'
ORDER BY start_time ASC;

-- All traces in a benchmark session
SELECT session_id, COUNT(*) AS traces_cnt,
       MIN(timestamp) AS first_ts, MAX(timestamp) AS last_ts
FROM traces
WHERE session_id LIKE 'benchmark__%'
GROUP BY session_id
ORDER BY last_ts DESC;
```

Key relations:
`projects.id → traces.project_id`,
`projects.id → observations.project_id`,
`(trace_sessions.id, trace_sessions.project_id) → (traces.session_id, traces.project_id)`,
`traces.id → observations.trace_id`.

## Interactive CLI path

When you run `claude` interactively as the orchestrator (via
`make research-console` / `scripts/research_console.sh`, see `README.md`), there
is no Python process wrapping the session, so `langfuse_tracer.py` cannot see it.
Tracing is instead driven by one switch, `HOLO_CC_TRACING` (`.env`; shell env
overrides; default `hook`):

| Value | Path | What happens |
|---|---|---|
| `hook` | **A** (default) | `.claude/settings.json` `Stop`/`SessionEnd` hooks run `scripts/cc_langfuse_export.py`, which re-parses the session JSONL and ships a nested trace via REST ingestion. |
| `otel` | **B** | The launcher exports Claude Code's beta OTel trace env; Claude Code streams `claude_code.*` spans straight to Langfuse OTLP. |
| `both` | A + B | Belt-and-suspenders (two trace shapes coexist). |
| `off` | — | No tracing. |

Both paths target **Langfuse v3 @ `:3051`** (`LANGFUSE_*_KEY_V3`) — the same
instance the SDK path and (since the CodeArkt repoint) the CodeArkt path now use.
The legacy v2 @ `:3050` no longer receives new traces; it is kept read-only for
historical CodeArkt traces. Consolidating **new** traces onto v3 is done;
migrating the old v2 data is deliberately out of scope
(`docs/plans/2026-06-21-claude-code-langfuse.md`, FU-1).

### Path A trace shape (the default)

`scripts/cc_langfuse_export.py` reuses the proven parser in
`scripts/cc_trace_lib.py` (shared with `langfuse_tracer.py`), so an interactive
trace looks like the SDK path:

```
TRACE  (tags: claude-code, interactive; sessionId = Claude Code session id)
  ├── GENERATION manager:step-N        (model, reasoning, usage incl. cache)
  │     └── SPAN tool:Agent            (the delegation call)
  │           └── SPAN agent:<type>    (linked via subagents/<id>.meta.json toolUseId)
  │                 ├── GENERATION <type>:step-M
  │                 │     └── SPAN tool:<name>   (level=ERROR on failure)
  │                 └── ...
  └── ...
```

Key properties:

- **Source of truth** is the on-disk transcript at
  `~/.claude/projects/<slug>/<session_id>.jsonl` plus
  `<session_id>/subagents/agent-<id>.jsonl` (+ `.meta.json`). The slug is the
  absolute cwd with `/`, `_` and `.` all replaced by `-`.
- **Idempotent.** Every observation id is `uuid5(session_id + kind + key)`, so
  the `Stop` hook firing once per turn upserts rather than duplicating.
- **Cost** is $0 in the Langfuse cost field (it can't price Claude models —
  `problems.md` §15); token totals (cache-inclusive) live in trace metadata.
- **Manual backfill / no-new-session:**
  `python scripts/cc_langfuse_export.py <session-id|prefix|latest>`.

The exporter self-gates: it is a no-op unless `HOLO_CC_TRACING ∈ {hook, both}`
and the v3 keys are present, and it never raises (a hook must not block the CLI).

## Local session viewer (Claude Code path only)

`scripts/view_session.py` reads raw Claude Code session logs from
`~/.claude/projects/` and prints agent delegations, MCP tool calls, token
usage, cache efficiency and timing. Useful when Langfuse is down or when you
want a flat textual transcript instead of nested spans.

```bash
python scripts/view_session.py --list
python scripts/view_session.py "multi agent"
python scripts/view_session.py <session-id>
```
