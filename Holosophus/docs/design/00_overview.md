# 00 — Обзор архитектуры

**Связано:** [master-план](../plans/2026-05-11-medium-traj-compaction.md), [ресеч-артефакты](../research/).

## Что мы строим

TypeScript-библиотека (npm-моно-репо) для Vercel AI SDK, которая делает context compaction **cache-preserving** (не ломает Anthropic / OpenAI prompt-cache) и **entity-aware** (не выкидывает `tool_result` entity_id'ы, на которые опираются downstream tool calls). Целевая нагрузка — assistant-агенты с инструментами на 5–15 ходов.

## Зачем

Прошлая сессия `workdir/compaction_policies__20260508_0231/` показала эмпирически:
- task-aware компакция выигрывает на разговорной памяти (LongMemEval 0.73 vs 0.65 full-context при 1.83 % входных токенов, p=0.0042).
- task-aware **рушится в 0.00** на τ-bench retail (n=10), потому что выкидывает entity-ids из tool_results.
- Cross-vendor (Gemini vs Claude Haiku) — Spearman ρ = −0.5: одна и та же политика инвертирует ранжирование.

Cache-сторону покрывает **«Don't Break the Cache»** (arXiv 2601.06007): naive cache-aware prompt design = 41–80 % экономии стоимости. Но никакая компакция-литература (PAACE, SUPO, FoldAgent, Agent-Omit, Memex-RL, ACON, Complexity Trap) не работает совместимо с кешем.

Cleanest empty quadrant в литературе 2025–2026: **online, training-free, cache-preserving компактор с явным tool-call dependency graph, framework-agnostic, zero-config**. Это та полка, куда мы целимся.

## Архитектура: 4 модуля + adapter поверх нормализованной схемы

```
        ┌─────────────────────────────────────────────────────┐
        │  Adapter: Vercel AI SDK (v0.1)                      │  ← framework boundary
        │           LangGraph / Anthropic-Py / OpenAI (v0.2)  │
        ├─────────────────────────────────────────────────────┤
        │  M0: TypedSegment[]   normalized message graph      │  ← контракт
        ├─────────────────────────────────────────────────────┤
        │  M1: EntityExtractor                                │  tier-1 regex + tier-2 JSON-path
        │      tool_call / tool_result → entity_refs          │  + tier-3 LLM rescue
        ├─────────────────────────────────────────────────────┤
        │  M2: LivenessGraph                                  │  per-entity liveness:
        │      schema-reachability + recency + open-task slot │  gate для компакции
        ├─────────────────────────────────────────────────────┤
        │  M3: CacheBudgeter                                  │  планировщик breakpoint'ов
        │      knapsack 4-of-N: reads × 0.1× − writes × 1.25× │  + детектор server-side clear
        ├─────────────────────────────────────────────────────┤
        │  M4: Compactor (pluggable backend)                  │  passthrough / rolling_summary /
        │                                                     │  task_aware / naive / auto / RoD
        └─────────────────────────────────────────────────────┘
```

## Per-модульные спеки

| # | Модуль | Файл | Что делает |
|---|---|---|---|
| 0 | TypedSegment schema | [01_typed_segment.md](01_typed_segment.md) | Нормализованная схема сообщений. Adapter Vercel CoreMessage ↔ TypedSegment. |
| 1 | EntityExtractor | [02_entity_extractor.md](02_entity_extractor.md) | Парсит tool_call/tool_result, извлекает ссылки на entities (IDs, имена). |
| 2 | LivenessGraph | [03_liveness_graph.md](03_liveness_graph.md) | Per-entity «вероятность нужности в будущих ходах». Gate-предикат для компакции. |
| 3 | CacheBudgeter | [04_cache_budgeter.md](04_cache_budgeter.md) | Когда стрелять компакцией и где ставить cache-breakpoint'ы. |
| 4 | Compactor | [05_compactor.md](05_compactor.md) | Pluggable backend: passthrough / rolling_summary / task_aware / auto / RoD. |
| 5 | Vercel AI SDK adapter | [06_vercel_adapter.md](06_vercel_adapter.md) | `compactionMiddleware` для `prepareStep`. Zero-config API. |

## Сопровождающие спеки

| # | Что | Файл |
|---|---|---|
| 7 | План эмпирической валидации | [07_eval_plan.md](07_eval_plan.md) |
| 8 | Шаблон финального отчёта (NLP Course Template, 4–6 стр.) | [08_paper_outline.md](08_paper_outline.md) |
| 9 | 6-недельный роадмап с контрольными точками | [09_roadmap.md](09_roadmap.md) |

## Default wiring (zero-config)

Когда пользователь пишет `compactionMiddleware({ mode: 'auto' })`:

1. Vercel `prepareStep` вызывает middleware.
2. Adapter конвертирует `CoreMessage[]` → `TypedSegment[]`.
3. M1 EntityExtractor (tier 1+2) бежит по новым `tool_result` сегментам.
4. M2 LivenessGraph обновляется новыми entity-refs.
5. M3 CacheBudgeter решает: компактить этот ход?
   - Нет → возвращаем messages как есть, возможно отмечаем новый `cache_breakpoint_after` на последнем сегменте.
   - Да → переходим к шагу 6.
6. ELR gate фильтрует: какие исторические сегменты eligible (все entity_refs имеют liveness < 0.3)?
7. Eligible сегменты идут в M4 compactor `auto`:
   - Если траектория chat-dominant (heuristic: tool_call rate < 0.2) → task_aware backend.
   - Иначе → rolling_summary на eligible-блок.
8. Новые `TypedSegment[]` конвертируются обратно через `toCoreMessage`.
9. Cache breakpoint ставится сразу после нового compacted-блока.

## Acceptance — что считается готовым

Полностью имплементирована библиотека, когда:

- [ ] Все 5 модулей (M0–M4) + Vercel adapter имеют unit-тесты и проходят их в CI.
- [ ] `npm install` + 3-line change в чистом Next.js поднимает работающий пример (`examples/next-retail-chat/`).
- [ ] τ-bench retail pass@1 ≥ 0.55 (vs прошлой сессии task_aware = 0.00).
- [ ] Cache-read fraction ≥ 60 % на Anthropic Haiku-4.5 в multi-turn smoke.
- [ ] Pareto-plot входных токенов × pass@1 показывает, что auto-gate-rolling доминирует rolling_summary при меньшей токен-стоимости.
- [ ] Отчёт скомпилирован (4–6 стр., NLP Course Template-style) и лежит в `paper/main.tex` + `paper.pdf`.

## Ключевые ссылки

- **Master-план**: [`docs/plans/2026-05-11-medium-traj-compaction.md`](../plans/2026-05-11-medium-traj-compaction.md)
- **Ресеч-артефакты**: [`docs/research/`](../research/)
  - `lib_a_cache.md` — Anthropic / OpenAI / vLLM cache механика, инварианты
  - `lib_b_frameworks.md` — Vercel AI SDK / LangGraph / LlamaIndex / Anthropic / OpenAI / Mastra / Letta / AutoGen / CrewAI / Agno: что есть, где провалы
  - `lib_c_latest.md` — PAACE / SUPO / FoldAgent / Agent-Omit / Memex-RL / TACO / Don't Break the Cache: глубокое чтение
  - `proposals.md` — 7 ранжированных proposer-предложений; топ-1 ELR (9.2), топ-2 CLC (8.8)
  - `local_audit.md` — что уже есть в репо: 5 Python-компакторов прошлой сессии, что переиспользуем
- **Anchor papers**: arXiv 2601.06007 («Don't Break the Cache»), 2510.00615 (ACON), 2510.00231 (Pitfalls of KV Cache Compression), 2406.12045 (τ-bench), 2410.10813 (LongMemEval)
- **Прошлая сессия**: [`workdir/compaction_policies__20260508_0231/`](../../workdir/compaction_policies__20260508_0231/) — Python-исходники компакторов, тестовые наборы, judge

## Стиль и инварианты для всех модулей

- **TypeScript-first**: v0.1 на TS, Python-адаптеры — v0.2 (отдельный спринт).
- **Async-first**: все compactor и extractor функции — `async`, потому что внутри возможны LLM-вызовы.
- **Stateless modules, stateful caller**: M1/M2/M3 кешируют состояние через `LivenessGraph` объект, передаваемый caller'ом; модули сами не держат глобальное состояние.
- **Externalize prompts**: все промпты в `prompts/*.yaml` (как Holosophos `prompts/`-pattern), не в коде.
- **No side effects in compactor**: input → deterministic output на тех же данных (LLM-вызовы — единственный источник недетерминизма; их seeds логируются).
- **Cache-breakpoint мутации — единственная мутация на сегменте после append**: M3 ротирует breakpoints; никто иной не пишет в meta существующего сегмента.
- **tool_call / tool_result пары неразрывны**: ни один компактор не имеет права их разорвать.
