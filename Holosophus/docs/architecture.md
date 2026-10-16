# Holosophus Architecture

This document describes the current runtime shape of the integrated Holosophus stack.

## Scope

`Holosophus/` is the integration layer around several vendored components:
- `holosophos/`: upstream app repo and the canonical Holosophos Python package
- `codearkt/`: agent runtime, CodeAct loop, server/client protocol, executor
- `mle_kit_mcp/`: experiment and analysis tools
- `academia_mcp`: consumed as an image/service in Docker Compose
- `paperbanana-mcp`: figure/diagram generation service in Docker Compose
- `writer-tools`: deterministic Writer analysis and quality-loop coordination

The local Claude Code + UI stack is started with `make up-claude` and
`make cc-runner`; see [the deployment guide](operations/claude-local.md).
`docker-compose.claude.yml` reuses the MCP and UI service definitions, with
its own project/volumes and loopback Docker ports. It starts `ui` (5062),
`ui-api` (5058 internal), the four MCP services (5056/5057/5059/5060), and
Langfuse v3 (3051) with its database, ClickHouse, Redis and MinIO, and
Phoenix (6007) with its own persistent volume. The host
runner (5061) uses Claude Agent SDK and the host CLI. The table below also
describes components retained for the older `docker-compose.yml` deployment.

The optional chemistry profile adds `chem` (5063) and `chem_conversions` (5064).
The UI fixes `general` / `chemistry` for each started session. The shared
`research_session.py` handles metadata and uploaded originals; HTTP and CLI
runners assemble chemistry prompts and tool configuration in memory using
`scripts/research_profiles.py`. Shared YAML and generated general agents are
not rewritten by chemistry runs. The manager and five specialist roles remain;
the remote experimenter is excluded in chemistry. See
[chemistry operations](operations/chemistry-local.md) for launch and file details.

For both profiles, `cc_research_lib.py` initializes the existing LangSmith/OTel
bridge for CLI and UI. With `ENABLE_PHOENIX=true`, `phoenix_tracing.py` adds a
second processor to the same provider. Phoenix receives an OpenInference copy
of each span, preserving identifiers and parents; Langfuse export remains in
place. See [observability](observability.md#phoenix-for-the-claude-code-sdk-path).

## System Map

| Service | Port | Image / Build | Health | Role |
| --- | --- | --- | --- | --- |
| `app` | 5055 | `holosophos/Dockerfile` (built) | depends on all below | Holosophos HTTP server + manager agent |
| `executor` | 8000 (internal) | `codearkt/container/Dockerfile` (built) | socket check | Python sandbox for `<execute>` blocks |
| `academia` | 5056 | `phoenix120/academia_mcp:1.11.3` (pulled) | socket check | Literature and paper MCP tools |
| `mle_kit` | 5057 | `mle_kit_mcp/Dockerfile` (built) | socket check | Experiment/ML MCP tools |
| `writer-tools` | 5059 | `holosophos/Dockerfile` (built) | socket check | Writer analyzers and persistent quality-check state |
| `paperbanana-mcp` | 5060 | `docker/paperbanana-mcp.Dockerfile` (built) | socket check | Figure/diagram generation |
| `langfuse-server` | 3000 | `langfuse/langfuse:2.95` (pulled) | via langfuse-db | Legacy **v2** — read-only archive of historical CodeArkt traces. Active backend is Langfuse **v3** (`docker-compose.mcp.yml`, `:3051`); see `docs/observability.md` |
| `langfuse-db` | internal | `postgres:17` (pulled) | `pg_isready` | Langfuse database |
| `phoenix` (Claude stack) | 6007 | `arizephoenix/phoenix:version-20.8.0` | HTTP | Optional second destination for Claude Code SDK spans, alongside Langfuse |
| `phoenix` (legacy stack) | 6006 | `arizephoenix/phoenix` | — | Separate historical deployment; not started by the Claude stack |

## Agent Model

Holosophos uses a CodeAct-style loop implemented by CodeArkt:
- the agent reasons about the next action
- emits Python inside `<execute>...</execute>`
- the executor runs that Python
- tool outputs become the next observation

`CodeAct` is the interaction pattern.
`CodeArkt` is the runtime implementation used in this repository.

The manager orchestrator can delegate to specialist agents such as:
- `librarian`
- `mle_solver`
- `writer`
- `proposer`
- `reviewer`

Delegation is exposed to the manager as callable `agent__*` tools.

### Agent Configuration

Source of truth: `holosophos/holosophos/settings.py`

| Agent | Max Iterations | Planning Interval | Tools | Key Capabilities |
| --- | --- | --- | --- | --- |
| manager | 100 | 9 | 4 + `agent__*` | bash, text_editor, describe_image, speech_to_text |
| librarian | 150 | 9 | 15 | arxiv_search, s2_*, document_qa, web_search, yt_transcript |
| mle_solver | 200 | 14 | 7 (local) / 8 (remote) | bash, text_editor, llm_proxy, hf_datasets_search |
| writer | 100 | 9 | 15 | compile_latex, figure generation, PDF reading, deterministic paper checks |
| proposer | 200 | 9 | 7 | generate_research_proposals, score_research_proposals |
| reviewer | 50 | 9 | 7 | review_pdf_paper, download_pdf_paper |

MLE solver can optionally use a different model (`MLE_SOLVER_MODEL_NAME`) and run remotely (`MLE_SOLVER_IS_REMOTE`). Two remote providers are supported, selected by `REMOTE_PROVIDER` env (vast | cloudru): Vast.ai (default; dynamic instance lifecycle via `vastai_sdk`) and cloud.ru ML Space (static SSH endpoint, no SDK). cloud.ru wiring lives in the standard `docker-compose.yml` / `docker-compose.mcp.yml` — set `REMOTE_PROVIDER=cloudru` and `CLOUDRU_*` env to switch; the SSH key bind falls back to `/dev/null` when `CLOUDRU_SSH_KEY_HOST_PATH` is unset, so Vast users are unaffected. See [ADR-0006](decisions/0006-cloudru-provider.md) and [`cloudru-mlspace.md`](cloudru-mlspace.md).

`MLE_SOLVER_IS_REMOTE` is now respected by both orchestrators symmetrically: in CodeArkt it picks one of two YAML/tool-list pairs (as before); in Claude Code, `scripts/sync_claude_agents.py` filters which of `mle-solver` / `mle-solver-remote` are rendered into `.claude/agents/`. Unset = both rendered (default); `true` = only remote; `false` = only local. Used to isolate parallel heavy/light research sessions.

## Tool Routing

Tool routing has two layers:

1. MCP services are mounted centrally in the app composition.
2. Each agent receives only an explicit allowlist of tool names.

This means a tool can exist in the stack globally but still be unavailable to most agents.

Examples:
- `writer` is the main consumer of PaperBanana tools.
- `writer` receives all five `writer_tools` lifecycle and analyzer tools. Other
  agents can opt in later by adding those names to their allowlists.
- `manager` is orchestration-first and should stay relatively narrow.

The `writer_tools` MCP server publishes `start_writer_check`,
`section_order`, `content_placement`, `paper_length`, and
`finish_writer_check`. CodeArkt removes the MCP namespace for agent calls and
injects the active `session_id` and `agent_name` where the schema accepts
them. Direct MCP clients provide that identity explicitly; the integrated Claude
runner binds the session ID. `start_writer_check` registers the article paths
shared with Academia review and UI through the dedicated `paper_registry_state`
volume. This document metadata is independent of research-run completion.

## Dependency Rules

- `app` depends on: `executor` (healthy), `academia` (healthy), `mle_kit` (healthy), `writer-tools` (healthy), `paperbanana-mcp` (healthy), `phoenix` (started), `langfuse-server` (started).
- `langfuse-server` depends on: `langfuse-db` (healthy).
- `executor` runs read-only with `cap_drop: ALL`, `no-new-privileges`, tmpfs (`/tmp` 64MB, `/run` 16MB), capped at 0.5 CPU / 1GB RAM / 256 PIDs.
- `mle_kit` mounts the Docker socket (`/var/run/docker.sock`) for remote GPU execution via Vast.ai.
- All MCP services and `app` share the `./workdir` volume.
- `writer-tools` mounts `./workdir` read-only and stores coordination state in
  the `writer_tools_state` volume. Its HTTP endpoint is intended only for the
  trusted local Compose network or loopback; do not expose it publicly.

## Execution Flow

For the main interactive app:

1. A client connects to `app` on port `5055`.
2. The app composes the manager agent.
3. The manager runs the CodeAct loop.
4. `<execute>` code uses the executor and can call MCP tools or subagents.
5. Outputs are written into `workdir/<session_id>/`.
6. Traces, spans, generations, and tool events are written to Langfuse.

When the Writer finishes a draft, it starts a server-owned quality-check run,
calls all three analyzers for the same pass, repairs reported problems, and
repeats until clean or the quality-rerun limit is exhausted. Only a pass in
which all three analyzers succeed advances the quality counter. MCP transport
failures and analyzer failures use a separate per-tool retry counter. The
Writer finishes the run as `clean`, `blocked`, or `abandoned`.

For benchmarks in `Holosophus/holosophos/reports/`:

1. A runner script constructs the same app-side agent composition in-process.
2. It still uses the real CodeArkt runtime and MCP tools.
3. Benchmark questions are grouped into one Langfuse benchmark session.
4. Each question still gets its own isolated workspace session id.

`run_gaia.py` is therefore not a mock path. It is a real manager run on a reduced specialist set.

## Critical Flows

### Interactive flow

```
client → POST /agents/manager (codearkt.server)
  → holosophos.server.server() → compose_main_agent()
    → CodeActAgent.ainvoke() (CodeAct loop)
      → LLM call (OpenRouter via codearkt.llm)
      → <execute> block → PythonExecutor → Docker sandbox
      → MCP tool calls (academia / mle_kit / paperbanana / writer_tools)
      → agent__* delegation to sub-agents
    → workdir/<session_id>/ (artifacts)
    → Langfuse (traces, spans, generations)
```

Key files: `holosophos/server.py` → `main_agent.py` → `codearkt/codeact.py` → `codearkt/python_executor.py`

### Benchmark flow

```
reports/run_*.py → compose_main_agent(included_agents=[...])
  → codearkt.server.run_batch() (in-process, not via HTTP)
  → same CodeAct loop as interactive
  → predictions.jsonl output
```

GAIA uses a reduced specialist set: `manager` + `librarian` + `mle_solver`.

## High-Risk Areas

- **`holosophos/langfuse_openai_patch.py`** (918 lines) — monkey-patches OpenAI client and CodeAct runtime for observability. Changes here affect all tracing for both interactive and benchmark runs.
- **Tool allowlists in `settings.py`** — a typo in a tool name silently drops the tool from the agent's capabilities. No error is raised.
- **`MCP_CONFIG` in `main_agent.py`** — single point of failure for all external tool access. An incorrect URL or missing service silently breaks tools.
- **Executor container** — read-only sandbox with strict limits. Changing container config can break tool execution in ways that surface as confusing Python errors.

## Local Entry Points

```bash
# Server
python -m holosophos.server --port 5055 --enable-phoenix

# CLI client
cd codearkt && uv run -m codearkt.terminal --url http://127.0.0.1:5055

# Gradio client
cd codearkt && uv run -m codearkt.gradio --url http://127.0.0.1:5055

# Direct agent query (no server)
python -m holosophos.main_agent --query "your task"

# Writer MCP lifecycle smoke test (service must be running)
python scripts/smoke_test_writer_tools_mcp.py --phase all

# Benchmark
cd holosophos && uv run python reports/run_gaia.py --nrows=10 --enable_langfuse=true
```
