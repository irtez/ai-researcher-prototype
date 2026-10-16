# Holosophus Docs

Start here when working on the integrated Holosophus stack. The repo-level
developer harness lives in [`../CLAUDE.md`](../CLAUDE.md); this index is its
router. Why the docs are shaped the way they are → [`README.md`](README.md).

Don't read the whole tree. Find your task below and open the one doc it points to.

## What you're doing → what to read

| You're about to… | Open |
| --- | --- |
| Onboard / get the lay of the land | [`../CLAUDE.md`](../CLAUDE.md), then this index |
| Understand how the docs are organized (genres, mutability) | [`README.md`](README.md) |
| Understand the runtime architecture & services | [`architecture.md`](architecture.md) |
| Use the standalone research console | [`operations/console-prototype.md`](operations/console-prototype.md) |
| Work with / run the web UI | [`ui-notebook.md`](ui-notebook.md) |
| Start the local Claude Code + UI stack | [`operations/claude-local.md`](operations/claude-local.md) |
| Configure native Claude Bash execution in Docker | [`operations/docker-bash.md`](operations/docker-bash.md) |
| Understand experiment summaries and their iteration history | [`operations/experiment-summaries.md`](operations/experiment-summaries.md) |
| Use the chemistry profile and upload input files | [`operations/chemistry-local.md`](operations/chemistry-local.md) |
| Register and prepare local chemistry datasets | [`operations/chemistry-datasets.md`](operations/chemistry-datasets.md) |
| Configure model connections and effort | [`operations/model-connections.md`](operations/model-connections.md) |
| Run chemistry programs on remote compute | [`operations/chemistry-remote.md`](operations/chemistry-remote.md) |
| Approve research stages and revise a completed result | [`operations/approvals-and-feedback.md`](operations/approvals-and-feedback.md) |
| Inspect server-owned iteration history and result snapshots | [`operations/research-iterations.md`](operations/research-iterations.md) |
| Find where sessions / traces / workspaces / config live | [`data-topology.md`](data-topology.md) |
| Configure Semantic Scholar retries and bibliography checks | [`operations/semantic-scholar.md`](operations/semantic-scholar.md) |
| Work with Langfuse / observability | [`observability.md`](observability.md) |
| Touch SDK / MCP / tracing code | [`problems.md`](problems.md) (gotchas first) |
| Learn the rules before changing code | [`agent_rules/`](agent_rules/) (anti-patterns, backend, testing) |
| Understand **why** a choice was made | [`decisions/`](decisions/) (ADR index below) |
| Plan a non-trivial change | skill `create-spec` → [`templates/system_design.md`](templates/system_design.md) |
| Check a spec is ready to Approve | skill `review-spec` (or the `dev-spec-reviewer` agent) |
| Record a decision | [`templates/decision_record.md`](templates/decision_record.md) → `decisions/` |
| Debug / investigate something | [`templates/investigation.md`](templates/investigation.md) |
| Edit docs, before you commit | skill `check-docs` (mechanical) / `docs-review` (drift+sprawl) |
| See which harness practices are adopted | [`adoption_status.md`](adoption_status.md) |
| Operate cloud.ru ML Space | [`operations/cloudru-mlspace.md`](operations/cloudru-mlspace.md) |

## Suggested first read (new to the repo)

`architecture.md` → `data-topology.md` → `observability.md` →
`agent_rules/anti-patterns.md` → `agent_rules/testing.md`.

## Core Docs

- [ui-notebook.md](ui-notebook.md) - modern notebook UI, local dev, Docker profile, and validation commands.

- [architecture.md](architecture.md) — runtime architecture, services, agent configuration, critical flows, high-risk areas.
- [data-topology.md](data-topology.md) — sessions, traces, workspaces, benchmark outputs, config resolution.
- [observability.md](observability.md) — Langfuse model for both orchestration paths (CodeArkt + Claude Code), schema, and access patterns.
- [problems.md](problems.md) — known SDK / MCP / Langfuse gotchas, workarounds, and open follow-ups. Read before touching SDK or tracing code.
- [adoption_status.md](adoption_status.md) — self-describing map of which agent-harness practices are adopted, and how far (template: [templates/adoption_status.md](templates/adoption_status.md)).

## Agent Rules

- [agent_rules/anti-patterns.md](agent_rules/anti-patterns.md) — living log of project-specific rules an agent must not break.
- [agent_rules/testing.md](agent_rules/testing.md) — test surfaces, decision framework, exit criteria by change type.
- [agent_rules/backend.md](agent_rules/backend.md) — coding style, commits, vendored repos, tool routing, Docker security.

## Decisions

- [decisions/README.md](decisions/README.md) — ADR format and when to write one.
- [decisions/0001-two-orchestrators.md](decisions/0001-two-orchestrators.md) — keep CodeArkt and Claude Code as first-class.
- [decisions/0002-canonical-yaml-prompts.md](decisions/0002-canonical-yaml-prompts.md) — canonical YAML schema for agent prompts.
- [decisions/0003-single-langfuse-observability.md](decisions/0003-single-langfuse-observability.md) — Langfuse-only observability stack (Phoenix dropped).
- [decisions/0004-evidence-based-prompt-minimalism.md](decisions/0004-evidence-based-prompt-minimalism.md) — descriptive minimal prompts, every change gated on an eval delta.
- [decisions/0005-vendored-monorepo.md](decisions/0005-vendored-monorepo.md) — `codearkt` and `mle_kit` vendored into Holosophus.
- [decisions/0006-cloudru-provider.md](decisions/0006-cloudru-provider.md) — cloud.ru ML Space as a second remote-GPU provider alongside Vast.ai.
- [decisions/0007-adopt-harness-practices.md](decisions/0007-adopt-harness-practices.md) — adopt the remaining agent-engineering harness delta from plate_ai, adapted to a research-agent project.

## Cloud providers

- [cloudru-mlspace.md](cloudru-mlspace.md) — living notes on cloud.ru ML Space access, quirks, and Holosophus integration.

## Task Templates

- [templates/system_design.md](templates/system_design.md) — for planning a new feature or architectural change (status-gated spec).
- [templates/implementation.md](templates/implementation.md) — for tracking implementation progress.
- [templates/investigation.md](templates/investigation.md) — for debugging or investigating issues.
- [templates/decision_record.md](templates/decision_record.md) — the ADR skeleton; copy to `decisions/NNNN-slug.md`.
- [templates/adoption_status.md](templates/adoption_status.md) — skeleton for the harness-adoption tracker.

## Harness Skills

Developer-harness skills (invoke by name; they drive the practices above):

- `create-spec` — start/resume a status-gated system-design spec.
- `review-spec` — readiness gate before a spec is flipped to `Approved` (also the `dev-spec-reviewer` agent).
- `check-docs` — mechanical docs gate (`scripts/check-docs.sh`): placeholder leaks, missing canon docs, trailing whitespace.
- `docs-review` — read-only audit for doc drift (doc vs reality) and sprawl (duplicated facts).
- `postprocess-traces` — Langfuse trace polishing (`scripts/polish_traces.py`).

## Benchmark Runners

- [`holosophos/reports/README.md`](../holosophos/reports/README.md) — benchmark runner documentation.
- [`holosophos/reports/results.md`](../holosophos/reports/results.md) — recorded benchmark snapshots.

## Sibling Projects

Rarely needed, in `../`:
- `reviewer_mcp/` — standalone MCP code, independent from Holosophus

## Writer Agent Skills and Workflow Documentation

- [tools/writer/README.md](tools/writer/README.md) — how the Writer MCP tools are exposed, run, coordinated, and tested.
- [writer-skills-workflow.md](writer-skills-workflow.md) — explains how the new Writer Agent skills work, how they relate to each other, and how the agent calls them.
- [writer-agent-how-it-works.md](writer-agent-how-it-works.md) — detailed description of the architecture, operational principles, contracts, and tools of the agent.
- [tools/writer/section_order.md](tools/writer/section_order.md) — deterministic LaTeX section-order check and MCP contract.
- [tools/writer/content_placement.md](tools/writer/content_placement.md) — artifact-placement policy, include safety, and MCP contract.
- [tools/writer/paper_length.md](tools/writer/paper_length.md) — rendered PDF section and main-content page measurements.

## Current State

- [current-state.md](current-state.md) — navigational checklist: which docs are canonical, which agents exist, writer contract pointer map.

## When To Update Docs

Update the matching doc in the **same change** as the code:

| What changed | Update |
|---|---|
| Docker service, port, image, volume | `architecture.md` service map |
| Agent tool allowlist (`settings.py`) | `architecture.md` agent table; if writer-related also `writer_agent_input_output_examples.md` |
| Prompt file (`prompts/*.yaml`, `.claude/agents/*.md`) | Corresponding agent docs; `manager_to_writer_handoff.md` if writer |
| Session or workdir layout | `data-topology.md` |
| Langfuse schema or tracing | `observability.md` |
| Eval runner, benchmark script, metadata schema | `holosophos/evals_and_reports/README.md`; writer-specific: `run_writer_benchmark_end_to_end.md`, `benchmark_metadata_registry.md` |
| Bug or gotcha discovered | `problems.md` |
| Non-obvious architectural decision | New ADR in `decisions/` following `decisions/README.md` |
| New doc added or doc removed | `current-state.md` pointer map |

## Rules of Use

- Do not load all docs by default. Start with the most relevant doc.
- Prefer small factual docs over one giant knowledge base.
- Add a new doc or rule when repeated mistakes show that an invariant is too implicit.
