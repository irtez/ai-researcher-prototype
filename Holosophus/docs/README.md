# docs/ — how this is organized

> Why the docs are shaped this way. Looking for *where* something lives → [`index.md`](index.md).

Holosophus is a research-agent harness: a manager that delegates to specialist agents
(reviewer / writer / proposer / mle-solver / librarian), wrapped by two orchestrators
(CodeArkt and Claude Code). Every doc answers exactly one question, and that question is
its genre (its home). One fact lives in one genre — a duplicated fact drifts, and the next
session reads the stale copy.

## The five genres

| Genre | Question | Where it lives | `Status` | Mutability |
| --- | --- | --- | --- | --- |
| **decisions** (WHY) | why we chose X, not Y | [`decisions/*.md`](decisions/) (one record per file, `NNNN-slug.md`) | `Proposed·Accepted·Deprecated·Superseded` | append-only — body immutable once `Accepted`; supersede, don't edit |
| **plans / specs** (WHAT) | what we're building | [`plans/*.md`](plans/) from [`templates/system_design.md`](templates/system_design.md) | `Draft·Approved·Implemented` | append-only — contract frozen on `Approved`; progress log only grows |
| **agent rules** (HOW) | how we work here | [`agent_rules/*.md`](agent_rules/) (`backend.md`, `testing.md`, `anti-patterns.md`) | none — living | living — edit in place (git = history) |
| **anti-patterns** (Mistakes) | what keeps going wrong | [`agent_rules/anti-patterns.md`](agent_rules/anti-patterns.md) | `AP-NN`; superseded marked, not deleted | append-only — add an entry the *second* time a mistake recurs |
| **reference** | facts / navigation / state | [`index.md`](index.md) (router), [`architecture.md`](architecture.md), [`data-topology.md`](data-topology.md), [`observability.md`](observability.md), [`operations/`](operations/), [`notes/`](notes/) | none — living | living — edit in place |

Anti-patterns are the **✗ side of agent rules** — a negative is the inverse of a HOW-rule —
but we keep them as their own genre with their own `AP-NN` lifecycle: a recurring mistake
graduates to an entry on its *second* occurrence, and as it becomes mechanically checkable it
moves into a lint rule / type / `make validate` gate / smoke check.

## The one principle: mutability follows event-vs-state

Mutability is not a free per-genre choice — it follows whether the artifact is an **event** or
a **state** (a LOG vs a WIKI):

- **State → living.** `agent rules` and `reference` describe how things are *now*. Exactly one
  current copy, rewritten in place, never stale; history is in git.
- **Event → append-only.** `decisions` (why, at a time), `plans/specs` (what we built, frozen
  on `Approved`), and the `anti-patterns` log (`AP-NN` entries) record something that happened.
  The body is immutable; they age by `Status`, never by silent rewrite.

**The current rule lives in a living doc; the decision record is its immutable provenance.** A
decision (ADR) records *why* a choice was made, with the rejected alternatives. The rule it
produces lives in `agent_rules/` — current, on the hot path that an agent acts off. A session
reads an ADR only to interrogate a rationale. Never make one file serve both roles (see
[`agent_rules/anti-patterns.md`](agent_rules/anti-patterns.md) AP-02: don't duplicate
`system.yaml` into `CLAUDE.md`).

## Single writer per fact

- Bias to fewer files — a doc is context cost + maintenance liability. A new file is justified
  only if it answers a *different* question; otherwise co-locate.
- Don't restate in prose what code, prompts (`system.yaml`), or `make validate` already express.
- Each genre with a folder ([`decisions/`](decisions/), [`agent_rules/`](agent_rules/)) carries
  its own `README` = that genre's rules + index. Document templates live in
  [`templates/`](templates/).

## Distillation on completion

When a `plans/` spec reaches `Approved` and ships, distill it — don't leave the whole working
doc on the hot path:

- a durable *why* (a choice that will be re-litigated) → a new ADR in [`decisions/`](decisions/);
- a durable *how* (a rule the next session must follow) → an entry in the right
  [`agent_rules/`](agent_rules/) file;
- a recurring *mistake* (now seen twice) → an `AP-NN` entry in
  [`agent_rules/anti-patterns.md`](agent_rules/anti-patterns.md), with a mechanical guard if one
  exists.

The spec then stays as the frozen record of what was built; the live facts move to their genre.

## Runtime prompts are not docs

The canonical research-runtime behaviour lives in `holosophos/holosophos/prompts/*.yaml`
(manager + specialists), not here. These docs are the **developer harness**. Don't copy prompt
content into docs or vice versa.
