# 0003. Single Langfuse-only observability stack (Phoenix dropped)

- **Status**: Accepted (amended 2026-08-12 — see Update below)
- **Date**: 2026-03-31
- **Deciders**: project lead

## Context

Holosophus initially ran two observability backends side by side: **Arize
Phoenix** (the original CodeArkt default for OpenTelemetry-based agent
traces) and **Langfuse** (added in `1dcda68` Feb 12 for richer LLM/session
analytics). Both stacks consumed the same instrumentation and stored
overlapping data — the only practical difference for our use case was UI and
schema; both showed roughly the same spans for the same runs.

By Mar 2026 the duplication had concrete costs:

- Two services to bring up, monitor and back up; double Postgres state.
- Two patches in the LLM client path (Phoenix OTel exporter + Langfuse
  OpenAI patch in `holosophos/langfuse_openai_patch.py`), increasing the risk
  that one would silently disagree with the other.
- Engineers had to remember which UI to open for which analysis. Most of the
  team converged on Langfuse for sessions/cost; Phoenix stopped being the
  daily driver.
- Benchmark scripts had to gate Phoenix vs Langfuse export per run, adding
  config surface area for a feature nobody used twice in the same week.

Two paths could resolve this: keep both with a clear split of
responsibilities (Phoenix = OTel/system, Langfuse = LLM/session) or pick
one. We picked one.

## Decision

**Langfuse is the single observability backend.** Phoenix and its OTel
export path were removed in `e90ae6a` (Mar 31). All instrumentation now
flows through:

- CodeArkt path: `holosophos/holosophos/langfuse_openai_patch.py` (patches
  `openai.AsyncOpenAI` and the CodeAct loop).
- Claude Code path: `scripts/langsmith_fork/` (forked langsmith → Langfuse
  bridge wrapping `ClaudeSDKClient`).

Both paths report into the same local Langfuse v2 instance at
`http://localhost:3050` (v3 alongside on `:3051`). See `docs/observability.md` for trace shapes,
session-id conventions and Postgres queries.

## Consequences

- One UI, one schema, one Postgres database to operate.
- Trace shape differs between the two orchestrator paths (`manager:run` /
  step spans vs. `claude.conversation` / `claude.llm.*`), but the storage
  layer is unified — dashboards and SQL queries work for both.
- We lose Phoenix's OTel-native ergonomics (e.g. easy export to other OTel
  backends). If we later need cross-tool OTel correlation, we re-open this
  ADR — the Langfuse OTel ingest endpoint is the likely re-entry point.
- Removing Phoenix removed a class of "trace appears in one UI but not the
  other" debugging dead-ends.
- Volume note: Langfuse Postgres holds the only copy of historical traces.
  See `docs/agent_rules/anti-patterns.md` ("Don't delete Docker volumes
  without explicit approval"). This rule predates this ADR but is now the
  sole protection for trace history.

## Update (2026-08-12)

The routing described above (both paths into **v2**) has moved: a **v3** instance
was added for the Claude Code path, and the CodeArkt path has now been repointed
onto v3 as well (executing FU-1 of
`docs/plans/2026-06-21-claude-code-langfuse.md`). Concretely, the `app` service in
`docker-compose.yml` points at v3 (`LANGFUSE_HOST` → `:3051`, keys →
`LANGFUSE_*_KEY_V3`) — no SDK bump, since the pinned langfuse 2.60 client emits over
`/api/public/ingestion`, which the v3 server accepts.

Net today: both orchestrator paths report to a single **v3** instance. The v2 stack
is retained **read-only** for historical CodeArkt traces (its Postgres volume still
holds the only copy — the "don't delete volumes" rule above still applies). The
"Langfuse v2 as the shared target" statement in the Decision section is superseded
by this note. See `docs/observability.md` for the current trace shapes and queries.
