# System Design: [Feature Name]

> **Style guide.** Target length: **≤250 lines, hard ceiling 280**. If you
> are over, you are explaining instead of designing — link out to
> background docs (architecture / data-topology / specific living doc),
> drop alternatives that didn't survive 30 seconds of thought, compress
> decision tables to ≤5 rows, collapse sub-bullets into single sentences.
> Tables and code blocks count toward the budget.
>
> Reproduction artefacts (manager prompts, judge prompts, schemas) may be
> quoted verbatim **once** in their own block. If they balloon the doc,
> hoist them out to adjacent files (`prompts/`, `schemas/`) and link.
>
> A spec is a decision artefact, not a tutorial. Future-you needs the
> *what + why + tradeoffs*; the *how* belongs in code, the *background*
> in the relevant living doc.

## Meta
**Author(s):** [name]
**Reviewer(s):** [name — must NOT be the author; see the review gate below]
**Date Created:** [YYYY-MM-DD]
**Date Updated:** [YYYY-MM-DD]
**Status:** Draft | Approved | Implemented
**Related:** [issue / investigation / PR]

> **Status is the gate** (`Draft → Approved → Implemented`). A spec stays `Draft`
> until the readiness gate (`.claude/skills/review-spec/SKILL.md`, run
> independently via the `dev-spec-reviewer` agent — the author is not the sole
> judge of their own spec) returns `ready-to-approve`. An **Approved** spec is a
> **contract — implement it, don't re-litigate the design**; deliberately
> `[deferrable]` open questions are not blockers. Flip to `Implemented` once it
> ships and `make verify` passes, then run **Distillation on Completion** below.

## Context

What is changing, why it matters, what is already confirmed.

## Goals

- [ ] Goal 1
- [ ] Goal 2

## Non-Goals

What this change intentionally does not solve.

## Success Criteria

Observable, verifiable outcomes that confirm the solution works — a reader can
write pass/fail without asking the author. Give them ids (`SC1`, `SC2`, …); each
maps to a row in `## Tests`. A Success Criterion is **not** a Definition-of-Done:
"tests pass / lint clean / reviewed" is not a criterion.

## Proposed Design

### Architecture Overview

Main idea, participating modules/runtimes/contracts, key boundaries.

Reference `docs/architecture.md` for current system map.

### Reuse / Seams

Existing modules, agents, prompts, ports, or contracts the implementer should
**build on, not reinvent** — real paths + symbols (e.g.
`holosophos/holosophos/prompts/<agent>.yaml`,
`holosophos/holosophos/main_agent.py::MCP_CONFIG`, the per-agent allowlists in
`holosophos/holosophos/settings.py`), not copied code. The highest-leverage
section for an agent implementer; the review gate greps these to confirm they
resolve (SPEC-REUSE / SPEC-PATH).

### Key Decisions

This table is the **distillation source**: when the spec reaches `Status:
Implemented`, each durable decision here becomes an append-only ADR in
`docs/decisions/` (next `NNNN-short-slug.md`; format in
`docs/decisions/README.md`). A decision that changes agent-prompt behaviour must
carry an eval delta as evidence (ADR-0004), not "it reads better".

| Decision | Why | Tradeoff |
| --- | --- | --- |
| | | |

### Data / Contracts

Fill only if relevant. New or changed contracts, invariants to preserve.

Reference `docs/data-topology.md` for current data map.

## Alternatives Considered

| Option | Why not chosen |
| --- | --- |
| | |

## Risks and Open Questions

What remains unconfirmed before or during implementation. Tier every open
question so the review gate (and the reader) knows what blocks Approval:

- **[blocking]** — must be resolved before the spec can be Approved (the plan
  can't be assembled without the answer). A live `[blocking]` tag forces the
  review gate to verdict `not-ready` and becomes a numbered clarification for the
  author.
- **[deferrable]** — can be decided during implementation or measured in use;
  does not block Approval.

## Validation Plan

Which test surface to use (see `docs/agent_rules/testing.md`) and the one command
that proves it end-to-end — `make verify`, an eval subset, or a real session.
Per project rule 5, lint/typecheck alone is not enough. This section picks the
*surface*; the per-Success-Criterion acceptance intent lives in `## Tests`.

## Tests

**Design-time acceptance intent**, one row per Success Criterion: *what* a test
must assert to prove the SC (happy / edge / error path), at the cheapest reliable
surface. This is the durable traceability anchor the review gate greps against —
it is **NOT** a hand-maintained pass/fail ledger. The realized `SC → test::name`
mapping lives in the test files and pass/fail is owned by the verify gate; don't
mirror either back here. For an agent-prompt change the "test" is the eval delta
ADR-0004 requires (e.g. recall@k, AUC, the proposer scoring testset).

| Criterion | Acceptance intent (what the test must assert) | Type |
| --- | --- | --- |
| `SC1` | `[happy / edge / error the test proves — at the cheapest reliable surface]` | `[unit / integration / eval / smoke / manual]` |

## Distillation on Completion

When this spec reaches `Status: Implemented`, the design is history — distill its
durable knowledge outward so a future agent (or human) does not re-litigate it
(single-writer-per-fact):

- **A durable decision / trade-off** (from Key Decisions or Alternatives) → a new
  append-only ADR in `docs/decisions/` (next `NNNN-short-slug.md`; format in
  `docs/decisions/README.md`). A prompt-behaviour decision carries its eval
  evidence (ADR-0004).
- **A durable rule that is now a standard** → `docs/agent_rules/` (`backend.md`
  or `testing.md`).
- **A mistake that recurred during the work** → `docs/agent_rules/anti-patterns.md`.

Once distilled, the spec is a historical record (kept for the audit trail, not
maintained); the plan that implemented it stays under `docs/plans/`.
