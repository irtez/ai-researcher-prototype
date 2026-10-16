# 08 — Outline финального отчёта (NLP Course Template, 4–6 страниц)

**Связано:** [overview](00_overview.md), [07_eval_plan](07_eval_plan.md), [09_roadmap](09_roadmap.md), [`NLP_Course_Template.pdf`](../../NLP_Course_Template.pdf).

## Формат

- Шаблон: `NLP_Course_Template.pdf` (V. Malykh, May 2020) — single-column course-style.
- Длина: 4–6 страниц основного текста + appendix (без жёсткого лимита).
- Язык: **English** (стандарт для ML reports; шаблон поддерживает и Russian-фразы — см. footer Table 2). Abstract — на английском.
- LaTeX-источник: `paper/main.tex`. PDF: `paper.pdf`.
- В Abstract ссылка на GitHub repo.

## Стиль и тон

- Honest framing. Избежать 4 caveats прошлой сессии (peer review):
  1. Numerical inconsistency между abstract и body (везде согласованные числа).
  2. Overclaim verbs («Pareto-dominates» только когда поддержано p-value; иначе «Pareto-dominates on point estimate, replication-supported»).
  3. Small-n τ-bench framing (явное «pass@1 = X at n=Y; CI [low, high]»).
  4. Missing pooled stat test (всегда reporting pooled когда есть multi-seed).
- Use «we» как стандарт NLP papers. Не использовать markdown emoji.

## Структура

### Title + Author + Date + Abstract

```
TBD title — варианты:
1. "Cache-Preserving Entity-Aware Compaction for Tool-Using LLM Agents"
2. "Beyond Don't Break the Cache: Compaction That Preserves Prefix and Tool-Result Dependencies"
3. "trailmark: A Drop-in Middleware for Cache-Aware Trajectory Compaction in Vercel AI SDK"

Author: TBD (Aleksei Stepin)
Date: TBD (June 2026 end of project)

Abstract (≈100 words):
LLM-агенты с инструментами на 5–15 ходов теряют 41–80 % экономии prompt-cache'а
при использовании существующих context-compaction методов (PAACE, ACON, и пр.),
а task-aware компактор полностью рушится на retail-задачах из τ-bench (pass@1=0.00),
выкидывая entity_id'ы из tool_results, на которые опираются последующие tool calls.
Мы представляем cache-preserving entity-aware framework — TypeScript middleware для
Vercel AI SDK, который сочетает per-entity liveness-gate перед компакцией с
breakpoint-aware планировщиком. На τ-bench retail (n=50 × 3 seeds) наш auto-gate-rolling
поднимает pass@1 с 0.00 (task_aware) до 0.55 при ≥30% сокращении входных токенов
и cache-read fraction ≥60% на Claude Haiku 4.5. Код: github.com/{TBD}.
```

### 1. Introduction (~0.8 стр.)

- Long-horizon LLM agents с инструментами растут линейно в input-cost'е. Replay полной траектории каждый ход — доминирующая стоимость.
- Prompt caching (Anthropic, OpenAI) сжимает это до 0.1×, но любая prefix-mutation инвалидирует кеш. Don't Break the Cache (Lumer 2026) показал 41–80 % cost reduction только от cache-aware дизайна.
- Compaction-литература (PAACE, SUPO, FoldAgent, Agent-Omit, Memex-RL, ACON, Complexity Trap) переписывает префикс на каждом ходу — cache-hostile.
- **Уникальность нашего подхода:**
  1. Cache-preservation как first-class constraint компактора.
  2. Explicit tool-call dependency graph (entity-refs → liveness scoring).
  3. Training-free + framework-agnostic middleware + zero-config defaults.
- Чего нет ни у кого из 13 deep-read'нутых работ (см. Related Work).

**1.1 Team** — кто что делал. Список агентов / contributor'ов.

### 2. Related Work (~0.8 стр.)

**Три параграфа:**

- **Memory-augmented agents** (Mem0, MemGPT, A-Mem, HippoRAG, Zep, Letta, MemoryOS, MemOS, Memoria). Все retrieval-side; решают, что **читать** в активный контекст. Не решают, что **дропнуть из** активного контекста.
- **Trajectory compaction** (ACON, Complexity Trap, PAACE, SUPO, FoldAgent, Agent-Omit, Memex-RL, TACO). Все либо RL-trained (не deployable), либо domain-specific (terminal/code), либо переписывают префикс (cache-hostile).
- **Cache-aware design** (Don't Break the Cache, Pitfalls of KV Cache Compression, KVFlow, vLLM/SGLang RadixAttention). Дают эмпирическую границу, **что НЕ делать**, но не предлагают компакционного алгоритма.

**Pull quote из синтеза:** «We are not aware of prior work that combines write-time live-query conditioning, explicit tool-call dependency graphs, and provider-cache awareness in a single training-free middleware.»

### 3. Model Description (~1.5 стр.)

Подробнее чем Related Work — даём formal API. Это «meat» отчёта.

**3.1 Typed-segment schema (M0)** — TypedSegment / Role / ContentBlock / EntityRef. Дать таблицу полей.

**3.2 Entity extraction (M1)** — три tier'а (regex / JSON-path / LLM rescue). Уравнение recall ≈ 1 − (1−r1)(1−r2)(1−r3).

**3.3 Liveness graph (M2)** — формула:

$$
L(e, t) = 0.4 \cdot L_{\text{schema}}(e) + 0.3 \cdot L_{\text{recency}}(e, t) + 0.3 \cdot L_{\text{slot}}(e)
$$

$$
L_{\text{recency}}(e, t) = \exp(-\alpha \cdot (t - t_{\text{last\_ref}}(e)))
$$

Gate-предикат: сегмент s eligible iff $\forall e \in \text{entity\_refs}(s): L(e, t) < \theta$, по умолчанию $\theta = 0.3$.

**3.4 Cache budgeter (M3)** — 4-of-N knapsack:

$$
\max_{S \subset C, |S|\le 4} \sum_{c \in S} \left( \text{prefix\_tokens}(c) \cdot \text{expected\_reads}(c) \cdot (1 - \mu_{\text{read}}) - \text{prefix\_tokens}(c) \cdot \mu_{\text{write}} \right)
$$

Где $\mu_{\text{write}} = 1.25$, $\mu_{\text{read}} = 0.1$ для Anthropic.

**3.5 Compactor (M4)** — pluggable. 4 backend (passthrough / naive / rolling / task_aware) + auto (gate-routed).

**Figure 1 — Архитектурная диаграмма** (см. [00_overview.md](00_overview.md) ascii-диаграмму, конвертировать в TikZ).

### 4. Dataset (~0.4 стр.)

Таблица:

| Benchmark | Trajectories | Avg turns | Avg input tokens | Domain |
|---|---|---|---|---|
| τ-bench retail | 50 × 3 seeds | 8 | 4000 | retail e-commerce |
| τ-bench airline | 30 × 2 seeds | 10 | 6500 | airline customer service |
| LongMemEval-S | 100 + 50 | n/a (chat) | 106 000 | very long chat memory |
| LoCoMo | 25 | 300+ | 200 000 | ultra-long dialogue |
| Cross-vendor probe | 15 × 4 vendors | mixed | mixed | 4 driver models |

Цитировать prior session как источник датасетов: Stepin et al. 2026 (можно self-citation с правильной форме).

### 5. Experiments (~0.6 стр.)

**5.1 Metrics** — A1–A5 (см. [07_eval_plan.md](07_eval_plan.md) §Metrics).

**5.2 Experiment Setup** — driver (Haiku 4.5 main, 4 vendors cross-probe), compactor model (Haiku 4.5), judge (gpt-4o-2024-08-06 pinned), 3 seeds, n per benchmark, статпротокол (paired permutation 10k + bootstrap CI 10k + pooled two-seed).

**5.3 Baselines** — `full_context`, `naive_truncation`, `rolling_summary`, `task_aware`, plus Anthropic's built-in `clear_tool_uses_20250919`.

### 6. Results (~1.3 стр.)

**Figure 1 — Pareto cost vs accuracy.** X = `mean_total_usd_per_query`, Y = pass@1 / accuracy. Все 6 strategies. Точки для 4 бенчмарков в разных цветах. Headline: `auto-gate-rolling` Pareto-доминирует на τ-bench.

**Figure 2 — Per-type bars (τ-bench retail).** 6 strategies × 6 LongMemEval question types. Чтобы показать, что наш подход не теряет ни на одном sub-type'е.

**Figure 3 — τ-bench retail close-up.** pass@1 bars with 95% CI. Highlights: `task_aware` = 0.00, `auto-gate-task_aware` ≥ 0.55.

**Figure 4 — Cross-vendor stability.** Spearman ρ matrix между 4 vendors. Heatmap. Highlights: `auto-gate-rolling` имеет ρ ≥ 0.3 (более стабилен чем `task_aware`).

**Figure 5 — Cache-read fraction.** Bar chart: passthrough / auto-gate-rolling / task_aware. Highlights: passthrough ≈ 75 %, auto-gate-rolling ≥ 60 %, task_aware ≈ 0 %.

**Table 1 — Main numbers.** Все стратегии × 4 бенчмарка: accuracy, $/query, input_tokens, cache_read_fraction, paired-permutation p vs full_context.

**Интерпретация:** один параграф per figure. Pareto, cache-savings, dependency-aware preservation, cross-vendor robustness. Limitations: small-n τ-bench, single judge-model.

### 7. Conclusion (~0.3 стр.)

Что сделали: TS middleware для Vercel AI SDK, который через entity-aware liveness gate + cache-breakpoint planner делает компакцию совместимой с prompt-caching API. Headline numbers (τ-bench retail 0.00 → 0.55, cache 60%, p < 0.05 pooled). Что дальше (v0.2): Python adapter для LangGraph; TCAD auto-switch; RoD stable; multimodal.

### References

20–30 ссылок. Должно быть:
- arXiv: 2601.06007 (Don't Break the Cache), 2510.00615 (ACON), 2510.00231 (Pitfalls KV Cache), 2406.12045 (τ-bench), 2410.10813 (LongMemEval), 2402.17753 (LoCoMo), 2504.19413 (Mem0), 2310.08560 (MemGPT), 2510.06727 (SUPO), 2510.11967 (FoldAgent), 2512.16970 (PAACE), 2602.04284 (Agent-Omit), 2603.04257 (Memex-RL).
- Anthropic docs (prompt caching), Vercel AI SDK docs.
- Prior session self-citation.

## Figures — кратко

| # | Title | What it shows | File |
|---|---|---|---|
| 1 | Cost-Accuracy Pareto | All 6 strategies × 4 benchmarks | `figures/fig1_pareto_usd.pdf` |
| 2 | Per-type bars | 6 strats × 6 LongMemEval types | `figures/fig2_per_type.pdf` |
| 3 | τ-bench retail close-up | pass@1 with 95% CI | `figures/fig3_taubench.pdf` |
| 4 | Cross-vendor stability | Spearman ρ heatmap, 4×4 | `figures/fig4_xvendor.pdf` |
| 5 | Cache-read fraction | bar chart, 3 strategies | `figures/fig5_cache_fraction.pdf` |
| 6 | Architecture diagram | M0–M4 stack | `figures/fig6_arch.pdf` (TikZ) |

Все figures генерируются через `paper/figures/make_figures.py` (Python script, port из прошлой сессии `make_figures.py`).

## Reuse from прошлой сессии

| Источник | Что брать |
|---|---|
| `workdir/compaction_policies__20260508_0231/paper/main.tex` | LaTeX skeleton — секции, references, fig refs. |
| `workdir/compaction_policies__20260508_0231/make_figures.py` | matplotlib code для pareto / bars / heatmaps. |
| `workdir/compaction_policies__20260508_0231/mle/results/peer_review.md` | Internal review с 4 caveats — применить превентивно. |
| `workdir/compaction_policies__20260508_0231/paper/refs.bib` | Bibliography — переиспользовать большую часть entries. |

**Template note:** `NLP_Course_Template.pdf` в репо корне — *.pdf, исходники .tex могут быть в репо автора (поискать на github.com/madrugado/{template}). Если не найдём — `agents4science_2025.sty` из прошлой сессии — fallback с явным template-deviation footnote.

## Roadmap

См. [09_roadmap.md](09_roadmap.md) §Week 5–6.

- Week 5 Day 1–3: draft skeleton, fill numbers from Week 4 sweep.
- Week 5 Day 4–5: figures, polish.
- Week 6: внутренняя reviewer-pass (через writer-agent или reviewer-agent Holosophus); применить top-3 issues; final compile.

## Acceptance

- [ ] PDF компилируется без warnings из `paper/main.tex`.
- [ ] 4–6 страниц основного текста + appendix.
- [ ] Headline-числа в abstract и в Results совпадают до 3 знака.
- [ ] Все figures имеют 95% CI или distribution markers где применимо.
- [ ] Pooled permutation test reported для headline τ-bench retail.
- [ ] GitHub repo URL в abstract — рабочий.
- [ ] Reviewer-pass дал no «must address» issues, или они закрыты.
