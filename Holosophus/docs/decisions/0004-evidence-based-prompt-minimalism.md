# 0004. Evidence-based prompt minimalism for specialist agents

- **Status**: Accepted
- **Date**: 2026-03-30
- **Deciders**: project lead

## Context

Specialist prompts started prescriptive and verbose — each agent received an
explicit workflow ("first do X, then Y, then archive Z"), exhaustive tool
tables, and ritualised post-processing. The model worked but with two
recurring failures:

- **Literal-mindedness.** Agents followed the written workflow even when the
  context warranted a different sequence — e.g. ran the full archival ritual
  (4–5 tool calls per paper) on every search hit, including ones the user
  wouldn't ultimately need.
- **Prompt bloat.** Each new tool added another line; over six months the
  librarian prompt drifted to ~1500 tokens, paid on every turn for the
  whole session.

Three independent experiments in late Mar 2026 produced concrete evidence
that shorter, *descriptive* prompts ("here are the tools and how they
relate; pick what fits") outperform longer *prescriptive* ones ("call A,
then B, then C") on this stack:

- **Librarian (`8906697`, Mar 30).** Pruned to ~250 tokens (–83%), keeping
  three search-priority tiers (AlphaXiv → ArXiv/S2 → Web) and dropping the
  archival ritual entirely. Justification cited: Anthropic's Claude 4.6
  guidance ("descriptive over prescriptive") and Augment Code's published
  400–800-token sweet spot.
- **MLE-Solver A/B on 7 tasks (`5cedd61`, Mar 29).**
  | Variant | Tool calls | AUC |
  |---|---|---|
  | Full prompt | 117 | 0.871 |
  | Minimal+ | **75** | **0.893** |
  | Vanilla | 56 | 0.871 |
  Minimal+ won on both efficiency (–46% tool calls vs full) and quality
  (+2.5% AUC). The kept content is one rule ("Make one big change at a
  time") plus the MCP tool list.
- **Proposer scoring scale (`a7f1172`, Mar 29).** Switched from 1–10 to 1–5
  per published evidence on better LLM↔human alignment at coarser scales,
  and fixed a leniency bug in passing — weak proposals now get 2/5 instead
  of 5/10.

Concurrent A/B benchmark on librarian search added a second axis: prompt
shrinking happened alongside an iterative tool-set tuning that took
recall@k from **35.8% → 76.8%** over 13 iterations, while runtime fell
from 86 → 23 minutes via parallel API calls. The methodological lesson is
that prompt and tool decisions need a measurable target before they are
made, not after.

## Decision

**Specialist prompts are descriptive, minimal, and revised against
benchmarks, not against intuition.** Concretely:

- Default shape per specialist: role, principles, tool list, escape
  conditions. No multi-step workflows. No "first do X, then Y" sequences
  unless an experiment shows the sequence is necessary.
- Any prompt change to a specialist must come with a benchmark delta:
  before/after on `holosophos/evals_and_reports/` (librarian: LitSearch
  recall + internal `librarian_test`; mle-solver: AUC on the 7-task set;
  proposer: scoring testset). "It reads better" is not a reason.
- The canonical YAML schema (ADR 0002) makes this enforceable — every
  change goes through `canon` and is automatically reflected in both
  orchestrators, so an A/B is a single-source experiment, not a
  per-orchestrator one.
- The manager prompt (`system.yaml`) is intentionally *not* covered by this
  rule. The manager owns delegation policy and language policy — those are
  contracts with the user, not behaviours we tune against an eval.

Specific defaults to date:
- librarian: ~250 tokens, 3 search tiers, no archival ritual.
- mle-solver: minimal+ variant ("one big change at a time" + MCP tools).
- proposer: 1–5 scoring scale, refinement loop ≤ 3 iterations, novelty
  check via `arxiv_search`.

## Consequences

- New contributors are expected to run an eval before merging a prompt
  change. CLAUDE.md and `docs/agent_rules/testing.md` point at the relevant
  runners; an eval delta in the PR description is the new norm.
- We accept that the prompts read sparse and "underspecified" by older
  conventions — the data says that helps the agent, not the human reader.
  When in doubt, lean shorter.
- Adding a new tool to a specialist is a prompt change that requires the
  same eval discipline — otherwise tool inventories drift back toward
  bloat.
- Reverting to verbose prescriptive prompts requires re-opening this ADR
  with a benchmark showing it wins. The previous default does not return
  by appeal to "obviousness".
- The "prompt is an API contract, not documentation" framing (used in the
  Mar 30 librarian rewrite commit message) is now the project line — bug
  in a prompt is a bug in production, with the same review weight as a
  code bug. See `docs/history.md` for the full timeline.
