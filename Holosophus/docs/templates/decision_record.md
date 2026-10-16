# NNNN. [short decision title]

> An architecture decision record (ADR) — copy to `docs/decisions/NNNN-short-slug.md`,
> `NNNN` zero-padded and incrementing (don't reuse numbers). One decision per file.
> Genre rules, statuses, and the index: [`docs/decisions/README.md`](../decisions/README.md).

- **Status**: Proposed → Accepted → Superseded by NNNN (or Deprecated)
- **Date**: YYYY-MM-DD
- **Deciders**: who signed off (handle or role)

## Context

Why the choice came up. What constraint or trade-off forced it (deadline, dependency,
prior incident, external requirement). A few paragraphs — reconstructable reasoning, not
full history.

## Decision

The chosen approach, in 1–3 sentences. Include the key trade-off accepted.

## Alternatives and why not

> The highest-value section of a record — don't drop it. Append-only: once `Status: Accepted`,
> the body is frozen. A reversal is a *new* record that supersedes this one (this one then gets
> `Status: Superseded by NNNN`). Edit freely only while `Proposed`.

- **[alternative]:** what it was, why it lost.
- **[alternative]:** what it was, why it lost.

## Consequences

What changes in code / prompts / process / other rules — new invariants contributors must
follow, future work this unlocks or blocks. Include the "what we agreed not to do" so the
option doesn't get re-litigated quietly. Link the PR, spec, or `agent_rules/` entry it produces.
