# 0007. Adopt agent-engineering harness practices from plate_ai (research-agent adaptation)

- **Status**: Accepted
- **Date**: 2026-06-30
- **Deciders**: @aleksei

## Context

Holosophus is a research-agent harness — a manager that delegates to specialist agents (librarian,
proposer, reviewer, writer, mle-solver), wrapped by two first-class orchestrators (CodeArkt and
Claude Code). Over its life it grew most of the agent-engineering-manifesto harness *by fact*:
two-orchestrator parity (ADR-0001), canonical YAML prompts (ADR-0002), a single observability
stack (ADR-0003), evidence-based prompt minimalism with an eval-delta gate (ADR-0004), a vendored
monorepo (ADR-0005), the five-genre doc taxonomy (`docs/README.md`), a routing index
(`docs/index.md`), agent rules plus an `AP-NN` anti-pattern log, and a `make verify` gate. Roughly
70% of the harness form was already in place — but organically, never recorded as a deliberate model.

A sibling project, `plate_ai`, recently ported the same manifesto harness wholesale and, in doing
so, sharpened pieces Holosophus had only partially: a doc-genre *policy* backed by a mechanical
docs-hygiene gate, a spec-as-contract status gate, generator–evaluator skill/agent splits, and an
autonomous build loop. But `plate_ai` is a web/app product, so parts of its harness are app-shaped —
a `ui-verify` real-render acceptance gate, a layered module config, a `todo.md`/`roadmap.md`
planning flow — that do not transfer to a research-agent harness.

This record captures the deliberate decision to close the remaining delta: adopt the parts that
transfer, adapt `plate_ai`'s app lenses to research lenses, and explicitly *not* port the app-only
parts. Without it, the next contributor re-asks "why do we have `dev-` agents, a docs gate, and a
build loop — and why no `ui-verify` step like plate_ai?".

## Decision

Adopt the harness delta in four layers, adapted to a research-agent (not app) project:

- **Phase 0 — dev-agent namespace isolation.** Hand-written developer-harness agents use a `dev-`
  prefix in `.claude/agents/`, are committed via a gitignore negation
  (`!/.claude/agents/dev-*.md`), and are filtered out of the research manager's agent set by
  `iter_research_agent_paths()` in `scripts/run_research.py`. Research runs never see dev agents.
- **Phase 1 — doc-genre policy + docs hygiene.** `docs/README.md` (genre policy),
  `docs/index.md` (routing), `docs/templates/decision_record.md` (ADR template), the `AP-NN`
  anti-pattern format, and a mechanical docs gate (`scripts/check-docs.sh` + the `check-docs` and
  `docs-review` skills).
- **Phase 2 — spec-as-contract.** A status gate (`Draft → Approved → Implemented`, body frozen on
  Approved) in `docs/templates/system_design.md`, plus the `create-spec` / `review-spec` skills and
  the committed `dev-spec-writer` + `dev-spec-reviewer` generator–evaluator pair (an author must not be
  the sole judge of its own spec).
- **Phase 3 — self-describing adoption.** This ADR plus `docs/adoption_status.md` (template:
  `docs/templates/adoption_status.md`), a tracker mapping every practice to a status and a location.
- **Phase 4 — adapted autonomous build loop.** Committed dev agents (`dev-plan-writer`,
  `dev-plan-reviewer`, `dev-executor`, `dev-evaluator`), workflows
  (`.claude/workflows/{author-specs,build-change}.workflow.js`), a `build` skill, and
  `docs/workflow-tool-reference.md`. `plate_ai`'s app lenses are remapped to research lenses:
  verify gate = `make verify` + `make smoke`/eval-subset; "grounding" = artifacts really exist /
  no fabricated results; observability = clean Langfuse traces; and privacy/tap-budget checks are
  replaced by prompt-regression (eval score didn't drop, ADR-0004) and MCP tool-wiring completeness.

## Alternatives and why not

> The highest-value section — don't drop it. Append-only: once `Accepted`, this body is frozen. A
> reversal is a *new* record that supersedes this one.

- **Full verbatim port of the plate_ai harness, including its app/UI lenses** — rejected.
  Holosophus has no UI, so `ui-verify` and real-render acceptance have nothing to render;
  `config_layers.md` assumes a module config layer Holosophus does not have (single `CLAUDE.md`);
  `todo.md`/`roadmap.md` duplicate the existing plan-mode + `docs/plans/` flow. Porting them would
  create placeholder docs and dead gates that drift.
- **Design 2: separate physical directories for dev vs research agents** — deferred, not rejected.
  It is the cleaner long-term isolation, but more invasive (entry-point path changes, two agent
  roots). Design 1 (co-locate + `dev-` naming filter) delivers the same research-run isolation with
  one gitignore line and one filter function, so it was chosen first; revisit if the dev-agent set
  grows large.
- **Do nothing / keep adopting ad-hoc** — rejected. The harness was already ~70% present by
  accident; leaving the rest implicit means every new contributor re-derives the genre rules,
  re-asks why dev agents are isolated, and risks a research run picking up a dev agent. The cost of
  recording the model is low and it shapes how the next change is specced and built.

## Consequences

- New artifacts: `docs/adoption_status.md` + `docs/templates/adoption_status.md` (this phase);
  `docs/README.md`, `docs/templates/decision_record.md`, `scripts/check-docs.sh`,
  `.claude/skills/{check-docs,docs-review}` (Phase 1); the spec status gate in
  `docs/templates/system_design.md`, the `create-spec`/`review-spec` skills, and `dev-spec-reviewer`
  (Phase 2); build-loop dev agents, workflows, the `build` skill, and `docs/workflow-tool-reference.md`
  (Phase 4).
- **New invariant — `dev-` is reserved.** Any hand-written developer agent must use the `dev-`
  prefix; research-runtime agents must not. `iter_research_agent_paths()` is the single filter —
  code that enumerates research agents must go through it, not re-glob `.claude/agents/*.md`. Add an
  `AP-NN` entry in `docs/agent_rules/anti-patterns.md` if this is violated.
- **Spec-first becomes status-gated.** A non-trivial change starts from a `docs/plans/` spec whose
  `Status` is a contract: the body freezes on `Approved`; later work appends to a progress log, it
  does not silently rewrite the contract.
- A docs-hygiene gate is now available (`scripts/check-docs.sh` + the `check-docs` / `docs-review`
  skills); run it when a change touches `docs/`, `CLAUDE.md`, or another canonical doc.
- The adopted build loop reuses the existing `make verify` / `make smoke` gates rather than adding a
  parallel verifier; verification stays end-to-end (real runs / artifacts), consistent with ADR-0004
  and the project's "no snapshot-only verification" rule.
- Adoption state is now legible and must be kept honest: `docs/adoption_status.md` is updated as
  each phase lands (Phases 0–3 landed; Phase 4 in progress).
- Deliberately not adopted, so they are not re-litigated: `ui-verify` / real-render acceptance,
  `config_layers.md`, the `todo.md`/`roadmap.md` flow, and the architecture / data-topology / index
  *templates* (those docs already exist; a template is only added when a new module appears).
