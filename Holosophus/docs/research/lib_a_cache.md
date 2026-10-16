# Cache-Side Literature Scan for Medium-Trajectory Agents

Scope: production LLM agent assistants on 5–15 turn trajectories, where the
dominant input-token cost is the per-turn replay of the trajectory. Focus is on
the **cache** side — what mechanisms exist, what invalidates them, and what
constraints they impose on any compaction scheme.

Compiled 2026-05-11 by a librarian sub-agent of the medium-traj research session.

## 1. Anthropic prompt caching (production)

Source: Claude API docs (platform.claude.com) — current as of 2026-05.

**Mechanism.** A request may carry up to **four** explicit `cache_control`
breakpoints, placed on individual content blocks across `tools`, `system`,
or `messages`. Caching strategies are **hierarchical and contiguous**: a
breakpoint always marks a *complete prefix* of the request, ordered
`tools → system → messages`. The system caches "everything up to and
including the marked block."

**The 20-block lookback.** A breakpoint can only match against prior cache
writes that occur within the **last 20 content blocks** before it. If a
conversation grows past 20 blocks since the last cache write, the lookup
silently misses — solved only by adding additional breakpoints.

**TTLs.** Default is **5-minute ephemeral**; an opt-in **1-hour extended**
TTL is available. Mixed TTLs must be ordered longest-TTL-first. Cache
refreshes at no cost when re-read within the window.

**Cost model.** Relative to base input price:
- 5-min cache write: **1.25×**
- 1-hour cache write: **2.0×**
- Cache read / refresh: **0.1×**
- Uncached input: 1.0×

Break-even versus uncached at the 5-min TTL is reached after roughly the
**second** read of the same prefix.

**Minimum cacheable size.** 1024 tokens (Sonnet 3.7/4, Opus 4/4.1), 2048
(Sonnet 4.6, Haiku 3.5), 4096 (Opus 4.5+ / Sonnet Mythos / Haiku 4.5).
Below the minimum, **caching silently no-ops** — check
`cache_creation_input_tokens` and `cache_read_input_tokens` to confirm.

**What invalidates.** Any change to a level invalidates that level and every
level after it. Specifically: tool-definition edits invalidate everything;
`tool_choice`, image add/remove, and thinking parameters invalidate
system+messages; non-tool-result user content can strip thinking blocks on
pre-Opus-4.5 models, breaking caches inside extended thinking.

**Auto-caching gotcha.** The "single top-level `cache_control`" mode places
the breakpoint on the *last cacheable block*. If that block changes every
turn (timestamps, per-turn metadata), the cache **never** hits — the prefix
hash changes each request.

**2025 best-practice consensus** (Spring AI blog, ProjectDiscovery,
mager.co, F5 Squashing Bugs, DigitalOcean): keep system prompts byte-stable;
push dynamic content (timestamps, session ids) to user messages or the *end*
of the system block; serialize JSON with sorted keys; place tool definitions
in a known stable order; pre-warm with `max_tokens=0` for latency-sensitive
paths.

## 2. OpenAI automatic prefix caching

Source: developers.openai.com prompt-caching guide, OpenAI Cookbook
"Prompt Caching 101/201". Live since Oct 2024.

- **Automatic** — no API change required.
- **Threshold:** 1024 tokens minimum, then hits granulate in **128-token
  increments** beyond the threshold.
- **Routing hash** is derived from approximately the **first 256 tokens**;
  same-prefix requests within an org route to the same back-end machine.
  Rate-overflow (>~15 req/min for the same prefix) causes routing to fall
  back to other machines and lose the warm cache.
- **TTL:** in-memory ~5–10 min idle, max ~1 hour standard; up to **24 h
  extended**.
- **Pricing:** "up to 80% latency reduction, up to 90% input-cost reduction"
  on cache hits.
- **Tool calls:** both `messages` and the `tools` array are part of the
  cached prefix; any tool-definition change invalidates the prefix.
- **Manual cache control:** none — no explicit breakpoints, no clear/purge.
  The optional `prompt_cache_key` parameter only influences routing.
- **Isolation:** per-organization.

## 3. vLLM, SGLang, RadixAttention

**vLLM Automatic Prefix Caching** (`--enable-prefix-caching`, stable since
v0.4). KV blocks are hashed by `(parent_hash, block_tokens, extras)` where
extras include LoRA id, multimodal hashes, and an optional cache salt. Hash
algorithms include SHA-256 and xxHash. Eviction is **LRU on block-level
free queue**, evicting reverse-completion-order so most-recent siblings die
last. Caches only **full blocks** (default block size 16 tokens). Works
under chunked prefill and integrates with prefix-aware scheduling in
llm-d-style deployments.

**SGLang RadixAttention** (arXiv 2312.07104). Maintains a radix tree
keyed at the **token** level (page size = 1 token in the paper), so prefix
matches are not block-aligned and shared sub-prefixes across siblings are
exploited. LRU eviction on leaf nodes; pairs with a **cache-aware
scheduling policy** that batches requests to maximize shared prefix
residency. Reported up to **5×** throughput on multi-turn / few-shot
workloads vs vLLM (LMSYS 2024 blog) and a consistent ~10% edge on
multi-turn chat per 2025 Runpod/Medium comparisons. Used in production
under DSPy, LLaVA, and DeepSeek V3/R1 day-one deployments.

**Block-level vs token-level.** vLLM's block-level hashing means a prefix
match requires alignment to block boundaries; SGLang's token-level radix
matches arbitrary cut-points, which matters for tool-call traces where
turn boundaries don't align to a 16-token grid.

**KVFlow** (arXiv 2507.07400, 2025). Workflow-aware KV cache management
for multi-agent setups: builds an Agent Step Graph, assigns each agent a
*steps-to-execution* value, and evicts at the radix-node level by future
reuse rather than LRU. Adds CPU→GPU **prefetch** in background threads.
Reports up to **1.83× / 2.19×** speed-ups over hierarchical-radix SGLang
on single and concurrent workflows.

## 4. KV-cache compression / eviction research

Foundational methods (still the baselines in 2025–2026 papers):

- **H2O** (NeurIPS 2023, 2306.14048) — "Heavy-Hitter" tokens retained by
  cumulative attention mass + recent window.
- **StreamingLLM** (2309.17453) — permanent attention-sink tokens (first
  few) + sliding window; fastest, lowest accuracy on long-context recall.
- **SnapKV** (2404.14469) — pre-fill-time compression using an
  "observation window" at the end of the prompt to predict importance.
- **Scissorhands, FastGen** — earlier importance-pruning baselines.

2025–2026 follow-ups worth knowing:

- **Ada-KV / Ada-SnapKV** (2407.11550) — adaptive per-head budget.
- **ChunkKV** (ICLR 2026 OpenReview) — compresses *semantic chunks*, not
  isolated tokens. Preserves linguistic structure.
- **CAKE** (2503.12491) — cascading + layer-preference-aware eviction.
- **CAOTE** (2504.14051) — attention-output-error-based selection.
- **LaProx** — reformulates eviction as output-aware layer-wise matrix
  approximation.
- **G-KV** (2512.00504) — decoding-time eviction.
- **EvicPress** (2512.14946) — joint compression + eviction.
- **MadaKV** (ACL 2025) — modality-perception eviction for multimodal.
- **"The Pitfalls of KV Cache Compression"** (2510.00231) — shows
  certain instructions degrade much faster than others under H2O / SnapKV
  / StreamingLLM; **system prompt leakage** and ignored multi-instruction
  prompts are real failure modes; recommends modest eviction-policy
  changes.
- **"Hold Onto That Thought"** (2512.12008) — KV compression on reasoning
  traces; long chains-of-thought are unusually fragile.

A central caveat (per "Pitfalls" + ChunkKV evaluations): **no single
compression method dominates** — on a corpus of reasoning tasks ~40% of
contexts prefer kvzip, ~40% prefer knorm, ~20% prefer SnapKV. Per-context
adaptation is required, which conflicts with cache reuse.

**The KV-compression / prompt-cache gap.** Every method above operates
**inside** the model, on its own KV buffers, on a single forward pass. None
of them survives across requests behind a provider's prompt-cache API — the
API hashes the *token prefix*, not the KV tensors. So compression research
helps a self-hosted vLLM/SGLang deployment but **does not help** an agent
that talks to Anthropic or OpenAI over the API. For those, only the
prefix-stability strategies of §1–2 matter.

## 5. Cache-aware prompt engineering for agents

The most directly relevant academic work is **"Don't Break the Cache: An
Evaluation of Prompt Caching for Long-Horizon Agentic Tasks"** (Lumer et
al., arXiv 2601.06007, 2026). Findings on DeepResearch-Bench, 500+ agent
sessions, 10k-token system prompts:

- Prompt caching reduces API cost by **41–80%** and TTFT by **13–31%**
  across providers.
- Benefits are **universal across prompt sizes 500–50k tokens and tool-call
  counts 3–50**, once minimums are met.
- Failure modes in agentic settings: dynamic content embedded in system
  prompts, traditional function-calling with dynamic parameters, and
  emitting changing tool results into the cached prefix.
- Recommended pattern: strategic breakpoint control, dynamic content at the
  *end* of system prompt, exclude dynamic tool results from cached prefix.

Reinforcing evidence from 2025 industry posts:

- **F5 / Squashing Bugs** (Aug 2025): identical system prompts achieved
  **85.2% hit rate**, **65% TTFT improvement**, **71.3% cost reduction**
  — vs 0% with per-request perturbations.
- **ProjectDiscovery** (2025): 74% → 84% hit rate by relocating dynamic
  content and explicit breakpoint placement; **59% cost cut**.
- **ThinkSmart.Life** local-inference note: 5k-token agentic session at
  turn 20 with invalid prefix caching is **~90% slower** on RTX 3090 (90
  tok/s → <10 tok/s).
- **llm-d** blog: prefix-aware scheduling = 57× faster response, 2× throughput.

**Confirmed gap.** I found no academic paper or production framework whose
*explicit* goal is "compaction that preserves cache." Compaction-side work
(MemGPT, ACON, Context-Folding, LongLLMLingua, etc.) treats the prefix as
mutable. Cache-side work (the above) treats compaction as out of scope.
This is the white space the framework targets.

## 6. Hierarchical / append-only / OOB-summary strategies

Closest extant ideas:

- **Context-Folding** (arXiv 2510.11967, Oct 2025) — agent branches into
  sub-trajectories, then "folds" completed subtasks by collapsing them to
  a summary + outcome. Folding is **deferred to sub-task boundaries**,
  which is structurally cache-friendly *within* a sub-task.
- **MemGPT-style** OS-paging — pages are append-only inside an episode,
  swap happens at the page boundary; the *active* page is potentially
  cache-stable but the swap event breaks the prefix.
- **Bill of Lading / Context Lifecycle** blog posts (Medium, Dec 2025) —
  argue for append-only working set with a *manifest* of out-of-band
  evictions, and a background process rebuilding the cache from the
  manifest and **atomic swap** when ready. This is the most cache-aware
  pattern in the wild but it's blog-level, not benchmarked.
- **JetBrains Research, Dec 2025** — "smarter context management":
  buffered eviction with deferred rewrites to coincide with natural
  prompt boundaries.

The pattern that emerges across these: **append until budget breach;
compact only at breach; place the breakpoint to amortize write cost; do the
compute out-of-band; swap atomically.**

## Design constraints for a cache-preserving compaction framework

Concrete invariants any candidate compaction scheme must respect, derived
from §1–6:

1. **Append-only between breaches.** Between budget-breach events, every
   new turn is *concatenated* to the existing prefix. No edits, no
   re-orderings, no key-sorting of prior content.
2. **Compact at breach, not per turn.** Rewriting the prefix burns a cache
   write (1.25–2.0× input). Amortize: trigger compaction only when the
   budget breach probability over the next K turns exceeds a threshold.
3. **Anchor breakpoints inside the 20-block lookback.** On Anthropic,
   place at most 4 breakpoints; ensure the conversation never grows >20
   content blocks past the last breakpoint without a new one.
4. **Quantize cache-write cadence to model minimums.** Sub-1024-token (or
   2048/4096 on larger Anthropic models) writes silently no-op. Don't
   bother caching until the prefix crosses the minimum.
5. **Stable JSON serialization, stable tool ordering.** Sort keys; freeze
   tool order; never inject timestamps or session ids into the cached
   region; place per-turn metadata only in the *last* user content block.
6. **OOB summarization with atomic swap.** Compute the summary in a
   background call; only on completion replace the older trajectory in a
   single new prefix, then re-warm with `max_tokens=0` before the next
   real turn.
7. **Forbid mid-conversation tool-set edits.** A tool-definition change
   invalidates everything below it. Tools must be declared up-front for the
   whole episode; new capabilities arrive via *content*, not new tools.

## Sources

- [Anthropic Prompt Caching docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [OpenAI Prompt Caching guide](https://developers.openai.com/api/docs/guides/prompt-caching)
- [vLLM Automatic Prefix Caching](https://docs.vllm.ai/en/stable/design/prefix_caching/)
- [LMSYS SGLang / RadixAttention blog](https://www.lmsys.org/blog/2024-01-17-sglang/)
- [KVFlow arXiv 2507.07400](https://arxiv.org/abs/2507.07400)
- [Don't Break the Cache (2601.06007)](https://arxiv.org/abs/2601.06007)
- [Pitfalls of KV Cache Compression (2510.00231)](https://arxiv.org/abs/2510.00231)
- [F5 / Ankit Bansal: KV-Cache Aware Prompt Engineering](https://ankitbko.github.io/blog/2025/08/prompt-engineering-kv-cache/)
- [Context-Folding (2510.11967)](https://arxiv.org/abs/2510.11967)
- [SGLang vs vLLM prefix caching comparison (Medium)](https://medium.com/byte-sized-ai/prefix-caching-sglang-vs-vllm-token-level-radix-tree-vs-block-level-hashing-b99ece9977a1)
- [Top 10 KV Cache Compression Techniques (MarkTechPost)](https://www.marktechpost.com/2026/04/29/top-10-kv-cache-compression-techniques-for-llm-inference-reducing-memory-overhead-across-eviction-quantization-and-low-rank-methods/)
- [ProjectDiscovery: 59% cost reduction with prompt caching](https://projectdiscovery.io/blog/how-we-cut-llm-cost-with-prompt-caching)
- [llm-d: KV-Cache Wins You Can See](https://llm-d.ai/blog/kvcache-wins-you-can-see)
