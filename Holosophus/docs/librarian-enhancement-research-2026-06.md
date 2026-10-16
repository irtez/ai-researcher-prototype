# Librarian Enhancement — Verified Proposal, Experiments & TCO (June 2026)

Research date: 2026-06-18. Repo state: local `master` fast-forwarded to `origin/master`
(`4d37385`); local `Dockerfile`/`run_research.py` edits preserved; `uv.lock` backup at
`/tmp/academia_uv.lock.local.bak`. Method: 5 parallel research agents + direct verification
via GitHub REST / PyPI / official pricing pages / live API probes / source audit of the
just-merged code. Every external claim is tagged **[verified]** or **[unverified]**.

---

## 0. Why merge mattered for fairness

The 60 merged commits do not touch `librarian.yaml`, but they DO change the librarian's
dependency cone: the **academia MCP gained `web_search` backends (`exa_web_search`,
`tavily_web_search`, `brave_web_search`) and citation tools (`verify_references`,
`verify_references_deep`)**, plus codearkt runtime + docker-compose. So the prior
75% / 61% baselines were on older infra. **Any new experiment must first re-establish the
baseline on merged HEAD** — the recorded numbers are reference points, not the A/B control.

---

## 1. Verified current state (the real constraints)

What the librarian has: ArXiv, S2 (keyword + citation graph), ACL, AlphaXiv, `web_search`,
`arxiv_download`, `document_qa`, hf_datasets, visit_webpage. Weak spots: find-paper-by-
description recall (litsearch ~64%), multi-hop factual QA, brittle substring verifier.

Verified environment facts (audited `.env` + `academia_mcp/tools/web_search.py` + settings):

| Fact | Evidence | Consequence |
|---|---|---|
| `TAVILY_API_KEY` **set** | `.env` | `web_search` already routes to Tavily (deep-research engine) |
| `EXA_API_KEY` **absent** | `.env` | **Exa neural search (best for find-by-description) is dead-wired** — tool exists, no key, not in allowlist, not the default |
| `BRAVE/COHERE/VOYAGE/JINA` absent | `.env` | No managed reranker key available |
| Jina pool balance **−290,405** | `jina_usage` live | Jina reranker/search/embed **unusable** — earlier #1 pick invalid |
| `OPENAI` + `ANTHROPIC` **set** | `.env` | **LLM-as-reranker / LLM-judge / embeddings are free of new vendor** |
| `exa/tavily/brave_web_search`, `verify_references*` **not in `LIBRARIAN_TOOLS`** | `settings.py`, `librarian.yaml` | Capability already in codebase, not reachable by librarian |

**Implication:** several "deep research" upgrades cost ~nothing because the plumbing is already
merged — they just aren't wired to the librarian.

---

## 2. The inter-agent handoff is the structural bottleneck

Topology = star through a stateless manager; the librarian↔proposer interface is a **single
free-text string** (CodeArkt) re-summarized by the manager ("game of telephone"). The proposer
needs a specific `arxiv_id` baseline with an extractable technical limitation (it calls
`extract_bitflip_info(arxiv_id)`), but the librarian emits prose "as much info as possible" and
doesn't know its consumer. **This convergedacross every research stream** as the #1 fix, and the
closest published analog — **Jr. AI Scientist (arXiv 2511.04583, TMLR 2026)** — runs exactly our
contract: "given the baseline paper, analyze its limitations, formulate hypotheses…". Its risk
report names our exact failure modes: improper citation, fabricated results.

---

## 3. Verified intervention catalog — fix × verification × TCO × experiment

Grouped by the weakness each attacks. TCO uses verified numbers: cached vs uncached Sonnet
≈ **$0.30 vs $3.00/MTok (10×)**; input:output ≈ 100:1; multi-agent ≈ **5–15× tokens**;
GPT-Researcher deep run ≈ **$0.40**. Each row is a one-variable A/B vs the re-established baseline.

### Group A — Find-paper-by-description recall (litsearch 64%)

| # | Intervention | Verified? | TCO (build / per-run) | Fair experiment |
|---|---|---|---|---|
| A1 | **Wire Exa neural search** into librarian (+`EXA_API_KEY`) — neural search is purpose-built for "find paper by description" | **[verified]** tool in code; Exa MCP `exa-labs/exa-mcp-server` 4,590★; **20,000 req/mo free**, then $7/1k | ~0 build (key + allowlist) / free tier | A/B `web_search`(Tavily) vs `exa_web_search` on LitSearch recall@20 |
| A2 | **Add OpenAlex source** (keyless, ~250M works) + dedup with arxiv/S2 | **[verified]** live: `api.openalex.org` returned 147,348 hits for "RAG", abstracts+citation graph | low build (REST fn) / **free** | A/B with vs without OpenAlex in the source set; ΔRecall@k |
| A3 | **LLM-as-reranker** (RankGPT-style listwise) over 30–50 candidates → top-8, using in-loop model | **[verified]** zero new vendor (OPENAI/ANTHROPIC keys present); Voyage rerank-2.5 (200M free) or Qwen3-Reranker-4B (free self-host) as alternatives | ~0 vendor / +1 cheap call per search batch (route to Haiku-class) | A/B rerank-on vs off; ΔRecall@8 + Δread-count |
| A4 | **Query decomposition + metadata filters + dense‖keyword parallel** (1 LLM call → {year,venue,subfield,dense_q,keyword_q}) | **[verified]** pattern from Ai2 ScholarQA (`ai2-scholarqa-lib` 281★) | low / +1 call | A/B vs single-query pass; ΔRecall@k |
| A5 | **Persona-diversified queries** (3–5 researcher personas → union of hits) | **[verified]** STORM (28.6k★) | ~0 / +N cheap query calls | ablate persona count vs Recall@k |
| A6 | **Two-stage broad→deep** (cheap wide net → full-text drill on finalists) | **[verified]** FutureHouse Robin Crow/Falcon (560★) | low / shifts cost to fewer deep reads | A/B vs flat pass; ΔRecall + Δdepth-quality |

### Group B — Multi-hop factual QA + citation grounding (infodeepseek; hallucinated cites)

| # | Intervention | Verified? | TCO | Fair experiment |
|---|---|---|---|---|
| B1 | **Grounding gate**: a paper becomes the answer/baseline only after full-text doc-QA returns a *verbatim supporting quote*; else re-query | **[verified]** Agent Lab read-then-commit + OpenScholar self-feedback (OpenScholar-8B beats GPT-4o +5%; GPT-4o hallucinates cites **78–90%** without it) | +1–2 cheap calls / answer | A/B on BrowseComp-Plus evidence-recall + InfoDeepSeek |
| B2 | **Self-verification + trajectory selection** (sample N answers → decompose into sub-questions → score → pick best) | **[verified]** FineVerify (2606.00660), RVR (2602.18425) | N× answer cost (cap N=3) / gate by query class | A/B N=1 vs N=3 on BrowseComp-Plus accuracy vs $ |
| B3 | **Citation existence-check** via the merged `verify_references`-style S2 lookup before final answer | **[verified]** tool merged (`verify_references.py` checks cite keys vs S2) | ~0 / cheap S2 calls | measure cite-validity rate before/after |
| B4 | **RCS limitation-targeted summarization** (summarize each chunk with "what is the technical limitation for [task]?") | **[verified]** PaperQA2 (`paper-qa` 8,722★, `pip install paper-qa`) | +calls per candidate / cheap model | proposer-readiness rubric on output |

### Group C — The librarian→proposer handoff (highest leverage)

| # | Intervention | Verified? | TCO | Fair experiment |
|---|---|---|---|---|
| C1 | **Structured handoff object** (replace 20-sentence prose). Librarian emits `{baseline_arxiv_id, title, what_it_does, technical_limitation, supporting_quote, evidence_location, why_still_open, comparison_to_gap}` + `coverage_confidence`. JIT references, not raw dumps | **[verified]** convergent: Anthropic artifact-handoff, OpenAI `input_filter`, Curie "Challenge" frame, Sakana-v2 forced "Related Work", Jr. AI Scientist baseline drill | ~0 (prompt+schema) / **lowers** proposer tokens | end-to-end: proposer bitflip-extraction success rate with prose vs structured input |
| C2 | **Pass references not values — unify on workspace for CodeArkt too** (port the Claude Code `papers/index.md` + paths pattern; give CodeArkt librarian persistent notes) | **[verified]** R7 in repo + Manus FS-as-memory + Cognition share-traces | low / **lowers** manager re-serialization tokens | manager context-token + dedup-rate before/after |
| C3 | **Novelty/precedent gate**: search for work *after* the baseline that already solves the limitation; if solved → next candidate | **[verified]** FutureHouse Owl/"Precedent", Sakana novelty check | +S2/arxiv calls / cheap | rate of "already-solved" baselines rejected |
| C4 | **Cross-run memory**: store `{baseline_arxiv_id, limitation}` keyed by topic; inject on related runs | **[verified]** AgentRxiv (+11–14% relative when reusing prior agent findings) | low / cache-friendly | dedup of re-searched papers across the 2 librarian calls |

### Group D — TCO / context efficiency (the loop's own cost)

The librarian's cost is **output tokens + cache misses (when history is rewritten) + fan-out
multiplier**, NOT input volume (99% cached). Optimize in that order.

| # | Intervention | Verified? | TCO effect | Experiment |
|---|---|---|---|---|
| D1 | **Compact only at boundaries** (95% window / every N turns), never per-turn; **mask don't remove tools** | **[verified]** Manus (cache 10×), Anthropic context-editing −84% tokens | protects 99% cache | tune boundary freq vs accuracy & $ |
| D2 | **Rolling condensed candidate record** (arxiv_id + findings-so-far) instead of accumulating raw search dumps | **[verified]** Tongyi IterResearch (2510.24701), ReSum +4.5% training-free | lowers tokens as session grows | token/turn curve before/after |
| D3 | **Model routing**: rerank/verify/compaction → cheap (Haiku-class); search-loop → Sonnet-class; orchestrator → frontier only for real planning | **[verified]** Anthropic Opus-lead+Sonnet-workers +90%; OpenAI compaction-model | large $ cut, ~flat quality | cost/quality Pareto per sub-step model |
| D4 | **Budget controller**: per-query-class tool-call cap + graceful "wrap-up & hand off" at maxTurns (no retry storm); fan out only for multi-topic briefs | **[verified]** Anthropic effort rules (token = 80% BrowseComp variance), Cognition read-parallel/write-serial | bounds worst-case $ | cost vs accuracy by budget tier |

### Group E — arXiv-native extraction (cleaner limitation drilling)

| # | Intervention | Verified? | TCO | Experiment |
|---|---|---|---|---|
| E1 | **Ingest LaTeX source** `arxiv.org/e-print/<id>` (clean math/tables/algorithms) instead of PDF text for the baseline | **[verified]** HKUDS AI-Researcher (5,490★) | ~0 / free | extraction precision of limitation quote vs PDF path |
| E2 | **VLM pass over ablation figures/tables** for the baseline | **[verified]** freephdlabor (564★); `describe_image` already in allowlist | +1 vision call | quote-grounding rate on table-based limitations |

---

## 4. Eval: fix the verifier + adopt verified original benchmarks

**First, fix the measurement** (Tier 0, ~free): the substring verifier gives false negatives
(infodeepseek 17%→60% after manual audit). Replace with the **original benchmark metrics**.

Verified runnable benchmarks (repo + eval code confirmed via GitHub REST):

| Benchmark | Repo | Metric | Corpus | Cost/run | Covers |
|---|---|---|---|---|---|
| **LitSearch** | princeton-nlp/LitSearch (109★, MIT) | Recall@k, **no judge** | offline 64k | **agent-only (free metric)** | find-paper recall |
| **BrowseComp-Plus** | texttron/BrowseComp-Plus (299★, MIT) | accuracy (Qwen3-32B) + **evidence-recall** | offline ~100k | 830 self-host judge calls (~$5–10) | multi-hop + grounding |
| **DeepResearchGym** | cxcscmu/deepresearch_benchmarking (28★) | citation P/R + key-point recall (gpt-4.1-mini) | offline frozen | tens of $ | citation grounding |
| PaSa | bytedance/pasa (1,598★, Apache-2.0) | recall@20/50/100 | hybrid | moderate | find-paper recall/precision |
| AstaBench | allenai/asta-bench (110★, Apache-2.0) | per-task + cost-Pareto | hybrid (live Asta) | heavy | all four (drop-in) |

**Recommended fair eval set:** **LitSearch (scale to full 597, recall@k) + BrowseComp-Plus +
DeepResearchGym** — all offline, reproducible, permissive, no expensive proprietary judge,
covering (a) recall, (b) multi-hop, (c) grounding. Keep DeepResearch Bench (RACE+FACT) as an
optional quarterly leaderboard run only (live web + GPT-5.x judge = expensive, non-reproducible).

**[unverified / corrections — caught during verification]:** PaSaMaster Bench (2605.14306) =
**hallucinated**; FINDER/DEFT and deep-research "Dr.Bench" = **no code**; **SAGE (2602.05975)** =
**data-only, no harness** (fails the original-code mandate). AutoResearchBench (2604.25256) and
Total Recall QA (2603.18516) = real & verified.

---

## 5. Fair experimental protocol

1. **Re-establish baseline** on merged HEAD: LitSearch(597, recall@k) + BrowseComp-Plus(subset) +
   the existing 95-q with the **fixed** verifier. This is the A/B control, not the old 75/61.
2. **One variable at a time.** Same model, temperature, max-turns, question set. Report per change:
   ΔRecall@k / Δaccuracy / Δevidence-recall **and** Δcost (output tokens, $, wall-time, turns,
   tool-calls, cache-rate). Mirror the existing `run_proposer_ablation_eval.py` structure.
3. **Power:** rely on LitSearch-597 and BrowseComp-Plus for signal; the custom 15-q sub-bench is
   too small (±1 paper = noise) — keep it only as a smoke test.
4. **Cost budget per full regression:** ≈ low tens of $ (LitSearch free metric + self-host judge),
   so every intervention is cheap to validate before keeping. Keep/discard on measured delta only.
5. **Ablate the bundle:** after individual A/Bs, run the kept set together and report marginal
   contributions (some overlap, e.g. A3 rerank ↔ A6 two-stage).

---

## 6. Prioritized roadmap (impact × ease × TCO)

**Tier 0 — do first, ~free, unblocks everything**
- Fix verifier → LitSearch recall@k programmatic; re-establish baseline on merged HEAD.
- Wire `exa_web_search` + `EXA_API_KEY` (free 20k/mo) into librarian allowlist (A1).
- Add OpenAlex source (A2, keyless).

**Tier 1 — low cost, high impact**
- C1 structured handoff object (the bottleneck fix) + C2 workspace unification.
- A3 LLM-as-reranker (zero new vendor) + A4 query decomposition.
- E1 LaTeX-source ingestion.

**Tier 2 — moderate**
- B1 grounding gate + B3 citation existence-check + C3 novelty gate.
- D1–D2 cache-safe compaction + rolling record; D3 model routing.
- A6 two-stage broad→deep; B4 RCS summarization.

**Tier 3 — heavier, gated by benchmark wins**
- PaperQA2 / Asta Paper Finder integration; B2 trajectory selection; parallel fan-out for
  multi-topic briefs (5–15× tokens — query-class gated); C4 cross-run memory; D4 budget tiers.

---

## 7. Appendix — verified external assets (URLs)

Tools: OpenAlex `api.openalex.org` (keyless) · Voyage rerank-2.5 (200M free) · Qwen3-Reranker-4B
HF (free self-host) · Exa MCP `exa-labs/exa-mcp-server` (4,590★) · paper-search-mcp
`openags/paper-search-mcp` (1,872★) · arxiv-mcp-server `blazickjp/...` (2,870★) · PaperQA2
`Future-House/paper-qa` (8,722★) · Asta Paper Finder `allenai/asta-paper-finder` (244★) ·
ScholarQA `allenai/ai2-scholarqa-lib` (281★) · OpenScholar `AkariAsai/OpenScholar` (1,539★) ·
bm25s (PyPI 0.3.9) · Europe PMC / Crossref / Unpaywall / dblp (keyless).

Systems (librarian-module patterns): Jr. AI Scientist (2511.04583) · PaperQA2 (2409.13740) ·
OpenScholar (2411.14199) · Ai2 ScholarQA/Asta · FutureHouse Robin (2505.13400) · Tongyi
DeepResearch (2510.24701) · STORM (2402.14207) · Sakana AI Scientist v1/v2 (2408.06292 /
2504.08066) · Agent Laboratory (2501.04227) · HKUDS AI-Researcher (2505.18705) · Curie
(2502.16069) · Google AI co-scientist (2502.18864).

Papers (techniques): FineVerify (2606.00660) · RVR (2602.18425) · SIM-RAG (2505.02811) ·
"Direct Corpus Interaction" (2605.05242) · SPAR/RefChain (2507.15245) · GRASP (2605.16598) ·
"Rerank Before You Reason" (2601.14224) · query-reformulation reproducibility (2604.27421).

**Unverified/flagged:** Cohere Rerank 4 exact PAYG $/1k · Parallel.ai MCP (repo 404) · Linkup
MCP (archived) · `asta-tools`/`asta-mcp` (404) · AgentRxiv standalone repo (404, logic in
AgentLaboratory) · "Aiger" (no repo/paper found).
