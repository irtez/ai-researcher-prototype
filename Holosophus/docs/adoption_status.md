# Adoption Status (this repository)

A living map of which agent-harness practices Holosophus has adopted, and how far. It makes the
harness self-describing: you can see what is enabled, what is a documented recipe, and what is
deliberately out of scope. Template: [`docs/templates/adoption_status.md`](templates/adoption_status.md).
Why the delta was adopted (and what was skipped): [`docs/decisions/0007-adopt-harness-practices.md`](decisions/0007-adopt-harness-practices.md).

> Context: Holosophus is a **research-agent harness** (a manager that delegates to specialist
> agents, wrapped by two orchestrators — CodeArkt and Claude Code), not a web/app project. It
> organically grew most of the agent-engineering harness by fact (ADR-0001…0006, the five-genre
> doc taxonomy, the `make verify` gate). This map tracks the deliberate adoption of the remaining
> delta from the sibling `plate_ai` project, adapted to research lenses (ADR-0007). Phases 0–3
> have landed; Phase 4 (the adapted build loop) is in progress.

## Legend

- `✓` — implemented and working
- `partial` — partially in place (note the gap)
- `doc-only` — documented recipe, not mechanically enforced
- `deferred` — planned; a plug-point exists, not built yet
- `✗` — not needed / out of scope for this repo

## Status

### Foundations
| Practice | Status | Notes / location |
| --- | --- | --- |
| Documentation genres (single-writer-per-fact) | `✓` | `docs/README.md` — five-genre taxonomy (decisions / plans / agent-rules / anti-patterns / reference) |
| Routing index | `✓` | `docs/index.md` |
| Decisions log (ADR folder + template) | `✓` | `docs/decisions/` (7 records + `README.md`); template `docs/templates/decision_record.md` |
| Spec / investigation entry path | `✓` | `docs/templates/{system_design,implementation,investigation}.md`; specs land in `docs/plans/` |
| Verify gate (`make verify`) | `✓` | `Makefile` → `check-env` + `validate` (lint + typecheck); `make smoke` / eval-subset for the runtime path |

### Single-agent discipline
| Practice | Status | Notes / location |
| --- | --- | --- |
| Anti-pattern log (`AP-NN` lifecycle) | `✓` | `docs/agent_rules/anti-patterns.md` — graduates on the 2nd recurrence; lifecycle defined in `docs/README.md` |
| Spec-as-contract status gate (`Draft → Approved → Implemented`, frozen) | `✓` | `Status` field in `docs/templates/system_design.md`; "contract frozen on Approved" in `docs/README.md` genre table |
| Spec authoring / review skills | `✓` | `.claude/skills/{create-spec,review-spec}` (design → review → Approve → distill lifecycle) |
| Generator–evaluator split | `✓` | spec-level pair `dev-spec-writer` + `dev-spec-reviewer` (Phase 2); build-loop `dev-executor` (generator) + `dev-evaluator` (judge) (Phase 4); research-side peer reviewer `.claude/agents/reviewer.md` |
| Output / prompt regression gate | `✓` | ADR-0004 evidence-based prompt minimalism — every prompt change gated on an eval delta |
| Decision distillation on completion | `doc-only` | "Distillation on completion" in `docs/README.md`: a shipped spec → ADR / agent-rule / `AP-NN` entry |

### Mechanical enforcement
| Practice | Status | Notes / location |
| --- | --- | --- |
| Docs-hygiene gate (`scripts/check-docs.sh`) | `✓` | leaked-placeholder scan + canonical-doc existence guard + trailing-whitespace scan; skills `.claude/skills/{check-docs,docs-review}` |
| Lint / typecheck gate (`make validate`) | `✓` | `Makefile` → `holosophos` validate (lint + typecheck) |
| Runtime smoke / eval-subset | `partial` | `make smoke` (`scripts/smoke_test_pipeline.py`) works; full `make test` (pytest) blocked by a pre-existing pyarrow/grpcio build issue (known, tracked) |
| Permissions (allow / deny) | `partial` | per-dev `.claude/settings.local.json` (gitignored); project `.claude/settings.json` is intentionally minimal — no committed deny hardening (see `CLAUDE.md` Sandbox / permissions) |
| Pre-commit / agent hooks | `partial` | `.claude/settings.json` Stop / SessionEnd hooks run the Langfuse export (`scripts/cc_langfuse_export.py`); no docs/verify pre-commit hook (run `make verify` / `check-docs` manually) |
| Layered config (team / module / personal) | `✗` | single `CLAUDE.md` + per-dev `settings.local.json`; no module config layer (plate_ai's `config_layers.md` not adopted) |

### Agent infrastructure
| Practice | Status | Notes / location |
| --- | --- | --- |
| Research runtime agents | `✓` | `.claude/agents/{librarian,proposer,reviewer,writer,mle-solver,mle-solver-remote}.md`, generated from `holosophos/holosophos/prompts/*.yaml` |
| Dev-harness agent isolation (`dev-` prefix) | `✓` | gitignore negation `!/.claude/agents/dev-*.md`; research set filtered via `iter_research_agent_paths()` in `scripts/run_research.py` (ADR-0007 "Design 1", Phase 0) |
| Committed dev-harness agents | `✓` | `dev-spec-writer` + `dev-spec-reviewer` (Phase 2) and build-loop `dev-{plan-writer,plan-reviewer,executor,evaluator}` (Phase 4) — all committed under the `dev-` prefix |
| Skills | `✓` | `.claude/skills/{check-docs,docs-review,create-spec,review-spec,postprocess-traces}`; `build` is Phase 4 (in progress) |
| Workflows / autonomous build loop | `partial` | Phase 4 — `.claude/workflows/{author-specs,build-change}.workflow.js`, `.claude/skills/build`, `docs/workflow-tool-reference.md` not yet landed |
| MCP / tool wiring | `✓` | `.mcp.json` (Claude Code) + `MCP_CONFIG` (CodeArkt) + per-agent allowlists; tool-wiring completeness is a build-loop verify lens |

### Deliberately deferred / out of scope
| Practice | Status | Notes / location |
| --- | --- | --- |
| Real-render / UI acceptance (`ui-verify`) | `✗` | no UI in Holosophus — replaced by `make verify` + `make smoke`/eval-subset and artifact-grounding checks (no fabricated results) |
| Layered config doc (`config_layers.md`) | `✗` | single `CLAUDE.md`, no module config layer |
| `todo.md` / `roadmap.md` planning flow | `✗` | project uses plan-mode + `docs/plans/` for task tracking |
| Architecture / data-topology / index *templates* | `deferred` | `docs/architecture.md`, `data-topology.md`, `index.md` already exist; a template is only added when a new module appears |
| Design 2: separate physical agent directories | `deferred` | Design 1 (co-locate + `dev-` naming filter) chosen first; physical split is more invasive — revisit if the dev-agent set grows (ADR-0007) |

## Notes

- This map is itself an adoption-legibility artifact; keep it honest. `✗ not needed` and `deferred`
  are legitimate statuses — don't inflate to `✓`.
- Adoption landed in phases: Phase 0 (dev-agent isolation) + Phase 1 (doc-genre policy + docs gate)
  + Phase 2 (spec-as-contract gate & spec skills) + Phase 3 (this tracker) are in; Phase 4 (the
  adapted build loop and its committed dev agents) is in progress. Flip those rows to `✓` as they land.
- The full rationale and the rejected alternatives live in
  [`docs/decisions/0007-adopt-harness-practices.md`](decisions/0007-adopt-harness-practices.md).
