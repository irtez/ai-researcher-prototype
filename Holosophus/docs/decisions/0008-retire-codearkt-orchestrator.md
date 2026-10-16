# 0008. Retire CodeArkt as an orchestration path

- **Status**: Accepted
- **Date**: 2026-08-18
- **Deciders**: @aleksei
- **Amended**: 2026-08-19 — one factual correction to Consequences: `settings.py` per-agent allowlists
  are **not** inert, since `compose_main_agent` reads them for the eval runners this record preserves.
  The decision itself is unchanged. Recorded here rather than edited silently, per the append-only rule.

## Context

Holosophus has run two first-class orchestrators since [ADR-0001](0001-two-orchestrators.md):
CodeArkt (the OpenRouter-backed CodeAct loop, `holosophos/holosophos/main_agent.py`) and Claude Code
(the native Claude Agent SDK path, `scripts/run_research.py`). `AP-04` exists because past PRs framed
CodeArkt as legacy and broke the eval pipeline some users relied on.

Production research has moved. Iterations 1–5 of the current study all ran through
`scripts/run_research.py`; the pause/resume, rate-limit, HITL and UI-operator work of the last month
(`docs/plans/2026-08-1*-*-cc.md`) is Claude-Code-only by construction.

The forcing constraint is the result-gate work
([`docs/plans/2026-08-18-self-improving-loop-verification.md`](../plans/2026-08-18-self-improving-loop-verification.md)).
Its enforcement mechanism is a `PreToolUse` hook that denies a sub-agent delegation. CodeArkt has no
equivalent chokepoint — there, sub-agents are invoked as `agent__writer(...)` inside `<execute>`
blocks, so parity would mean designing, building and testing a second, structurally different
enforcement mechanism for a path no production run uses. Under ADR-0001 and AP-04 as they stand, any
such spec is obliged to do that.

Two facts bound how far retirement can go:

- **The agent-eval surface runs on CodeArkt.** Seven of the fourteen scripts in
  `holosophos/evals_and_reports/` import `codearkt.server.run_batch` and
  `main_agent.compose_main_agent` — every agent-level runner (`run_reviewer_eval.py`,
  `run_writer_eval.py`, `run_mle_solver_eval.py`, `run_proposer_eval.py`, `run_librarian_eval.py`,
  `run_gaia.py`) plus the shared `observability.py`.
  [ADR-0004](0004-evidence-based-prompt-minimalism.md) requires every specialist prompt change to
  carry a benchmark delta, and this is the machinery that produces one.
- **The reference surface is wide.** 46 files under `docs/` mention CodeArkt, all seven prompt YAMLs
  carry a `codearkt:` section, and CLAUDE.md names it in eight places including project rule 3.
  Most of those are `plans/` and `decisions/` — append-only genres that must not be rewritten.

## Decision

**CodeArkt is retired as a target for new work: Claude Code is the sole orchestration path, and no
new spec owes CodeArkt parity.** The vendored runtime stays in the tree, unmaintained; existing
CodeArkt-only capability keeps working until something breaks it. The accepted trade-off is that the
agent-eval runners remain CodeArkt-bound for now, so ADR-0004's eval-delta gate has no Claude Code
path — a debt this record names rather than settles.

## Alternatives and why not

- **Keep both first-class (status quo, ADR-0001):** every new feature pays a design tax for a path
  production does not use. For the result gates specifically that tax is a whole second enforcement
  mechanism, since hooks have no CodeArkt analogue. The parity rule was written when both paths were
  live; it now blocks work rather than protecting users.
- **Delete CodeArkt outright — code, YAML sections, docs:** rejected as a false economy. It would
  strand the seven eval runners with no replacement, force edits to append-only `plans/` and
  `decisions/` records that are supposed to be immutable, and buy nothing: the `codearkt:` YAML
  sections and the vendored package are inert when nothing loads them.
- **Port the eval runners to Claude Code first, then retire:** the right end state, and it stays
  available — but sequencing it first blocks the gate work behind a migration of seven runners plus
  shared observability, with no user-visible benefit in between. Recorded here as follow-on, not as a
  precondition.
- **Retire CodeArkt but keep it as an eval-only dependency:** effectively what this decision does in
  the interim. Named separately because if the eval port never happens, this *is* the end state, and
  a future contributor should see it was chosen rather than drifted into.

## Consequences

- **[ADR-0001](0001-two-orchestrators.md) gets `Status: Superseded by 0008`.** `AP-04` in
  `docs/agent_rules/anti-patterns.md` is marked superseded **in place**, never deleted — its
  numbering is stable and the incident it records still happened.
- **CLAUDE.md**: project rule 3 ("CodeArkt and Claude Code are both first-class") is removed; the
  "Where things live" row for the CodeArkt entrypoint is marked as retired. For **new agent work** the
  MCP-tool wiring convention collapses from four sites to two (`.mcp.json` + YAML `claude_code.tools`).
  **`holosophos/holosophos/settings.py` allowlists stay live** — `compose_main_agent` reads them
  (`main_agent.py:61`, `settings.LIBRARIAN_TOOLS` and siblings) and every preserved eval runner calls
  it. They become inert only once the runners are ported; treating them as dead before that strands
  exactly the surface this record protects.
- **Prompt YAMLs**: the `canon` / `claude_code` sections remain canonical. `codearkt:` sections and
  `codearkt_framework.yaml` are dead but harmless — leave them; removal is churn against ADR-0002's
  schema for no runtime gain. New prompts need not add one.
- **ADR-0004's enforcement path is the open debt.** Until the runners are ported, a specialist prompt
  change cannot produce its required benchmark delta on the Claude Code path. Specs landing before
  then must state their substitute evidence explicitly (the result-gate spec uses fixture replay).
  This is the one consequence that should not be allowed to drift silently. Note the harness itself is
  **not** missing: `scripts/run_eval_claude_code.py` already "replaces CodeArkt's `run_batch()` with
  claude-agent-sdk `query()` calls" with semaphore-bounded concurrency. The four agent-level runners
  simply have not been pointed at it — this is porting work, not a capability gap.
- **The orchestrator becomes Claude-only, and that is the single real capability lost.** CodeArkt's
  manager is an `LLM(model_name=...)` over OpenRouter and can be any model
  (`main_agent.py::compose_main_agent`); the Claude Code path pins `model="opus"`
  (`scripts/run_research.py:1092`). Experiments are unaffected — acting agents inside a run reach
  their models through `llm_proxy`, not the orchestrator — so this only forecloses studying
  *orchestrator-backbone* effects, or running a deliberately cheap manager. Accepted knowingly; if
  that capability is ever wanted back, it is a new ADR, not a quiet revival of this path.
- **Living reference docs** (`docs/index.md`, `architecture.md`, `observability.md`,
  `current-state.md`, `adoption_status.md`) are updated in place to describe one orchestrator.
  `plans/` and `decisions/` are append-only and are **not** rewritten — they remain accurate records
  of what was true when written.
- **Unblocks**: [`docs/plans/2026-08-18-self-improving-loop-verification.md`](../plans/2026-08-18-self-improving-loop-verification.md),
  whose `[blocking]` open question is this record.
