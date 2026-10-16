# Production agent frameworks — conversation-history reality (May 2026)

Scope: how each shipping framework handles message history *today* for
medium-trajectory (5–15 turn) tool-using agents. We assume the reader already
knows the academic compaction literature (Mem0, MemGPT, A-Mem, HippoRAG, ACON)
and focuses on default behavior, knobs, and concrete pain points pulled from
issues, docs, and community posts. The first integration target for the
proposed library is Vercel AI SDK (TypeScript), so it gets the most depth.

Compiled 2026-05-11.

---

## 1. Vercel AI SDK (`ai` npm, currently v5 GA / v6 preview)

**(a) Default behavior.** `streamText` / `generateText` are *pass-through*: the
`messages: ModelMessage[]` array the caller hands in is sent to the provider
verbatim after format conversion. There is no built-in trimming, summarization,
or token-budget enforcement. If the array exceeds the model context window, the
provider returns an error; the SDK surfaces it (e.g. `AI_APICallError:
"messages resulted in 209275 tokens"`). The `useChat` UI hook appends every
turn — including every `tool-call` and `tool-result` UI part — to a single
`UIMessage[]` array that lives in client state or in the route handler's
persistence callback. The `convertToCoreMessages` / `convertToModelMessages`
helper flattens UI parts into provider-shaped `ModelMessage` content arrays;
again, nothing is dropped.

**(b) Knobs.**
- `prepareStep({ messages, stepNumber }) => { messages, system, model }` —
  the only first-class hook to mutate history. Runs before each step of a
  multi-step / tool-loop call. This is the official recommended seam for
  manual compaction per the Vercel community thread.
- `experimental_prepareRequestBody` on `useChat` — lets the client decide what
  subset of UI history to send to the server.
- `toModelMessage` / per-part transformers — AI SDK 5 added these to
  customize how individual content parts (e.g. images, large tool results) are
  serialized; the maintainer has pointed at them as the "control surface" for
  oversized tool results but there is no token-budget primitive built on top.
- `stopWhen` (replacement for `maxSteps` in v5) — bounds tool-loop depth, not
  tokens.
- `providerOptions.anthropic.cacheControl` — per-tool / per-message cache
  breakpoint, set manually.

**(c) Pain points.**
- *No built-in compaction.* Vercel Community thread "How to implement
  conversation compaction with AI SDK v5" (Oct 2025): "There's no built-in
  conversation compaction in AI SDK v5, so managing it yourself is the way to
  go." The recommended pattern is to track a summary + cutoff index in client
  state and slice in `prepareStep`. Users explicitly request native support.
- *Tool-result overflow.* GitHub discussion #8193 "Handling tool results
  larger than the model's context window" — locked without resolution.
  Maintainer suggested `toModelMessage`; users replied that the docs only show
  image handling, no token-management example.
- *useChat freezes during tool loops.* Default UI shows nothing while tools
  are running; users misread it as "frozen". Tool-call and tool-result parts
  also balloon the UI message list independent of what is sent to the model.
- *Cost blow-up at modest scale.* DEV community "Lessons from 30 Days of Real
  Traffic": 50 sessions × 50 messages × 200 tokens ≈ 500k tokens/request at
  steady state; no built-in cap, no rate-limit hook.
- *Programmatic tool calling (AI SDK 6 preview)* lets Claude call tools from
  inside a code-execution environment so intermediate results never enter the
  message array — a partial workaround that only works with the Claude
  code-execution beta, not general tools.

```ts
// The current "compaction" pattern in AI SDK v5 — done by hand.
const result = streamText({
  model,
  messages: clientMessages,
  prepareStep: async ({ messages, stepNumber }) => {
    if (estimateTokens(messages) < 80_000) return { messages };
    const { summary } = await summarize(messages.slice(0, -6));
    return { messages: [{ role: "system", content: summary }, ...messages.slice(-6)] };
  },
});
```

---

## 2. LangChain / LangGraph

The pre-LCEL `Conversation*Memory` classes are deprecated. The 2026
recommended path is LangGraph state with `add_messages` reducer plus one of:

**(a) Default behavior.** `add_messages` appends every turn — including
`ToolMessage` blocks — to `state["messages"]`. The default `create_react_agent`
loop has no automatic trimming; long tool loops accumulate state until the
checkpointer or the LLM rejects it (issue #3717: 142k tokens after retrievers
ran several times).

**(b) Knobs.**
- `trim_messages(messages, max_tokens=…, strategy="last", token_counter=…,
  allow_partial=False, start_on="human", include_system=True)` —
  LangChain-core utility, sliding window with structural constraints (won't
  leave an orphan tool_result).
- `RemoveMessage(id=…)` and `REMOVE_ALL_MESSAGES` constant — declarative
  deletes that flow through the `add_messages` reducer.
- `summarize_messages` (langchain-core) — generates a summary that replaces a
  prefix; you wire it into a graph node that emits `RemoveMessage`s plus a new
  `SystemMessage`.
- LangGraph **middleware** (1.0, late 2025): `SummarizationMiddleware`,
  `pre_model_hook`, `post_tool_hook`. Recommended-today pattern.
- `langmem` — separate package: "hot-path" tool the agent can call
  (`manage_memory_tool`), background extractor, semantic/episodic/procedural
  long-term stores.

**(c) Pain points.**
- *Tool-call/tool-result invariants are easy to violate.* Issue #5109:
  "`tool_use` ids were found without `tool_result` blocks immediately after"
  — happens when summarization or `trim_messages` cuts between an assistant
  tool-call and its result. Anthropic and Bedrock providers reject this hard.
- *Checkpointer cost.* Issue #3717: the Postgres checkpointer stores the full
  message list, so old tool results get re-replayed; users had to bolt on
  custom pruning.
- *Two parallel APIs.* `trim_messages` vs. `RemoveMessage` vs. middleware
  `SummarizationMiddleware` vs. `langmem` — community posts (Focused, Towards
  Dev) note the choice is non-obvious and the recipes don't share types.
- *Roll-your-own importance.* Nothing in core ranks tool results by
  usefulness; the official "how_to_fix_your_context" repo demonstrates pruning
  but it is sample code, not a reusable primitive.

```python
from langgraph.prebuilt.chat_agent_executor import create_react_agent
from langchain_core.messages.utils import trim_messages

def pre_model_hook(state):
    trimmed = trim_messages(state["messages"], max_tokens=60_000,
                            token_counter=count_tokens, start_on="human",
                            allow_partial=False)
    return {"llm_input_messages": trimmed}

agent = create_react_agent(model, tools, pre_model_hook=pre_model_hook)
```

---

## 3. LlamaIndex

**(a) Default.** `AgentWorkflow` / `FunctionAgent` use a `Memory` object
(unified class as of v0.12, late 2025) that keeps a FIFO of `ChatMessage`s
inside a token budget. When the budget is hit, oldest messages are dropped.
Tool messages stay anchored to their parent assistant message so the API
contract isn't broken.

**(b) Knobs.**
- `Memory.from_defaults(token_limit=30000, chat_history_token_ratio=0.7,
  token_flush_size=…)` — short-term FIFO with a soft target.
- `MemoryBlock` plug-ins: `StaticMemoryBlock`, `FactExtractionMemoryBlock`,
  `VectorMemoryBlock` — each has a `priority` and `truncate()` method; when
  the block budget is exceeded the framework calls `truncate()` in priority
  order. This is closer to a real budgeting framework than LangChain's.
- Deprecated but still shipped: `ChatMemoryBuffer`, `ChatSummaryMemoryBuffer`,
  `VectorMemory`.

**(c) Pain points.**
- *No global tool-result importance.* Truncation is FIFO inside short-term
  memory; a single huge `search_index` result will not be downranked relative
  to a small useful one.
- *Serialization friction.* Issue #19128: there's no canonical persistence
  format for `Memory` + `ChatStore` SerDes — users hit it when moving sessions
  between processes.
- *Tool-call summarization is recent and partial.* Issue #14014 fixed
  `ChatSummaryMemoryBuffer` choking on assistant messages that contain tool
  calls; the new `Memory` class inherits the fix but extraction blocks
  (`FactExtractionMemoryBlock`) still see structured tool calls as opaque
  text.

---

## 4. Claude Agent SDK / Anthropic Messages API

Two distinct features land in 2025:

**Server-side context editing** (`context_management` request param,
strategy `clear_tool_uses_20250919`): when the input-token count crosses a
threshold the *API itself* replaces old `tool_result` (and optionally
`tool_use`) blocks with a placeholder. Params: `trigger.value`,
`keep.value` (n most recent tool uses to keep), `clear_at_least`,
`clear_tool_inputs`, `exclude_tools[]`.

**Client-side compaction** (`compaction_control` in
`client.beta.messages.tool_runner`): when token usage exceeds
`context_token_threshold`, the SDK injects a "summarize what you've done"
user turn, captures `<summary>…</summary>`, then resumes with
`[original_first_user, summary_as_user]`. Tool-use blocks that were mid-flight
when compaction triggers are removed and Claude re-issues the call after
resuming.

**(c) Pain points.**
- *Compaction is whole-transcript and lossy.* Substack analysis ("Context
  Editing Looks Like a Feature…") frames it as "a garbage collector without
  write barriers": the agent has no way to mark a tool result as load-bearing
  before the GC runs.
- *Server-side clearing breaks Anthropic prompt-caching breakpoints.* The
  prefix hash changes the moment the API rewrites a `tool_result`, so the
  20-block lookback window for cache breakpoints typically misses. Doc
  warning: changes to `tool_choice` or image presence invalidate cache too.
- *Only Anthropic-specific.* Neither feature works for OpenAI or Gemini
  endpoints, so multi-provider apps cannot rely on it.
- *No cross-session memory.* Compaction summary lives in the next request's
  message array; persistence is the caller's problem.

```python
runner = client.beta.messages.tool_runner(
    model="claude-sonnet-4-6", tools=tools, messages=messages,
    compaction_control={"enabled": True, "context_token_threshold": 100_000,
                        "model": "claude-haiku-4-5"},
)
```

---

## 5. OpenAI Assistants API + Responses API

**Assistants API (threads).** `truncation_strategy` on a Run:
`{"type": "auto"}` drops messages from the *middle* of the thread to fit
`max_prompt_tokens`; `{"type": "last_messages", "last_messages": N}` keeps the
N most recent. Thread cap: 100k messages. No summarization, no
importance signal, no tool-aware splitting — users have explicitly asked for
"smarter controls" (community thread, 2024).

**Responses API (2025, replacing Assistants long-term).** State is opt-in:
`store: true` plus `previous_response_id` chains turns server-side; the same
`truncation: "auto" | "disabled"` parameter applies (default disabled).
`max_output_tokens` caps output. `include` / `included_tool_outputs` let you
read tool outputs back from a stored response. Tool calls live as
`function_call` and `function_call_output` items in the items array, and
truncation operates on items, not tokens within an item — meaning a single
huge tool output is either fully included or fully dropped.

**(c) Pain points.**
- *No middle-out preservation of tool pairs.* `auto` truncation can drop a
  function_call_output while keeping the function_call, producing the same
  orphan-tool error LangGraph sees.
- *Realtime API "auto" is undocumented* (community thread, May 2025).
- *Storage persistence is murky.* Docs-issue thread asks how long Responses
  storage lives, default scoping, deletion semantics.

---

## 6. AutoGen v0.4+

**(a) Default.** `ChatCompletionContext` base class manages the per-agent
history view. The default in `AssistantAgent` is `UnboundedChatCompletionContext`
— keep everything. Users opt into `BufferedChatCompletionContext(buffer_size=N)`
(last-N messages), `HeadAndTailChatCompletionContext(head=2, tail=10)` (keep
system + recent), or `TokenLimitedChatCompletionContext`.

**(b) Knobs.** Subclass `ChatCompletionContext` and override `get_messages()`.
Separate `Memory` protocol (`update_context(model_context)`) for RAG-style
injection — `ListMemory`, `Mem0Memory` plug-ins.

**(c) Pain points.** No tool-pair awareness in `BufferedChatCompletionContext`;
the buffer can split a tool_use/tool_result. Migration guide notes this is an
intentional "bring your own strategy" surface — no battery-included
summarizer.

---

## 7. CrewAI

Four memories: **short-term** (per-crew-run, ChromaDB+RAG, semantic recall of
intra-run messages), **long-term** (cross-run, SQLite, "lessons learned"),
**entity** (knowledge-graph-ish entity tracking, ChromaDB), and **contextual**
(automatic combiner that injects relevant pieces at the start of each task).

Knobs: `memory=True` flag on `Crew`, `embedder=…`, `recency_weight`,
`semantic_weight`, `importance_weight`, `recency_half_life_days`. Mem0
provider integration for hosted long-term store.

Pain points (community forum + docs): contextual memory is *not configurable*
— users routinely ask how to disable a specific injection; long-term memory
persistence has hit DB-locking issues on concurrent crews; the four-memory
abstraction does not address the in-task tool-result blow-up problem at all —
once a task is running, every tool result lands in the same prompt buffer
with no trimming policy.

---

## 8. Mastra (TypeScript)

Three memory types, all wired through `new Memory({...})` and attached to an
`Agent`: **working memory** (structured Markdown scratchpad injected into the
system prompt; agent rewrites between turns), **semantic recall** (vector
search over past messages, scope `resource` by default), and **observational
memory** (April 2026 release: two background agents — Observer + Reflector —
compress old messages into dense observations, scope `thread`, defaults to
`google/gemini-2.5-flash`).

Observational memory is the only mainstream TS framework that ships a
background-compaction pipeline out of the box; Mastra claims 94.87% on
LongMemEval (gpt-5-mini). The API is a single flag:

```ts
new Memory({ options: { observationalMemory: true } })
```

Gaps: observational memory is thread-scoped and runs in the background, so
in-trajectory tool-result bloat during a single 5–15 turn agent run is not
addressed — the compression catches up after the fact. Working memory is
unstructured Markdown the agent must self-discipline to maintain.

---

## 9. Letta (MemGPT productionized)

Two server-managed stores per agent: **recall memory** (full conversational
log, searchable via `recall_search` tools the agent calls itself) and
**archival memory** (vector store for facts/data, agent reads/writes via
`archival_insert` / `archival_search`). In-context **core memory** blocks
(persona, human, custom) are editable by the agent via tool calls and live in
the system prompt; when the active context fills, Letta pages older messages
out to recall.

The model is "OS pages memory to disk" — the agent decides what to
re-page in. Strong for long-horizon assistants, heavy for 5–15 turn
tool-using agents (every paging decision is an extra LLM call). REST API +
Python/TS SDKs.

---

## 10. Agno (formerly Phidata)

Memory primitives: session storage (SQLite/Postgres), `AgentMemory` (recent
runs + summaries), `UserMemory` (long-term per-user facts created either by
the agent itself or by a background `MemoryManager`). Vector backends via
LanceDB/pgvector for long-term recall. Issue tracker (#1696) shows the
memory write path is provider-specific — Gemini models fail summarization
silently. Overall behavior is closest to CrewAI: per-session FIFO + optional
long-term, no in-task tool-result budgeting.

---

## Common gaps in production frameworks (what a new library could fill)

1. **No framework exposes tool-result-importance hooks that survive
   compaction.** Every "drop oldest" / "summarize prefix" strategy treats
   tool_result blocks as opaque tokens. There is no per-result importance
   score, no `keep_until` marker, no "this was load-bearing — re-fetch
   instead of summarizing" annotation. Anthropic's context editing comes
   closest (`exclude_tools[]`) but at tool-name granularity, not per-call.
2. **No cache-breakpoint-aware compaction.** Anthropic prompt-caching only
   tolerates four breakpoints with a 20-block lookback, and any rewrite of
   `tool_result` content within the breakpoint invalidates the prefix. No
   framework's trimmer/summarizer is aware of where caller-set
   `cacheControl` markers sit, so compaction routinely costs more than it
   saves on Sonnet/Opus workloads.
3. **No tool-pair safety primitive that's portable.** LangGraph
   (`start_on="human"`, `allow_partial=False`), LlamaIndex (anchored tool
   messages), and Anthropic's compactor each invent their own way of not
   producing orphan tool_use blocks; OpenAI Assistants `auto` truncation
   still emits orphans. A small TS/Py library that exposes "given a
   `ModelMessage[]`, return a valid contiguous subset honoring constraints
   X, Y, Z" would slot under Vercel AI SDK, LangChain, and direct Anthropic
   callers identically.
4. **No medium-trajectory tier.** Mem0/MemGPT/Letta target long-horizon
   stateful assistants (hundreds of sessions); LangChain/LlamaIndex token
   trimmers target single-turn windows. Nothing targets the sweet spot of
   5–15 tool-heavy turns where one big tool result, not history length, is
   the failure mode. Most papers in this band (ACON, A-Mem) have no shipping
   library equivalent.
5. **No cross-framework `ModelMessage` adapter.** Vercel's `ModelMessage`,
   LangChain's `BaseMessage`, LlamaIndex's `ChatMessage`, Anthropic's
   `MessageParam`, OpenAI's items list — all encode tool calls differently.
   A normalized representation that round-trips would let one compaction
   policy serve all four ecosystems.
6. **No "compaction transcript" / replay log.** Once Anthropic's tool_runner
   rewrites history, there is no first-class artifact recording what was
   dropped, why, with which prompt and model. Debugging a hallucinated
   reference to a vanished tool result is currently a forensic exercise on
   raw API logs.
7. **No backpressure / streaming-aware compaction.** Vercel `streamText`,
   LangGraph `astream`, LlamaIndex `astream_events` all emit tool-call /
   tool-result deltas live; compaction libraries assume a static
   `messages[]`. A streaming-aware trimmer that can decide *before* the
   next provider call (in `prepareStep` / `pre_model_hook` / middleware) is
   the integration surface every TS-first agent app actually needs.

---

## Sources

- [AI SDK 5 — Vercel blog](https://vercel.com/blog/ai-sdk-5)
- [AI SDK 6 — Vercel blog](https://vercel.com/blog/ai-sdk-6)
- [Vercel AI discussion #8193 — tool results larger than context window](https://github.com/vercel/ai/discussions/8193)
- [Vercel community — conversation compaction with AI SDK v5](https://community.vercel.com/t/how-to-implement-conversation-compaction-with-ai-sdk-v5/29171)
- [Vercel community — minimizing token usage and caching tool schemas](https://community.vercel.com/t/minimizing-token-usage-and-caching-tool-schemas-with-vercel-ai/27518)
- [DEV — useChat in Production: Streaming, Errors, Patterns](https://dev.to/whoffagents/vercel-ai-sdk-usechat-in-production-streaming-errors-and-the-patterns-nobody-writes-about-4ecf)
- [LangChain docs — Memory (LangGraph)](https://docs.langchain.com/oss/python/langgraph/add-memory)
- [LangGraph issue #3717 — token-limit overflow with checkpointer](https://github.com/langchain-ai/langgraph/issues/3717)
- [LangGraph issue #5109 — tool_use without tool_result blocks](https://github.com/langchain-ai/langgraph/issues/5109)
- [langchain-ai/how_to_fix_your_context](https://github.com/langchain-ai/how_to_fix_your_context)
- [LangMem docs](https://langchain-ai.github.io/langmem/)
- [LlamaIndex — Memory module](https://developers.llamaindex.ai/python/framework/module_guides/deploying/agents/memory/)
- [LlamaIndex — ChatSummaryMemoryBuffer](https://developers.llamaindex.ai/python/examples/agent/memory/summary_memory_buffer/)
- [LlamaIndex issue #19128 — Memory/ChatStore SerDes](https://github.com/run-llama/llama_index/issues/19128)
- [Anthropic Cookbook — Automatic context compaction](https://platform.claude.com/cookbook/tool-use-automatic-context-compaction)
- [Anthropic Cookbook — Context engineering: memory, compaction, tool clearing](https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools)
- [Anthropic — Context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing)
- [Anthropic — Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- ["Context Editing Looks Like a Feature…" (Substack analysis)](https://conikeec.substack.com/p/context-editing-looks-like-a-feature)
- [OpenAI — Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)
- [OpenAI Assistants — Deep Dive (truncation_strategy)](https://developers.openai.com/api/docs/assistants/deep-dive)
- [OpenAI community — Add smarter truncation controls](https://community.openai.com/t/add-smarter-controls-to-truncate-thread-chat-history-assistant-api-runs-api/844919)
- [AutoGen — Model Context](https://microsoft.github.io/autogen/stable//user-guide/core-user-guide/components/model-context.html)
- [AutoGen — BufferedChatCompletionContext source](https://microsoft.github.io/autogen/stable/_modules/autogen_core/model_context/_buffered_chat_completion_context.html)
- [CrewAI — Memory docs](https://docs.crewai.com/en/concepts/memory)
- [Mastra — Observational Memory](https://mastra.ai/docs/memory/observational-memory)
- [Mastra blog — Announcing Observational Memory](https://mastra.ai/blog/observational-memory)
- [Letta — Memory management](https://docs.letta.com/advanced/memory-management/)
- [Letta — MemGPT concepts](https://docs.letta.com/concepts/memgpt/)
- [Agno (Phidata) — Memory docs](https://docs.phidata.com/agents/memory)
- [Agno issue #1696 — memory storage fails with Gemini models](https://github.com/agno-agi/agno/issues/1696)
