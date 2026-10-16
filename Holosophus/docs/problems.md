# Known Problems & Gotchas

SDK / MCP / Langfuse pitfalls discovered while integrating the Claude Agent
SDK with Langfuse tracing, plus a small set of open follow-ups that are
tracked here rather than in an external issue tracker. New items go to the
matching section; close an item by removing it (history stays in `git log`).

## SDK Behavior

### 1. SDK hangs on remote MCP servers via `setting_sources`
When using `setting_sources=["project"]`, the SDK reads `.mcp.json` and tries to connect to all MCP servers including remote ones (AlphaXiv). Remote HTTP MCP servers can cause the SDK to hang during initialization (5+ min with no output).

**Workaround**: Pass MCP servers explicitly via `mcp_servers={}` in `ClaudeAgentOptions`, including only the servers you need. Skip remote servers like AlphaXiv for SDK-driven runs.

### 2. `stop_reason` is always `None` in SDK stream
`AssistantMessage.stop_reason` is never set — it's `None` for every message in the stream. You cannot rely on it to detect the "final block" of an API call.

**Impact**: Token accumulation logic must use `message_id` changes (new message_id = new API call) instead of stop_reason.

### 3. Sub-agent AssistantMessages leak into parent stream
When a sub-agent runs, its `AssistantMessage`s appear in the parent's `async for message in query(...)` stream. They can be distinguished by `parent_tool_use_id` being set (non-None).

**Fix**: Filter out messages with `parent_tool_use_id` in `handle_message()`.

### 4. SDK streams one AssistantMessage per content block
Each content block (TextBlock, ThinkingBlock, ToolUseBlock) arrives as a separate `AssistantMessage` with the same `message_id`. They must be accumulated into a single API call before processing.

### 5. Hooks fire before stream messages arrive
`PreToolUse` and `SubagentStart` hooks fire BEFORE the corresponding `AssistantMessage` blocks arrive in the stream. This means `_current_gen_id` isn't set yet when the hook needs a parent.

**Fix**: Retroactive parent updates — create spans with `parent=None`, then update `parentObservationId` when the generation is eventually created.

### 6. Token counts with heavy caching
`input_tokens` can be very small (3-7) when most of the prompt is cached. Real input = `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`. Always sum all three for Langfuse `promptTokens`.

### 7. SubagentStart `tool_use_id` differs from PreToolUse `tool_use_id`
The `tool_use_id` passed to `SubagentStart` hook is a different UUID from the one in `PreToolUse` for the Agent tool call. Cannot use it directly to find the parent tool span.

**Fix**: Scan `_open_spans` for the most recently created `tool:*` span.

## MCP Servers

### 8. AlphaXiv requires OAuth and browser flow
AlphaXiv MCP at `https://api.alphaxiv.org/mcp/v1` requires OAuth authentication via browser. Works in interactive Claude Code sessions (via `/mcp` command) but not reliably in SDK-driven runs.

### 9. `.mcp.json` type must be `"http"` not `"sse"`
AlphaXiv and other remote MCP servers must use `type: "http"`. The `"sse"` transport type is deprecated in Claude Code.

## Langfuse

### 10. Langfuse v2.95 REST API — batch size limits
Sending too many events in one batch (50+) during JSONL parsing can cause slow responses. Use batch threshold of ~15 events with flush-at-end for JSONL parsing.

### 11. `generate_research_proposals` tool schema mismatch
Proposer sends `chain_of_reasoning` as a string, but the tool expects a full `ResearchProposal` dict with fields: `abstract`, `risks_and_limitations`, `chain_of_reasoning`, etc. 14/18 calls failed with Pydantic validation errors.

**Fix needed:** Update proposer.md with correct tool input schema. Or make the proposer use its own reasoning instead of relying on a broken MCP tool.

### 12. Langfuse langsmith bridge: no session_id, no error highlighting, no agent transitions
- `sessionId` is always None (langsmith doesn't propagate it)
- Failed tool calls all show `level=DEFAULT` (not ERROR)
- Agent transitions (librarian→proposer) look identical to regular tool calls in timeline

**Status:** Limitation of langsmith bridge. Custom tracer (v2) handles these better.

### 13. Manager context bloat after sub-agent returns
After librarian finishes (30 papers, 212 tool calls), the ENTIRE conversation history stays in manager's context: 107K+ tokens, $0.54/turn. The proposer then runs against this bloated context unnecessarily.

**Root cause:** SDK/Claude Code accumulates all sub-agent tool_results into the parent conversation. There's no automatic summarization or context pruning between agent calls.

**Impact:** Cost ($3.37 for a 3-turn research pipeline), potential quality degradation from irrelevant context.

**Possible fixes:**
- Manager could explicitly summarize librarian output before delegating to proposer
- SDK could support a `compact_after_agent` option
- Custom hook on SubagentStop to trigger context compaction

### 14. SDK `mcp_servers` parameter hangs on connect (mitigated)
Earlier observation: passing `mcp_servers={"academia": {"type": "http", "url": "http://localhost:5056/mcp"}}` could cause the SDK subprocess to hang at startup with 0% CPU. The current `scripts/run_research.py` wires MCP servers explicitly through `ClaudeAgentOptions(mcp_servers=...)` and runs without hangs; the original failure mode has not been reproduced after the SDK 0.1.51 bump.

**If a hang reappears**: fall back to `setting_sources=["project"]` for interactive sessions (subject to problem #1 with remote servers), or strip the offending entry from the `mcp_servers` dict to localise it.

### 15. Cost shows $0.00 in Langfuse
Langfuse doesn't know Claude model pricing. `calculatedTotalCost` is always None/0. Cost data only available in trace metadata (from `ResultMessage.total_cost_usd`).

## Open Follow-ups

### Chemistry SDK roles shadowed by project agent files (fixed)

With Claude Code 2.1.220 / Python SDK 0.1.72, an SDK-initialized `mle-solver`
can be replaced at delegation time by `.claude/agents/mle-solver.md` from the
project. The chemistry manager then sees the chemical MCP, while its specialist
gets the general ML prompt/tools and `No such tool available` for a chem call.
Seeing the chemistry definition in the initialize handshake is insufficient.

Both chemistry entry points now set `setting_sources=[]`: they already pass
their prompts, agent definitions, MCP configuration and hooks explicitly.
This also disables implicit user/project settings and their hooks for these
runs; explicit SDK hooks, MCPs, native tools and prompt-linked skill files stay
available. General runs retain their existing setting sources.

A deterministic local model-API fixture reproduced the failure with the old
setting and a real MCP; the same call returned CCO mass 46.069 and LogP -0.0014
with the fix. No external model was called. Missing `ToolSearch` was ruled out:
in this CLI, a specialist without it receives its permitted MCP schemas upfront
when the correct agent definition is used.

### F1. Proxy wrapper for `get_paper_content` oversized results
`mcp__alphaxiv__get_paper_content` falls back to raw full text (~100-185KB) when no AI report exists. This exceeds Claude Code's tool result size limit, spills to a temp file, and agents waste 5-10 turns reading it in chunks.

**Current workaround (prompt-level):** agents are instructed not to read chunks and to fall back to `answer_pdf_queries` + `arxiv_download`.

**Better fix:** Write a proxy MCP tool (or middleware in `academia_mcp`) that:
1. Calls `get_paper_content`.
2. If the result exceeds N chars (e.g. 30K), truncates to abstract + first N chars, or calls an LLM to summarise.
3. Returns a manageable-size result.

**Trigger:** if the prompt-level workaround proves insufficient (agents still waste turns on oversized results).
