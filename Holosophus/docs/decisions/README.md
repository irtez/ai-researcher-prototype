# Architecture Decision Records

Short, append-only records of "why we chose X, not Y" for decisions that future
readers (human or agent) would otherwise have to reconstruct from chats and
commit messages.

T1.2 in the AI-Native Practices reference (kept outside this repo). Format
follows Michael Nygard's original ADR template
(<https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions>).

## File naming

`NNNN-short-slug.md` where `NNNN` is a zero-padded incrementing number starting
at `0001`. Don't reuse numbers; superseded ADRs stay in place with their
`Status` updated.

## Required sections

Copy [`../templates/decision_record.md`](../templates/decision_record.md) — it is
the single source for the section skeleton (Status / Date / Deciders / Context /
Decision / Alternatives and why not / Consequences), so the format is not
restated here. Statuses: `Proposed | Accepted | Deprecated | Superseded by NNNN`.

## When to write one

- The decision will be expensive to reverse (touches multiple modules,
  changes runtime behaviour, locks in a vendor or protocol).
- It will be re-asked: every new contributor would otherwise raise it.
- It removes an option deliberately ("we considered X and decided not to").

For local design that fits in a single PR description, an ADR is overkill —
use `docs/templates/system_design.md` instead.

## When to update one

When a decision is reversed or replaced, set `Status: Superseded by NNNN`
and link the new ADR. Don't delete the old one — the audit trail is the
point.
