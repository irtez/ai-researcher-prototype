# 0005. Vendored monorepo for `holosophos`, `codearkt`, `mle_kit`

- **Status**: Accepted
- **Date**: 2026-02-15
- **Deciders**: project lead

## Context

Three repositories were under active co-development and constantly
out-of-sync:

- `holosophos` — research-pipeline application code (agents, prompts,
  evals, reports).
- `codearkt` — CodeAct runtime (Python `<execute>` loop, MCP client glue,
  prompt storage).
- `mle_kit` (and the corresponding `mle_kit_mcp` MCP service) — code
  execution sandbox: `bash`, `text_editor`, `remote_bash`, LLM proxy.

Cross-repo changes were the common case rather than the exception. Adding
a tool meant edits in `mle_kit` *and* a new agent prompt in `holosophos`;
fixing a CodeAct bug meant a `codearkt` patch *and* a `holosophos` rerun
to verify; adjusting trace shape meant `codearkt` instrumentation *and*
`holosophos/langfuse_openai_patch.py` in lockstep. With three repos this
required:

- A coordinated PR per change set, manually sequenced.
- Editable installs (`pip install -e ../codearkt`) for local dev, which
  diverged across contributors.
- Version pins in each repo's `pyproject.toml` that lagged actual
  compatibility, surfacing as runtime import errors instead of
  resolution-time conflicts.
- Bisecting a regression across three histories.

By Feb 2026 the integration overhead exceeded the value of independent
release cadence — there was no independent cadence; every release was
joint.

## Decision

**Vendor `codearkt` and `mle_kit` into the `Holosophus` repository as
top-level subdirectories**, retaining their own `pyproject.toml` so they
remain installable units, but living in one git history. Realised in
`ffa0a78` (codearkt, +9876 lines) and `4ad2bb2` (mle_kit, +2644 lines) on
Feb 15.

The resulting layout (current):

```
Holosophus/
├── holosophos/        # the research-pipeline package (vendored upstream app repo)
│   └── holosophos/    # the actual Python package
├── codearkt/          # vendored CodeArkt runtime
├── mle_kit_mcp/       # vendored MLE Kit MCP service
├── academia_mcp/      # academia tools
├── scripts/           # orchestrator runner + langsmith fork bridge
└── pyproject.toml     # root manifest for the Claude Code orchestration path
```

Each vendored subdirectory keeps its own tests and `pyproject.toml`. The
root `pyproject.toml` exists only for the Claude Code orchestrator path's
dependencies; the CodeArkt path resolves through `holosophos/`'s
`pyproject.toml`.

Upstream tracking is deliberately informal — we do not run a
git-subrepo / git-subtree workflow. When the upstream `codearkt` or
`mle_kit` repository ships something we need, we copy it in by hand, the
same way we'd take a patch from a fork.

## Consequences

- One `git log`, one bisect, one PR per cross-cutting change. The
  three-repo coordination tax is gone.
- Two virtualenvs in active use:
  - `Holosophus/holosophos/.venv` for the CodeArkt path (langfuse v2 pin).
  - `Holosophus/.venv` for the Claude Code path (langfuse v3 pin via root
    `pyproject.toml`, established in `869c8db`).
  - This split is intentional and documented in ADR 0003 and the root
    manifest comment.
- Upstream `codearkt` / `mle_kit` repositories no longer receive changes
  from us by default. If we need to upstream a fix, the workflow is
  manual: cherry-pick from the monorepo into a fork. We accept this cost
  in exchange for no longer paying it on every change.
- The repo is large by Python-project standards (tens of thousands of
  vendored lines). Tooling (formatters, type-checkers) must be configured
  per-subdirectory or with explicit excludes. CI runs each subpackage's
  test suite independently.
- New contributors should not assume the vendored subdirectories are
  third-party untouchables. They are co-owned. PRs that change
  `codearkt/` or `mle_kit_mcp/` are normal.
- Reverting this — splitting back into three repos — requires re-opening
  this ADR and absorbing the integration tax we already measured. The
  monorepo is the default until a concrete pain point forces a split
  (e.g. mle_kit growing into a product with external users).
