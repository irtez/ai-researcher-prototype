# Holosophos — Technical Report: Architecture and Execution

## Abstract
Holosophos is an autonomous research assistant built on a multi-agent architecture using the CodeAct interaction pattern and the CodeArkt runtime, together with an MCP stack around Academia MCP, MLE Kit MCP, and PaperBanana MCP. This report explains how the system works end-to-end: request lifecycle, agent composition, tool execution (local and remote), configuration surfaces, Langfuse-based observability, deployment modes, and extensibility patterns. It is intended for developers and operators who need a practical understanding of the current codebase and runtime behavior.

## 1. System Overview
- **Interaction model**: CodeAct is the agent loop and prompting contract; CodeArkt is the concrete runtime that implements this loop, hosts the server, and executes tool-facing Python blocks.
- **Core runtime**: CodeArkt (agent orchestration, tool execution, HTTP server, batch execution).
- **Agents**: Manager agent orchestrates a team of specialists: `librarian`, `mle_solver`, `writer`, `proposer`, `reviewer`.
- **Tools**: Functions callable from agents' code blocks. A large portion comes from external MCP servers: Academia (papers/web), MLE Kit (execution/files/LLM proxy), and PaperBanana (figure generation and evaluation).
- **Observability**: Langfuse is the primary observability backend. It records agent runs, LLM calls, tool spans, and ties them to `session_id`.
- **Workspace**: `workdir/` is shared across services. In practice, each run is isolated under `workdir/<session_id>/`.

## 2. Components and Responsibilities
- **Server (`holosophos/holosophos/server.py`)**
  - Configures logging
  - Installs the Langfuse/OpenAI patch used by CodeArkt
  - Composes the manager agent with specialists
  - Starts the CodeArkt HTTP server
- **Manager agent (`holosophos/holosophos/main_agent.py`)**
  - Loads the system prompt (`prompts/system.yaml`)
  - Holds references to specialist agents and their tool allow-lists
  - Uses `MCP_CONFIG` to reach external MCP servers
- **Specialist agents (`holosophos/holosophos/agents/*.py`)**
  - `librarian`: literature search and document analysis
  - `mle_solver`: code authoring and execution (local or remote GPU)
  - `writer`: LaTeX authoring, compilation, and figure generation
  - `proposer`: research idea generation and scoring
  - `reviewer`: paper review with venue-style criteria
- **Prompts (`holosophos/holosophos/prompts/*.yaml`)**
  - Define behavioral contracts for Thought -> Code `<execute>` -> Observation -> Final Answer
  - Treat `<execute>...</execute>` as an executable Python block, not a declarative tool-only wrapper
  - Enforce agent-specific policies and pipelines
- **MCP servers**
  - `academia_mcp`: ArXiv/ACL Anthology/Semantic Scholar/web tools
  - `mle_kit_mcp`: local/remote execution tools, file operations, LLM proxy
  - `paperbanana`: diagram/plot generation and figure evaluation
- **Executor container (Compose)**
  - Provides the CodeArkt execution backend used by the app for tool execution

## 3. Request Lifecycle (End-to-End)
1) A client (Gradio UI or terminal) sends a query to the Holosophos HTTP server (port `5055` by default).
2) The CodeArkt server invokes the manager agent configured in `server.py`.
3) The manager agent plans the solution and delegates subtasks to specialists using their prompts and allowed tools.
4) Each specialist iterates through CodeArkt's loop:
   - Thought (reasoning)
   - Code in `<execute>...</execute>` as executable Python, typically used to call tools, managed agents, and lightweight glue logic
   - Observation (tool outputs, `print()` output, or last-expression results)
   - Repeat until completion; then `<final_answer>`
5) Tool calls may be local (for example `bash`, `text_editor`) or MCP-backed over HTTP. `MCP_CONFIG` wires the manager to Academia, MLE Kit, and PaperBanana.
6) Langfuse instrumentation tags the run with `session_id`, records agent/LLM/tool activity, and keeps the workspace aligned with the same session.
7) The manager aggregates results and returns the final answer back to the client.

## 4. Key Code Paths
- **Server entrypoint**: `holosophos/holosophos/server.py`
  - Parses runtime parameters (model, tokens, port, optional `mle_solver_model_name`)
  - Installs Langfuse instrumentation for CodeArkt
  - Calls `compose_main_agent(...)`
  - Runs `run_server(agent, MCP_CONFIG, add_mcp_server_prefixes=False, port=port)`

```python
def server(
    model_name: str = settings.MODEL_NAME,
    verbosity_level: int = settings.VERBOSITY_LEVEL,
    max_completion_tokens: int = settings.MAX_COMPLETION_TOKENS,
    max_history_tokens: int = settings.MAX_HISTORY_TOKENS,
    port: int = settings.PORT,
    mle_solver_model_name: str | None = None,
) -> Any:
    configure_uvicorn_style_logging(verbosity_level)
    patch_codearkt_openai_for_langfuse()
    agent = compose_main_agent(
        model_name=model_name,
        verbosity_level=verbosity_level,
        max_completion_tokens=max_completion_tokens,
        max_history_tokens=max_history_tokens,
        mle_solver_model_name=mle_solver_model_name,
    )
    run_server(agent, MCP_CONFIG, add_mcp_server_prefixes=False, port=port)
```

- **Agent composition**: `holosophos/holosophos/main_agent.py`
  - Builds a shared `LLM` instance for most agents
  - Optionally builds a dedicated `LLM` for `mle_solver`
  - Instantiates each specialist via factory functions in `holosophos/holosophos/agents/`
  - Loads manager prompt `prompts/system.yaml` and creates the manager `CodeActAgent`
  - Defines `MCP_CONFIG` for Academia, MLE Kit, and PaperBanana

```python
MCP_CONFIG = {
    "mcpServers": {
        "academia": {"url": settings.ACADEMIA_MCP_URL, "transport": "streamable-http"},
        "mle_kit": {"url": settings.MLE_KIT_MCP_URL, "transport": "streamable-http"},
        "paperbanana": {
            "url": settings.PAPERBANANA_MCP_URL,
            "transport": "streamable-http",
        },
    }
}

model = LLM(
    model_name=model_name,
    max_completion_tokens=max_completion_tokens,
    max_history_tokens=max_history_tokens,
)

mle_solver_model: LLM = model
if mle_solver_model_name and mle_solver_model_name != model_name:
    mle_solver_model = LLM(
        model_name=mle_solver_model_name,
        max_completion_tokens=max_completion_tokens,
        max_history_tokens=max_history_tokens,
    )
```

- **Specialist agents** (`holosophos/holosophos/agents/*.py`)
  - Each file follows the same factory pattern: load prompt storage, define tool allow-list, and create a `CodeActAgent`
  - The pattern is uniform and not independently complex; the main value is in the tool lists and prompts, not the constructor boilerplate

- **Prompts** (`holosophos/holosophos/prompts/*.yaml`)
  - Manager prompt describes recommended pipelines (proposal generation vs full paper)
  - Specialists enforce domain-specific policies and example usage of tools

```yaml
Recommended pipeline for a proposal generation:
1) librarian (to get overview of the literature)
2) proposer (to generate proposals)
3) librarian (to collect relevant papers for the best proposals)
Avoid calling mle_solver and writer for proposal generation.

Recommended pipeline for a full research paper creation:
1) librarian (to get overview of the literature)
2) proposer (to generate proposals)
3) librarian (to collect relevant papers)
4) mle_solver (to conduct all experiments)
5) writer (to compile a paper)
6) reviewer (to review the paper)
```

- **Settings**: `holosophos/holosophos/settings.py`
  - `BaseSettings` (pydantic-settings) reads `.env`
  - Exposes model names, token budgets, MCP URLs, Langfuse config, tool allow-lists per agent, planning intervals, iteration limits, port, and workspace path
  - Includes PaperBanana and optional `MLE_SOLVER_MODEL_NAME` routing through runtime parameters

## 5. Tooling and MCP Integration
- **CodeArkt tools** are Python functions mapped into the agent execution environment.
- **MCP tools** are accessed via `MCP_CONFIG` and streamable-HTTP. CodeArkt forwards tool calls to the MCP server; the server executes the operation and returns JSON.
- **Academia MCP** provides literature and web tools such as `arxiv_search`, `arxiv_download`, `anthology_search`, `s2_get_citations`, `document_qa`, `web_search`, and `visit_webpage`.
- **MLE Kit MCP** provides execution and workspace tools such as `remote_bash`, `remote_text_editor`, `remote_download`, and `llm_proxy_remote`.
- **PaperBanana MCP** provides writer-oriented figure tools such as `generate_diagram`, `generate_plot`, and `evaluate_diagram`.

## 6. Local vs Remote Code Execution (MLE Solver)
- Local mode (`MLE_SOLVER_IS_REMOTE=false`): tools like `bash`, `text_editor`, `llm_proxy_local`.
- Remote mode (`MLE_SOLVER_IS_REMOTE=true`): tools like `remote_bash`, `remote_text_editor`, `remote_download`, `llm_proxy_remote` via MLE Kit MCP. Suitable for GPU jobs.
- Both modes may still use auxiliary search/inspection tools such as `web_search`, `visit_webpage`, `hf_datasets_search`, and `describe_image`.
- In Compose, `mle_kit` mounts `./workdir` and has Docker socket access to orchestrate containers/jobs (see security considerations).

## 7. Configuration Surface
- `.env` variables override defaults from `holosophos/holosophos/settings.py` (case-insensitive):
  - **Core**: `MODEL_NAME`, `MAX_COMPLETION_TOKENS`, `MAX_HISTORY_TOKENS`, `PORT`, `WORKSPACE_DIR`, `BASE_URL`
  - **Model routing**: `MLE_SOLVER_MODEL_NAME`
  - **MCP**: `ACADEMIA_MCP_URL`, `MLE_KIT_MCP_URL`, `PAPERBANANA_MCP_URL`
  - **Observability**: `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_PROJECT_NAME`
  - **Agent behavior**: planning intervals, iteration caps, tool allow-lists for each agent
  - **MLE Solver mode**: `MLE_SOLVER_IS_REMOTE`
  - **PaperBanana**: `PAPERBANANA_VLM_PROVIDER`, `PAPERBANANA_VLM_MODEL`, `PAPERBANANA_IMAGE_PROVIDER`, `PAPERBANANA_IMAGE_MODEL`, `PAPERBANANA_REFINEMENT_ITERATIONS`

## 8. Workspace and Artifacts
- Shared workspace: `./workdir` (host) <-> `/workdir` (containers)
- In practice, each run is isolated under `workdir/<session_id>/`
- The initial prompt is persisted as `workdir/<session_id>/started_prompt.md`
- Session IDs are generated deterministically by CodeArkt when the client does not provide one, using mode/topic/model-based naming
- Librarian/writer/mle solver read and write files through editor/execution tools; outputs, drafts, figures, and reports accumulate inside the session directory
- Batch/evaluation scripts may copy attached files into `WORKSPACE_DIR` and write outputs such as `predictions.jsonl`

## 9. Observability (Langfuse)
- Langfuse is the primary observability backend and the practical default for this stack.
- The server patches CodeArkt's OpenAI client and execution path so agent runs, LLM generations, and tool execution are captured as Langfuse traces/spans.
- `session_id` is propagated through the run and reused for workspace isolation, making it easier to correlate traces with `workdir/<session_id>/`.
- The `PythonExecutor.ainvoke` path is instrumented so executed code, detected tool calls, and outputs/errors become part of the trace.

## 10. Typical Execution Scenarios
- **Proposal generation**: `librarian -> proposer -> librarian`
  - Librarian surveys literature; Proposer generates and scores proposals; Librarian fetches supporting papers for top proposals.
- **Full paper cycle**: `librarian -> proposer -> librarian -> mle_solver -> writer -> reviewer`
  - Librarian gathers context, Proposer ideates, Librarian collects references, MLE Solver runs experiments, Writer compiles a paper and may generate figures through PaperBanana, Reviewer produces a venue-style review.

## 11. Evaluation and Batch Runs
- `holosophos/reports/run_gaia.py` loads the GAIA dataset, optionally stages attached files into `WORKSPACE_DIR`, composes an agent (typically `librarian + mle_solver`), and runs `run_batch` with configurable concurrency and timeouts.
- Scores are computed by normalizing predicted vs ground-truth answers.

```python
agent = compose_main_agent(
    model_name=model_name,
    verbosity_level=verbosity_level,
    included_agents=("librarian", "mle_solver"),
    max_completion_tokens=max_completion_tokens,
    max_history_tokens=max_history_tokens,
)
predicted_answers = await run_batch(
    tasks,
    agent,
    mcp_config=MCP_CONFIG,
    max_concurrency=max_workers,
    add_mcp_server_prefixes=False,
    task_timeout=7200,
    output_path=Path(predictions_path),
)
```

- Top-level smoke and operational scripts live under `scripts/`, while evaluation scripts specific to the packaged app live under `holosophos/reports/`.

## 12. Deployment Modes
- **Local multi-process**
  - Start Langfuse or point the app to an existing Langfuse instance
  - Start `academia_mcp`, `mle_kit_mcp`, and optionally `paperbanana` if writer figure tools are needed
  - Start `holosophos.server`
  - Use Gradio UI or terminal client from CodeArkt
- **Docker Compose**
  - Core services: `app`, `executor`, `langfuse-server`, `langfuse-db`, `academia`, `mle_kit`, `paperbanana-mcp`
  - Bind mounts `./workdir` to share artifacts
  - Ports are configurable via environment variables; see `docker-compose.yml`

## 13. Security Considerations
- `mle_kit` mounts `/var/run/docker.sock` to orchestrate jobs: this grants control over the host Docker daemon. Run only in trusted environments.
- Treat API keys in `.env` as sensitive; avoid committing `.env` to version control.
- Langfuse credentials and project configuration are operational secrets and should be handled like API credentials.
- Remote execution can launch long-running jobs; ensure resource limits and quotas are enforced.

## 14. Extensibility Patterns
- **Add a new agent**
  - Create `holosophos/holosophos/agents/<name>.py` with a `get_<name>_agent(...)` factory
  - Add `holosophos/holosophos/prompts/<name>.yaml`
  - Wire it in `compose_main_agent(...)` and optionally in `AGENTS`
- **Add/change tools**
  - Update tool allow-lists in `holosophos/holosophos/settings.py`
  - If MCP-backed, expose tools in the MCP server and align schemas
  - If the tool is figure-oriented, decide explicitly whether it belongs to `writer`, `mle_solver`, or manager-level orchestration
- **Customize prompts**
  - Edit YAML under `holosophos/holosophos/prompts/` (manager and specialists)
  - Preserve CodeArkt `<execute>`/`<final_answer>` contract and tool usage rules

## 15. Failure Modes and Mitigations
- **MCP connectivity issues**
  - Verify `ACADEMIA_MCP_URL`, `MLE_KIT_MCP_URL`, and `PAPERBANANA_MCP_URL`
  - Use Compose `logs` for the relevant service
- **Langfuse configuration errors**
  - Verify `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`
  - If the server fails early, check the Langfuse patch initialization path first
- **LLM provider errors**
  - Check `MODEL_NAME`, `MLE_SOLVER_MODEL_NAME`, credentials (`OPENROUTER_API_KEY`, etc.), and rate limits
- **Long-running/failed experiments**
  - MLE Solver tools run blocking by design; inspect outputs in `workdir/<session_id>/` and adjust scripts

## 16. Performance Considerations
- **Token budgets**: `MAX_COMPLETION_TOKENS` and `MAX_HISTORY_TOKENS` control context/output size
- **Batching**: `run_batch` with `max_concurrency` and `task_timeout` controls throughput
- **Iteration caps**: per-agent iteration and planning intervals prevent runaway loops
- **Model routing**: keeping a separate model for `mle_solver` can reduce cost or improve coding quality without changing the rest of the stack

## 17. Source Map (Where Things Live)
- Application package: `holosophos/holosophos/`
- Server: `holosophos/holosophos/server.py`
- Composition: `holosophos/holosophos/main_agent.py`
- Agents: `holosophos/holosophos/agents/*.py`
- Prompts: `holosophos/holosophos/prompts/*.yaml`
- Settings and utilities: `holosophos/holosophos/settings.py`, `holosophos/holosophos/utils.py`, `holosophos/holosophos/files.py`
- Evaluation scripts: `holosophos/reports/`
- Tests: `tests/` and `holosophos/tests/`
- Operational scripts: `scripts/`
- Deployment: `docker-compose.yml`, `Makefile`, `.env_example`

## 18. Appendix — Quick Commands
- Local install
  ```bash
  cd Holosophus/holosophos
  uv venv .venv && source .venv/bin/activate && make install
  ```
- Local run (multi-process)
  ```bash
  cd Holosophus
  uv run -m academia_mcp --port 5056
  uv run -m mle_kit_mcp --port 5057 --workspace workdir
  uv run -m holosophos.server --port 5055
  cd codearkt && uv run -m codearkt.gradio --url http://127.0.0.1:5055
  ```
- Docker Compose
  ```bash
  cd Holosophus
  python scripts/check_env_required.py
  docker compose up --build -d
  docker compose logs -f app
  docker compose down
  ```
