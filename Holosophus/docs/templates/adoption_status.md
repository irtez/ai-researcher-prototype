# Adoption Status — template

> A reusable template. Copy to `docs/adoption_status.md`, fill it for this repo, and keep it
> updated as practices land. Genre: **reference** (state) — living, edit in place.

A living map of which agent-harness practices a repository has adopted, and how far. It makes the
harness **self-describing**: a new agent or contributor sees what is enabled at a glance, and what
is deliberately not. Pair every `✓` with a location so a reader can verify it without asking.

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
| Documentation genres (single-writer-per-fact) | `[status]` | `[where]` |
| Routing index | `[status]` | `[where]` |
| Decisions log (ADR folder + template) | `[status]` | `[where]` |
| Spec / investigation entry path | `[status]` | `[where]` |
| Verify gate (`[verify command]`) | `[status]` | `[where]` |

### Single-agent discipline
| Practice | Status | Notes / location |
| --- | --- | --- |
| Anti-pattern log (`AP-NN` lifecycle) | `[status]` | `[where]` |
| Spec-as-contract status gate (`Draft → … → Approved`, frozen) | `[status]` | `[where]` |
| Generator–evaluator split | `[status]` | `[where]` |
| Output / prompt regression gate | `[status]` | `[where]` |
| Decision distillation on completion | `[status]` | `[where]` |

### Mechanical enforcement
| Practice | Status | Notes / location |
| --- | --- | --- |
| Docs-hygiene gate (`[docs gate command]`) | `[status]` | `[where]` |
| Lint / typecheck / test gate | `[status]` | `[where]` |
| Permissions (allow / deny) | `[status]` | `[where]` |
| Pre-commit / agent hooks | `[status]` | `[where]` |
| Layered config (team / module / personal) | `[status]` | `[where]` |

### Agent infrastructure
| Practice | Status | Notes / location |
| --- | --- | --- |
| Runtime / product agents | `[status]` | `[where]` |
| Dev-harness agent isolation (namespace / dir) | `[status]` | `[where]` |
| Committed dev-harness agents | `[status]` | `[where]` |
| Skills | `[status]` | `[where]` |
| Workflows / autonomous build loop | `[status]` | `[where]` |
| MCP / tool wiring | `[status]` | `[where]` |

### Advanced / deferred
| Practice | Status | Notes / location |
| --- | --- | --- |
| Adoption / outcome evaluation | `[status]` | `[where]` |
| Domain acceptance gate (real artifact / render) | `[status]` | `[where]` |
| Autonomous long-horizon orchestration | `[status]` | `[where]` |

## How to use

- Update a row when a practice lands or changes status — this doc is living, edit in place.
- Don't pad the table — `✗ not needed` and `deferred` are legitimate, honest statuses.
- Pair every `✓` with a location so a reader can verify it.
- Prefer `partial` / `deferred` over an aspirational `✓`; this map is only useful while it is honest.
- Record *why* the delta was adopted (and what was deliberately skipped) in an ADR; link it from the
  filled `docs/adoption_status.md`.
