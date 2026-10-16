# Reference: the Claude Code `Workflow` tool

> **Provenance / status.** This is a condensed transcription of the **inline description that ships
> with the Claude Code `Workflow` tool** — the prose the harness injects into the agent's context
> alongside the tool's JSON schema. It is **reference material about an external tool**, not a
> repo-authored contract: it documents the harness, so it is exempt from single-writer-per-fact (it
> defines no Holosophus product or codebase fact). Captured 2026-06-30 from a Claude Code session; if
> the harness updates the tool this copy can drift — treat the live tool description as the source of
> truth. Kept here because the Holosophus build loop (`.claude/skills/build/`,
> `.claude/workflows/{author-specs,build-change}.workflow.js`) is authored against this API, so a
> contributor editing those scripts needs it.

---

## What it is

`Workflow` executes a **workflow script that orchestrates multiple subagents deterministically**.
Workflows run in the **background** — the tool returns a task ID immediately and a
`<task-notification>` arrives on completion. `/workflows` watches live progress.

Use it to be **comprehensive** (decompose + cover in parallel), **confident** (independent
perspectives + adversarial checks before committing), or to take on **scale one context can't hold**
(audits, sweeps, broad refactors). The script encodes that structure: what fans out, what verifies,
what synthesizes. A subagent's final text **is** the return value of `agent()`.

It is **gated behind explicit opt-in** (it can spawn dozens of agents): the keyword `ultracode`,
ultracode-on-for-session, a direct request for a workflow / multi-agent orchestration, or a skill /
slash command whose instructions tell the agent to call it (this is how `/build` launches the two
Holosophus workflows). For any other task, use the `Agent` tool instead. The right move is often
**hybrid**: scout inline to discover the work-list, then call `Workflow` to pipeline over it.

---

## The `meta` block (required)

Every script must begin with `export const meta = {...}` as a **pure literal** — no variables,
function calls, spreads, or template interpolation:

```js
export const meta = {
  name: 'build-change',
  description: 'Full lifecycle for one Approved spec',   // one line, shown in the permission dialog
  phases: [                                              // one entry per phase() call, matched by exact title
    { title: 'Plan' },
    { title: 'Implement' },
  ],
}
phase('Plan')   // script body starts here
```

- Required: `name`, `description`. Optional: `whenToUse`, `phases`, `model`.
- Use the **same titles** in `meta.phases` as in `phase()` calls — matched exactly. A `phase()` with no
  matching `meta` entry just gets its own progress group.

Pass the script via `script` (inline) — it auto-persists to a file and returns the path; iterate with
`{ scriptPath }` instead of resending the whole script.

---

## Script body hooks

The body runs in an async context — `await` directly.

### `agent(prompt, opts?) → Promise<any>`

Spawn a subagent.

- Without `schema` → returns its final **text** (a string).
- With `schema` (a JSON Schema) → the subagent is forced to call a `StructuredOutput` tool and
  `agent()` returns the **validated object** (the model retries on mismatch; no parsing needed).
- Returns **`null`** if the user skips the agent or it dies on a terminal error after retries —
  `.filter(Boolean)` the results.

`opts`: `label` (display label) · `phase` (assign to a progress group — set it explicitly inside
`pipeline`/`parallel` stages to avoid racing the global `phase()` state) · `schema` · `model`
(per-agent override; **default to omitting** — inherit the session model) · `effort`
(`low`/`medium`/`high`/`xhigh`/`max` — `low` for cheap mechanical stages, higher only for the hardest
judges) · `isolation: 'worktree'` (fresh git worktree — **expensive**; only when agents mutate files in
parallel) · `agentType` (a custom subagent type, e.g. `'Explore'`, `'dev-evaluator'`; composes with
`schema`).

Workflow agents reach all session MCP tools via `ToolSearch` (schemas load on demand per agent).

### `pipeline(items, stage1, stage2, ...) → Promise<any[]>`

Run each item through **all stages independently, with NO barrier between stages** — item A can be in
stage 3 while item B is still in stage 1. **The default for multi-stage work.** Each stage callback
receives `(prevResult, originalItem, index)`. A stage that throws drops that item to `null` and skips
its remaining stages.

### `parallel(thunks) → Promise<any[]>`

Run thunks concurrently. **This is a BARRIER** — it awaits all before returning. A thunk that throws
resolves to `null` (the call never rejects), so `.filter(Boolean)`. Use ONLY when you genuinely need
all results together.

### `log(message)` / `phase(title)`

`log` emits a narrator line above the progress tree; `phase` starts a new phase that subsequent
`agent()` calls group under.

### Globals

- **`args`** — the value passed to Workflow, verbatim. The tool may deliver it as a **JSON string** —
  parse-then-validate the shape (both Holosophus workflows do this in an ARGS GUARD; a missing/empty
  field throws loud instead of running a target-less panel).
- **`budget`** — `{ total: number|null, spent(), remaining() }`. The turn's token target; `total` is
  `null` if none set. A **hard ceiling** — once `spent()` reaches `total`, further `agent()` calls
  throw. Use for dynamic loops or static scaling.
- **`workflow(nameOrRef, args?)`** — run another workflow inline as a sub-step (shares the run's
  concurrency cap, counter, budget). **One level only** — nesting throws.

---

## `pipeline()` vs `parallel()` — the central design choice

**Default to `pipeline()`.** A barrier is correct ONLY when stage N needs cross-item context from all
of stage N-1: dedup/merge across the full set, early-exit on zero count, or a prompt that references
"the other findings". It is NOT justified by "I need to flatten/map/filter first" (do that **inside** a
pipeline stage) or "the stages are conceptually separate" (separate ≠ synchronized). Barrier latency is
real — if the slowest finder takes 3× the fastest, a barrier wastes the fast ones' idle time. When in
doubt: pipeline.

---

## Patterns (shapes you build, not tool features)

- **Adversarial verify** — spawn N skeptics per finding, each prompted to REFUTE; kill if a majority
  refute. Prevents plausible-but-wrong findings from surviving. (The build loop's skeptic verify pass.)
- **Perspective-diverse verify** — give each verifier a distinct lens (correctness, observability,
  grounding) instead of N identical refuters; diversity catches failure modes redundancy can't. (The
  `dev-evaluator` research panel.)
- **Judge panel** — generate N attempts from different angles, score with parallel judges, synthesize
  from the winner. Beats one-attempt-iterated when the solution space is wide.
- **Loop-until-dry / loop-until-converge** — keep iterating until K rounds find nothing new (dedup
  against *all seen*, not just confirmed, or judge-rejected findings reappear and it never converges).
  (The build loop's panel + grounding backstop.)
- **Multi-modal sweep** — parallel agents each searching a different way; useful when one angle won't
  find everything.
- **No silent caps** — if a workflow bounds coverage, `log()` what was dropped.

```js
// multi-stage default: each dimension verifies as soon as its review completes (no barrier)
const results = await pipeline(
  DIMENSIONS,
  d => agent(d.prompt, { phase: 'Review', schema: FINDINGS }),
  review => parallel((review.findings || []).map(f => () =>
    agent(`Adversarially verify: ${f.title}`, { phase: 'Verify', schema: VERDICT }).then(v => ({ ...f, v })))),
)
```

Scale to the request: "find any bugs" → a few finders, single-vote verify; "thoroughly audit" → a
larger pool, 3–5-vote adversarial pass, a synthesis stage.

---

## Constraints

- **Plain JavaScript, NOT TypeScript** — type annotations, interfaces, generics fail to parse.
- Standard JS built-ins are available **EXCEPT** `Date.now()` / `Math.random()` / argless `new Date()`,
  which **throw** (they'd break resume). Pass timestamps via `args` (Holosophus `author-specs` takes a
  `date` arg for the dated spec path); for randomness, vary the prompt/label by index.
- No filesystem or Node API access from the script body (subagents still have their normal tools).
- **Concurrency cap**: `min(16, cpu cores − 2)` concurrent `agent()` calls per workflow — excess
  queues. A single `parallel`/`pipeline` call accepts ≤ 4096 items; total agents per run ≤ 1000.

---

## Resume

The result includes a `runId`. Relaunch with `Workflow({ scriptPath, resumeFromRunId })` — the longest
unchanged prefix of `agent()` calls returns cached results instantly; the first edited/new call and
everything after runs live. Same script + same args → 100% cache hit. Stop the prior run first
(`TaskStop`); same-session only.

---

## Ultracode

`ultracode` flips the default from *ask before orchestrating* to *orchestrate by default*. The keyword
in one prompt opts that turn in; a system-reminder can confirm it is **on for the session** (standing
mode — author and run a workflow for every substantive task, token cost not a constraint; solo only on
conversational turns or trivial edits). When a reminder says it is **off**, revert to the opt-in rule.
