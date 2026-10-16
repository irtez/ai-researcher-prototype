# Holosophus Current State

This file is a navigational checklist — a pointer map to canonical docs, not a copy of them.
Read the linked files for the actual facts. Update this file whenever a canonical doc is added or removed.

## System Components

| Component | Status | Canonical Source |
|---|---|---|
| Docker stack (services, ports, images) | Active | [`architecture.md`](architecture.md) — service map table |
| Local Claude Code + UI deployment | Configured | [`operations/claude-local.md`](operations/claude-local.md) |
| Registered chemistry datasets and persistent snapshots | Integrated; SQL/RDKit and Docker recreation checked without LLM | [`operations/chemistry-datasets.md`](operations/chemistry-datasets.md) |
| Agent model (manager + specialists) | Active | [`architecture.md`](architecture.md) — Agent Model section |
| Tool routing and allowlists | Active | [`architecture.md`](architecture.md) + `holosophos/holosophos/settings.py` |
| Session / workspace layout | Active | [`data-topology.md`](data-topology.md) |
| Langfuse observability (v2 + v3) | Active | [`observability.md`](observability.md) |
| Known bugs and workarounds | Rolling | [`problems.md`](problems.md) |
| Agent continuation, bilingual papers and scoped review | October fixes; offline verification | [`operations/smoke-reliability.md`](operations/smoke-reliability.md) |
| Architecture decisions (why) | Rolling | [`decisions/README.md`](decisions/README.md) |

## Orchestrators

| Orchestrator | Entry Point | Prompt Files |
|---|---|---|
| CodeArkt | `docker compose up --build -d` → `codearkt.terminal/gradio` | `holosophos/holosophos/prompts/*.yaml` |
| Claude Code | `scripts/run_research.py` or `claude` CLI | `.claude/agents/*.md` |

Both hit the same MCP toolservers and share `workdir/<session_id>/` artifact layout.

## Specialist Agents

| Agent | CodeArkt Definition | Claude Code Definition | Eval |
|---|---|---|---|
| manager | `holosophos/agents/` + `prompts/system.yaml` | `scripts/run_research.py` | — |
| librarian | `agents/librarian.py` + `prompts/librarian.yaml` | `.claude/agents/librarian.md` | `run_librarian_eval.py` |
| proposer | `agents/proposer.py` + `prompts/proposer.yaml` | `.claude/agents/proposer.md` | — |
| mle_solver | `agents/mle_solver.py` + `prompts/mle_solver.yaml` | `.claude/agents/mle-solver.md` | — |
| writer | `agents/writer.py` + `prompts/writer.yaml` | `.claude/agents/writer.md` | `benchmark/` + `scripts/run_writer_pack.py` |
| reviewer | `agents/reviewer.py` + `prompts/reviewer.yaml` | `.claude/agents/reviewer.md` | `reviewer_patches/` |

## Writer Agent State

The writer is the most heavily documented specialist. Its current contract lives in:
- [`tools/writer/README.md`](tools/writer/README.md) — Writer MCP lifecycle, agent exposure, service operation, and tests
- [`writer-skills-workflow.md`](writer-skills-workflow.md) — explains how the new Writer Agent skills work, how they relate to each other, and how the agent calls them
- [`writer-agent-how-it-works.md`](writer-agent-how-it-works.md) — detailed description of the architecture, operational principles, contracts, and tools of the agent
- [`writer_agent_input_output_examples.md`](writer_agent_input_output_examples.md) — inputs, outputs, tools, tool reference
- [`manager_to_writer_handoff.md`](manager_to_writer_handoff.md) — how the manager passes context, workdir layout
- [`run_writer_benchmark_end_to_end.md`](run_writer_benchmark_end_to_end.md) — full benchmark run guide
- [`run_writer_pack_scripts.md`](run_writer_pack_scripts.md) — CLI usage for `run_writer_pack.py` / `run_writer_packs.py`
- [`run_writer_on_distilled_benchmark.md`](run_writer_on_distilled_benchmark.md) — distilled benchmark specifics
- [`benchmark_metadata_registry.md`](benchmark_metadata_registry.md) — metadata registry schema

Writer benchmark metrics are tracked in `holosophos/evals_and_reports/AGENT_RESULTS.md`.

## MCP Servers

| Server | Port | Role | Canonical Source |
|---|---|---|---|
| academia | 5056 | Literature search, PDF/LaTeX, proposals | `architecture.md` |
| mle_kit | 5057 | bash, text_editor, GPU, LLM proxy | `architecture.md` |
| writer-tools | 5059 | Deterministic Writer checks and persisted check state | `tools/writer/README.md` |
| paperbanana | 5060 | Diagram and plot generation | `architecture.md` |

## Configuration Sources (Resolution Order)

`.env` → `docker-compose.yml` defaults → `holosophos/holosophos/settings.py` defaults

See [`data-topology.md`](data-topology.md) for the full resolution chain, including the `MODEL_NAME` divergence between compose and settings.

## When To Update This File

Update `current-state.md` when:
- A new canonical doc is added to `docs/`
- An existing canonical doc is deleted or renamed
- A new agent role or orchestrator is added
- The writer contract doc list changes

Do **not** copy content from the linked files here. This file is navigation only.
