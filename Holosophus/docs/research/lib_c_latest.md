# Deep-Read Notes: Context Management for Tool-Using LLM Agents (Nov 2025 – May 2026)

Reader is assumed familiar with Mem0, MemGPT, A-Mem, HippoRAG, ACON, Complexity Trap, LongLLMLingua, LLMLingua, LongMemEval, LoCoMo, τ-bench, and Zep — those are not re-summarized.

Each paper uses the 7-field template:
**(a) mechanism** · **(b) tool_call/tool_result preservation & dependency awareness** · **(c) online vs offline** · **(d) code/language** · **(e) eval (benchmarks, n, driver model)** · **(f) headline number** · **(g) what they DON'T address**

Compiled 2026-05-11 by a librarian sub-agent of the medium-traj research session.

---

## 1. PAACE — arXiv 2512.16970 — "PAACE: A Plan-Aware Automated Agent Context Engineering Framework"

(a) PAACE is a **plan-conditioned, function-preserving compressor** trained by distillation. A separate "teacher" pipeline (PAACE-Syn) generates synthetic agent trajectories annotated with step-wise compression supervision; it then distils a family of small compressors (PAACE-FT) that, at inference, take the current plan, the running context, and the **next-k tasks** and emit a compressed context. The four ingredients are: next-k-task relevance scoring, plan-structure analysis, instruction co-refinement, and function-preserving compression (i.e. the compressor must keep enough information that downstream steps still execute).
(b) The abstract mentions "cumulative dependency" as a metric they explicitly *reduce*, and "function-preserving compression" implies the compressor is supposed to keep tool-result content that future tool calls require — but the paper does not present a formal dependency-tracking model (no per-tool-call dependency graph, no symbolic tracking of variable bindings). It treats dependency preservation as a learned, distilled behavior rather than a structural guarantee.
(c) **Hybrid.** Training/distillation is offline; the distilled compressor runs **online** during agent execution, invoked step-wise.
(d) Repo/language not advertised in the abstract; AlphaXiv lists it but no public GitHub link.
(e) AppWorld, OfficeBench, 8-Objective QA. Driver model and n not disclosed in abstract.
(f) "PAACE-FT retains 97 % of teacher performance while reducing inference cost by >10×"; "higher accuracy than all baselines while lowering peak context."
(g) No discussion of: prompt-cache friendliness (compressor rewrites prefix → invalidates KV cache); concurrency / multi-agent; framework-agnostic deployment; zero-config use without teacher-corpus generation; latency overhead of running the compressor itself.

## 2. SUPO — arXiv 2510.06727 — "Scaling LLM Multi-turn RL with End-to-end Summarization-based Context Management"

(a) **Periodic summarization baked into the RL policy.** During rollout, when the running context approaches a threshold, the policy emits a `summarize` action: a model-generated summary replaces the tool-use history. The RL gradient is derived such that both the *primary tool-use* tokens and the *summarization* tokens are optimized end-to-end under one policy (no separate summarizer model, no off-policy correction kludges).
(b) Tool-call/tool-result pairs are not structurally preserved — they are absorbed into free-text summaries. The agent learns *what to keep* through reward signal, but there is no explicit dependency tracking. If a tool result is needed downstream and the summarizer drops it, the only recourse is re-issuing the tool call.
(c) **Online**, by construction — summarization is an action the policy takes mid-trajectory. Offline only in the sense that the RL training itself is a batch process.
(d) ByteDance Seed / Stanford / CMU project page (miaolu3.github.io) hosts the PDF; no public code release confirmed yet. Python (verl / OpenRLHF-style training stack implied).
(e) Interactive function calling (BFCL-style multi-turn) and search (BrowseComp-Plus). Driver: Qwen2.5-7B/14B-Instruct as student policy; teacher / data-gen details not in abstract.
(f) **+14.0 % absolute accuracy on BrowseComp-Plus** vs no-summarization baseline; generalises to more turns than seen at training time, while keeping working context constant.
(g) Does NOT address: prompt-cache reuse (summaries rewrite the prefix — cache-hostile); zero-shot deployment to non-RL-trained models; deterministic / framework-agnostic compaction; tool-result dependency graphs.

## 3. MemOS — arXiv 2505.22101 — "MemOS: An Operating System for Memory-Augmented Generation"

(a) Defines three memory types — **parametric** (weights), **activation** (KV/context), **plaintext** (RAG/notes) — and wraps each as a `MemCube`: a uniform descriptor with metadata, lifecycle, and access policy. The "OS" layer (`MemReader`, `MemScheduler`, `MemLifecycle`, `MemGovernance`, `MemStore`, `MemVault`) decides *which* memory is touched at any step and orchestrates transitions (e.g., promote plaintext → parametric via fine-tune; demote activation → plaintext via summarization).
(b) Memory primitives are *content-typed* but not *tool-call-typed*. No first-class notion of "tool_call_id → tool_result_id" linkage; tool results would live in plaintext memory as undifferentiated chunks.
(c) **Online runtime** with batch lifecycle jobs. Offline-style operations (promotion/distillation) happen asynchronously.
(d) Code at `github.com/MemTensor/MemOS`. Python.
(e) Position/architecture paper — short version is light on benchmarks; companion full version reports LongMemEval / LoCoMo gains, and the GitHub README claims "35.24 % token savings."
(f) Headline: standardized memory abstraction across three types; ~35 % token savings on the repo's benchmark suite.
(g) Does NOT address: medium-trajectory tool-use traces specifically; cache preservation; the data-dependency structure between consecutive tool calls; latency budget of memory routing.

## 4. MemoryOS — arXiv 2506.06326 — "Memory OS of AI Agent" (BUPT + Tencent AI Lab)

(a) Three-tier hierarchy mimicking OS memory paging: **short-term** (recent dialogue turns, FIFO), **mid-term** (segmented "pages" of dialogue chains; promoted from short-term once the chain ends or the buffer fills), **long-term personal memory** (per-user facts/preferences extracted from mid-term segments). Four modules: Storage, Updating, Retrieval, Generation.
(b) Designed for **dialogue**, not tool-use traces. There is no tool-call concept; "observations" in the agent sense map onto user turns. Promotion uses dialogue-chain segmentation, not tool-call boundaries.
(c) **Online** — every turn triggers the update/retrieve cycle.
(d) Open source at `github.com/BAI-LAB/MemoryOS`. Python.
(e) LoCoMo; driver = GPT-4o-mini; baselines include Mem0, MemGPT, LangMem.
(f) +49.11 % F1 / +46.18 % BLEU-1 vs baselines on LoCoMo with GPT-4o-mini.
(g) Does NOT address: tool-call/tool-result preservation; coding-agent traces; cache-friendliness; medium-trajectory tool-use; the dependency-bearing nature of structured tool outputs.

## 5. Context-Folding (FoldAgent) — arXiv 2510.11967 — ByteDance Seed / CMU / Stanford

(a) The agent is taught to **issue explicit fold / unfold actions**. When tackling a subtask, it `branch`-es into a sub-trajectory; on `return` it *folds* — the sub-trajectory is removed from active context and replaced by a one-line outcome summary the agent itself wrote. So the active context resembles a recursive call stack rather than a flat ReAct log. Training is `FoldGRPO` — process rewards reward (i) tasks completed per fold, (ii) compactness of returned summary, (iii) end-task success.
(b) Tool calls live *inside* a fold while it is open and disappear when it closes — only the agent-authored summary survives. Tool-result dependencies are honored if and only if the agent (a learned policy) decides to surface them in the summary. No mechanical guarantee.
(c) **Online** — folding is an inference-time action.
(d) `github.com/sunnweiwei/FoldAgent`. Python.
(e) Deep Research, SWE-bench-style coding tasks; driver Qwen-base.
(f) "Matches or outperforms ReAct baselines using **10× smaller active context**"; outperforms summary-only baselines.
(g) Does NOT address: cache preservation (every fold rewrites context tail); zero-shot use with non-RL-trained drivers; non-decomposable trajectories (where there is no clean subtask boundary); long-tail dependencies that cross many folds.

## 6. MOOM — arXiv 2509.11860 — "Maintenance, Organization and Optimization of Memory in Ultra-Long Role-Playing Dialogues"

(a) **Dual-branch memory plugin** for role-play. Branch 1: multi-time-scale plot-conflict summarizer. Branch 2: incremental user-character profile. A "competition–inhibition" forgetting rule caps capacity by suppressing low-utility memories when new ones are written.
(b) Pure dialogue memory; no tool-call concept.
(c) Online, per-turn.
(d) Repo `github.com/AgentMemory/Huaman-Agent-Memory`. Chinese-language focus; Python.
(e) ZH-4O (their new Chinese ultra-long dataset, avg 600 turns); baselines: Mem0/MemGPT/MemoryOS/LangMem.
(f) SOTA on ZH-4O with fewer LLM calls; concrete metrics in paper body (not in abstract).
(g) Does NOT address: tool-using agents, English/code domains, cache reuse, dependency tracking.

## 7. Don't Break the Cache — arXiv 2601.06007 — Lumer et al. (CRUCIAL)

(a) **Empirical study, not a new algorithm.** Compares 4 caching strategies for long-horizon agentic workloads, controlled by **UUID injection at cache-block boundaries**:
- *No cache*: UUID prepended to system prompt → invalidates all caching.
- *Full-context*: provider's default (cache the entire prefix that matches).
- *System-prompt-only*: UUID appended after the system prompt — provider only caches the static section.
- *Exclude-tool-results*: UUIDs placed after the system prompt **and after every tool result** — provider caches static instructions and tool-result-bracketed segments but skips the volatile tool-result blocks.
(b) **Explicitly does not model tool-result dependencies.** The paper concedes that "tool results inform subsequent tool calls within the same session" but treats them as *cache-write overhead* to be excluded. There is no notion of marking some tool results as durable (needed for downstream calls) and others as ephemeral.
(c) **Online inference**, real provider APIs. No offline batch path.
(d) No GitHub link in the paper. Python via LangChain DeepAgents.
(e) DeepResearch Bench (100 PhD-level tasks, 22 fields); 40 sessions × 4 cache conditions × 4 models = 500+ runs. Drivers: GPT-5.2, Claude Sonnet 4.5, Gemini 2.5 Pro, GPT-4o.
(f) **Cost reduction 41–80 %; TTFT improvement 6–31 %.** Counter-intuitive: naive full-context caching can *increase* latency below provider minimum-thresholds, because cache-write overhead exceeds reuse benefit. "System-prompt-only" is most robust.
(g) Does NOT address: **task success / quality** (only $$$ and latency); security side-channels; *what* to put inside the cached region (i.e., they do not propose a compaction scheme — just a caching boundary). Does NOT consider eviction TTL coordination, does NOT address multi-agent / sub-agent fan-out, does NOT differentiate dependency-bearing tool results from one-shot ones.

This paper is the strongest empirical evidence that **any compaction scheme that rewrites the prefix kills 41–80 % of the cost savings**, which is the central tension with PAACE / SUPO / FoldAgent.

## 8. Pitfalls of KV Cache Compression — arXiv 2510.00231 — Chen, Geh, Grover, Van den Broeck, Israel

(a) **Diagnostic study** of off-the-shelf KV-cache eviction (H2O, SnapKV, StreamingLLM, etc.) under **multi-instruction prompts** (a system prompt with many sub-instructions, then a user query). Finds that eviction policies are biased: certain instructions — typically those middle of the system prompt or far from the query — get their KV evicted disproportionately and are then **effectively ignored** by the LLM. Case study: system-prompt leakage attacks succeed more often under compression because the "do-not-leak" instruction is the one evicted. Proposes a simple fix: eviction policies should be *position-and-instruction-aware*, with per-instruction quotas and recency override.
(b) No agent / tool-call notion — purely instruction-following.
(c) Inference-time; compression is online.
(d) No code link advertised.
(e) Multi-instruction system-prompt benchmarks; their own constructed leakage probe; driver = Llama-3-8B-Instruct, Mistral-7B-Instruct.
(f) Headline: certain instructions degrade *2–10×* faster than others under compression; proposed eviction fix recovers near-full instruction following at the same compression ratio.
(g) Does NOT address: tool-using agents, prompt-cache vs KV-cache compression interaction, long trajectories, summarization-based methods.

## 9. SWE-Pruner — arXiv 2601.16746

(a) **Self-adaptive, task-aware pruning for coding agents.** Given the current subtask, the *agent itself* writes an explicit "skim goal" (e.g. "focus on error handling"). A lightweight 0.6B-parameter neural *skimmer* uses that goal to score lines in the surrounding code/context and drop the ones below threshold. Inspired by "selective skimming" by human programmers.
(b) Operates on **code context** (files, hunks), not on tool-call traces — tool-result dependency is sidestepped because the pruner runs over file content, not over the agent's own transcript.
(c) Online, per agent step.
(d) `github.com/Ayanami1314/swe-pruner`; HF model `ayanami-kitasan/code-pruner`. Python.
(e) SWE-bench Verified + 3 other coding/long-context benchmarks; LongCodeQA for single-turn. Multiple driver models.
(f) **23–54 % token reduction on SWE-bench Verified with no success-rate drop** (sometimes a small gain). **Up to 14.84× compression on LongCodeQA.**
(g) Does NOT address: non-code domains; full trajectory compaction (focuses on file/context pruning, not the agent's own ReAct log); cache-aware operation; tool-result preservation across turns.

## 10. Agent-Omit — arXiv 2602.04284

(a) **RL-trained selective omission of thoughts and observations.** The agent learns *which* of its own past thoughts and *which* past observations are safe to drop. Two-stage: cold-start SFT on small synthetic single-turn and multi-turn omission scenarios, then "omit-aware agentic RL" with dual-sampling (positive/negative omission rollouts) and a tailored omission reward (success + tokens-saved). Baselines compared: Thinking-Retention, DEPO, Tool-Light, Observation-Mask, MEM-Agent, ReSum.
(b) **Closest of all listed papers to explicit tool-result handling**: observations *are* tool results in their framing, and the policy decides per turn whether each is keepable. Still no *structural* dependency tracking — it's a learned heuristic.
(c) Online (omission is an action), trained offline.
(d) Code availability not advertised. Likely Python.
(e) Tool-use benchmarks (e.g. BFCL, AppWorld-style) — exact n/driver not in abstract.
(f) Numbers not in abstract; paper claims improvement over MEM-Agent / ReSum on both task success and token cost.
(g) Does NOT address: cache preservation (omission still rewrites prefix); zero-shot use without RL training; cross-session memory.

## 11. Memex(RL) — arXiv 2603.04257

(a) **Indexed experience memory with RL'd read/write policy.** Working context holds only (i) compact structured summaries and (ii) **stable indices** pointing into an external store that contains the *exact* original tool outputs / reasoning. The agent learns, via `MemexRL`, when to dereference an index and pull the full evidence back into context. Crucially: *no information is destroyed* — only paged out. This is closer to MemGPT-style virtual memory but with the OS policy itself learned.
(b) Best preservation guarantee of all listed papers: tool results are kept *verbatim* off-context, retrievable by index. Dependency tracking is implicit — if a future tool call needs the precise output of a past tool call, the agent dereferences. Still no symbolic dependency graph, but lossless storage means dependencies are *recoverable*.
(c) Online inference; RL training offline.
(d) Code availability not advertised in abstract.
(e) Long-horizon agent tasks; exact set/n/driver not in abstract.
(f) Headline: "substantially less lossy than summary-only approaches" while keeping active context small; quantitative numbers in the paper body.
(g) Does NOT address: cache preservation (rewrites prefix when indices change); zero-shot use; latency cost of dereferencing.

## 12. Beyond the Context Window — arXiv 2603.04814

(a) **Cost/quality empirical comparison**, not a new system. Compares a Mem0-based fact-memory architecture against simply passing the full long history to a long-context LLM (GPT-5-mini), across three memory-centric benchmarks. Builds a closed-form cost model: long-context = O(turns × context_size) per-turn even with caching; memory system = one-time write cost + small per-query retrieval.
(b) Dialogue-centric; not tool-call-focused.
(c) Offline benchmarking of two online inference strategies.
(d) No code advertised. Python implied (uses Mem0 SDK).
(e) LongMemEval, LoCoMo, PersonaMemv2; driver GPT-5-mini.
(f) Long-context GPT-5-mini **beats** the memory system on LongMemEval and LoCoMo for recall; memory system competitive on PersonaMemv2. Cost curves cross at ~N turns (paper specifies).
(g) Does NOT address: tool-using agents; medium-trajectory regime explicitly; cache-aware operation; quality–cache trade-off.

## 13. TACO (Self-Evolving Terminal Agent) — arXiv 2604.19572

(a) **Training-free, self-evolving observation compression for terminal agents.** Across trajectories, TACO mines patterns of redundant observation content (e.g. recurring shell prompts, log noise) and auto-induces compression *rules* (likely regex/template-level) that future runs apply at observation-ingest time. Plug-and-play onto existing terminal agents; no model retraining.
(b) Targets exactly the tool-result preservation problem in the terminal domain, but at a syntactic / pattern level — not via dependency tracking. Rules are learned, not specified.
(c) Online compaction at observation time; rules refined offline between runs (self-evolution loop).
(d) Code link not surfaced in abstract.
(e) TerminalBench (TB1.0, TB2.0), SWE-bench Lite, CompileBench, DevEval, CRUST-Bench.
(f) Quadratic token-growth claim is the framing; paper reports concrete token / success numbers per benchmark.
(g) Does NOT address: non-terminal tool ecosystems; preserving structured tool results (JSON, etc.) where pattern-based rules underperform; cache preservation (rules rewrite the observation block in the prefix).

---

## Fresh keyword scans (Nov 2025 – May 2026)

**"trajectory compaction agent"**
- *AgentDiet / Reducing Cost of LLM Agents with Trajectory Reduction* — arXiv 2509.23586. Automatic removal of "waste" turns; 39.9–59.7 % input-token reduction, 21–36 % cost reduction at parity quality.
- *DeepAgent* — arXiv 2510.21618. Autonomous *memory folding* across episodic / working / tool memory tiers; close cousin to Context-Folding but with tool-call typed memory tiers.

**"agent context engineering"**
- *Agentic Context Engineering* — arXiv 2510.04618. Self-improving context curation via failure-driven natural-language guidelines (extension of ACON's idea).
- *Structured Context Engineering for File-Native Agentic Systems* — arXiv 2602.05447. Schema-accuracy / format-effectiveness framework for coding agents that read/write many files; complements SWE-Pruner.

**"tool call retention"**
- *MCP Tool Descriptions Are Smelly* — arXiv 2602.14878. Augmenting tool descriptions improves accuracy but eats context; argues for adaptive description loading.
- *Natural Language Tools* — arXiv 2510.14453. Replaces JSON tool calls with NL; 31.4 % avg token reduction; orthogonal compaction lever.

**"cache-friendly agent memory"**
- *Agent Memory Below the Prompt* — arXiv 2603.04428. Persist per-agent Q4-quantized KV cache to disk; **4–35× TTFT speed-up at 1–4K, up to 136× at 32K** on Apple-silicon edge. Closest paper to truly cache-aware agent memory.
- *KVFlow* — arXiv 2507.07400. Prefix-cache scheduling for multi-agent workflows.

**"lazy summarization"**
- *ReSum* — arXiv 2509.13313. Periodic external-tool summarization for web agents + ReSum-GRPO for RL credit assignment across summary boundaries. Already adjacent to SUPO/FoldAgent.
- *Beyond Static Summarization* — arXiv 2601.04463. Proactive (predictive) memory extraction triggered before context is full — closest "lazy/triggered" framing.

---

## Synthesis: what is still missing for medium-trajectory tool-using assistants (5–15 turns)

**Well-covered.** The literature has converged on three families:

1. *Learned-policy compaction* (SUPO, FoldAgent, Agent-Omit, Memex-RL). Strong empirical wins (10–14 % accuracy or 10× active context) — but every member requires RL training of the driver model. They're not deployable as drop-in middleware.
2. *Distillation-based compactor models* (PAACE, SWE-Pruner). The compactor is a small dedicated model; the driver stays untouched. Closer to deployable, but still requires offline teacher data generation per domain.
3. *Memory-OS abstractions* (MemOS, MemoryOS, Memex-RL). Multi-tier storage with promotion/demotion; designed for *long* horizons (50–600+ turns of dialogue), evaluated mostly on LongMemEval / LoCoMo. Overkill for the 5–15-turn regime and dialogue-centric.

Plus one empirical-pitfalls family: *Don't Break the Cache* and *Pitfalls of KV Cache Compression* — these tell us *what not to do* but propose no compaction algorithm.

**Genuinely unaddressed for the 5–15-turn tool-use regime:**

- **Cache-preservation as a first-class constraint.** Only *Don't Break the Cache* and *KVFlow* take prompt-cache reuse seriously; every compaction paper (PAACE/SUPO/FoldAgent/Memex-RL/TACO) rewrites the prefix, sacrificing the 41–80 % cost savings that *Don't Break the Cache* documents. There is no published algorithm that **compresses the trail while keeping the static prefix bit-identical** for cache reuse.
- **True tool-call dependency tracking.** No paper builds an explicit DAG of "tool_call_N produced result_R; tool_call_M depends on R". Agent-Omit and Memex-RL come closest (learned omission / lossless indexing), but neither models dependencies symbolically. For 5–15-turn traces — where a single mis-dropped result kills the run — this is the central correctness risk.
- **Online / lazy compaction with no model training.** All learned methods require either RL training (SUPO, Agent-Omit, FoldAgent, Memex-RL) or distillation corpora (PAACE). Training-free entries (TACO, SWE-Pruner) target specific domains (terminal, code). **A framework-agnostic, training-free, lazy (triggered-on-budget) compactor for general tool-use traces is missing.**
- **Framework-agnostic deployment.** Every system listed is married to a specific harness — FoldAgent to its RL stack, MemOS/MemoryOS to their own runtimes, SWE-Pruner to a code agent loop. None presents a minimal proxy / middleware design that drops in front of LangChain / OpenAI-tools / Anthropic-tools without code changes.
- **Zero-config operation.** All approaches require tuning thresholds, training data, or domain rules. The 5–15-turn assistant case wants something an engineer enables with a single env var.

**Cleanest novelty gap.**
> An **online, training-free, cache-preserving compactor** that operates **only on the tail (post-cache-boundary)** of the trajectory, uses an **explicit tool-call dependency graph** (parse tool_call_id → tool_result references in subsequent tool inputs) to decide what to keep verbatim vs. drop vs. summarize, and ships as **framework-agnostic middleware** with zero-config defaults.

This sits in the empty quadrant: orthogonal to RL-trained compactors (SUPO, FoldAgent), more deployable than distillation systems (PAACE, SWE-Pruner), respects the empirical lesson from *Don't Break the Cache* that the static prefix is sacred, and addresses the dependency-tracking blind spot that learned-omission systems (Agent-Omit) handle only heuristically.

---

## Sources

- [PAACE — arXiv 2512.16970](https://arxiv.org/abs/2512.16970)
- [SUPO — arXiv 2510.06727](https://arxiv.org/abs/2510.06727)
- [MemOS — arXiv 2505.22101](https://arxiv.org/abs/2505.22101)
- [Memory OS of AI Agent — arXiv 2506.06326](https://arxiv.org/abs/2506.06326)
- [Context-Folding / FoldAgent — arXiv 2510.11967](https://arxiv.org/abs/2510.11967)
- [MOOM — arXiv 2509.11860](https://arxiv.org/abs/2509.11860)
- [Don't Break the Cache — arXiv 2601.06007](https://arxiv.org/abs/2601.06007)
- [Pitfalls of KV Cache Compression — arXiv 2510.00231](https://arxiv.org/abs/2510.00231)
- [SWE-Pruner — arXiv 2601.16746](https://arxiv.org/abs/2601.16746)
- [Agent-Omit — arXiv 2602.04284](https://arxiv.org/abs/2602.04284)
- [Memex(RL) — arXiv 2603.04257](https://arxiv.org/abs/2603.04257)
- [Beyond the Context Window — arXiv 2603.04814](https://arxiv.org/abs/2603.04814)
- [TACO / Self-Evolving Terminal Agents — arXiv 2604.19572](https://arxiv.org/abs/2604.19572)
- [AgentDiet / Trajectory Reduction — arXiv 2509.23586](https://arxiv.org/abs/2509.23586)
- [DeepAgent — arXiv 2510.21618](https://arxiv.org/abs/2510.21618)
- [Agentic Context Engineering — arXiv 2510.04618](https://arxiv.org/abs/2510.04618)
- [Structured Context Engineering — arXiv 2602.05447](https://arxiv.org/abs/2602.05447)
- [MCP Tool Descriptions Are Smelly — arXiv 2602.14878](https://arxiv.org/abs/2602.14878)
- [Natural Language Tools — arXiv 2510.14453](https://arxiv.org/abs/2510.14453)
- [Agent Memory Below the Prompt — arXiv 2603.04428](https://arxiv.org/abs/2603.04428)
- [KVFlow — arXiv 2507.07400](https://arxiv.org/abs/2507.07400)
- [ReSum — arXiv 2509.13313](https://arxiv.org/abs/2509.13313)
- [Beyond Static Summarization — arXiv 2601.04463](https://arxiv.org/abs/2601.04463)
