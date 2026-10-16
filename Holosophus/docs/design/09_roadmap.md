# 09 — 6-недельный roadmap

**Связано:** [overview](00_overview.md), все M0–M4 specs, [07_eval_plan](07_eval_plan.md), [08_paper_outline](08_paper_outline.md).

## Контрольные точки

| Точка | Дата (рабочих недель от старта) | Критерий passing |
|---|---|---|
| **C1** — конец Week 1 | +5 days | Smoke прошёл full-context paritet с прошлой сессии (LongMemEval-S n=10) |
| **C2** — конец Week 2 | +10 days | Extractor recall на 200-trace gold ≥ 85 % (tier-1+2 only) |
| **C3** — конец Week 3 | +15 days | Auto-pipeline проходит smoke n=5 на τ-bench retail с pass@1 > 0.40 |
| **C4** — конец Week 4 | +20 days | Main sweep завершён; pooled permutation test reported |
| **C5** — конец Week 5 | +25 days | Paper draft 4–6 стр., 5 figures, full results |
| **C6** — конец Week 6 | +30 days | npm preview-release, example app, GitHub README, financial closeout |

## Week 1 — M0 + Vercel adapter + harness scaffolding

**Deliverable:** `@holo/typed-segment` + `@holo/vercel-ai` (skeleton). Round-trip 100 % на 5 000 трасс. TS eval-runner запускает τ-bench retail (n=5 smoke) + LongMemEval-S (n=10 smoke) через Vercel AI SDK.

**Tasks:**

- **Day 1:** scaffold pnpm моно-репо, базовые `package.json` / `tsconfig.json` / `vitest.config.ts` для 6 пакетов. Первый коммит.
- **Day 2:** port `segments.py` → `@holo/typed-segment/src/types.ts`. Tokenize helper (tiktoken / wasm-tiktoken).
- **Day 3:** Vercel adapter (`fromCoreMessage` / `toCoreMessage`). Property tests с fast-check, target 5 000 трасс.
- **Day 4:** `@holo/vercel-ai/src/middleware.ts` skeleton — `mode: 'off'` (passthrough only) и базовая state-management. Минимальный smoke в `examples/next-retail-chat`.
- **Day 5:** `eval/bench_runner.ts` skeleton. Запускает τ-bench retail n=5 на full_context через Vercel SDK. Запускает LongMemEval-S n=10. Cost-log JSONL. **C1 check.**

**Acceptance:**
- [ ] `pnpm install && pnpm test` зелёный во всех 6 пакетах.
- [ ] 5 000 round-trip property tests проходят.
- [ ] `pnpm run bench:retail-smoke -- --n 5 --strategy full_context` создаёт `eval/results/raw/retail_full_smoke.jsonl`.
- [ ] Numbers на full_context smoke в пределах ±10 % от прошлой сессии.

**Бюджет:** ~$0.50 (smoke вызовы).

## Week 2 — M1 EntityExtractor + M2 LivenessGraph

**Deliverable:** Tier-1+2 extractor, конфигурируемый под tool-схемы. Recall ≥ 85 % на 200-label gold. Liveness API + gate predicate.

**Tasks:**

- **Day 1:** аннотировать 200 τ-bench retail entity_refs gold (вручную или через `mle-solver-remote` агента из Holosophos). Sample из `workdir/compaction_policies__20260508_0231/mle/results/taubench_main.jsonl`. Время: ~2 часа.
- **Day 1–2:** `@holo/entity-extractor` tier-1 (DEFAULT_REGEXES, 10 patterns) + tier-2 (JSON-path). Tests против 200-label gold.
- **Day 3:** `@holo/liveness-graph` — class + score() + isEligibleForCompaction(). Тесты на recency decay, schema-reachability, open-slot binding.
- **Day 4:** аннотировать 50 τ-bench airline trajectory eligibility gold. Test gate F1 на этом наборе.
- **Day 5:** интегрировать M1 + M2 в `@holo/vercel-ai/middleware.ts` (после M0 step). **C2 check.**

**Acceptance:**
- [ ] M1 recall ≥ 85 % retail (tier-1+2 only).
- [ ] M2 gate F1 ≥ 0.85 на 50 airline traces.
- [ ] Latency: M1 tier-1+2 < 5 ms p95, M2 score < 0.5 ms p99.

**Бюджет:** ~$0 (extractor — без LLM в tier-1+2).

## Week 3 — M3 CacheBudgeter + M4 Compactors

**Deliverable:** All 4 compactor backend'ов portированы; `auto` meta-strategy с gate-routing; cache scheduler v1 + server-side clearing detector.

**Tasks:**

- **Day 1–2:** port `compactors.py` → `@holo/compactors/src/{passthrough,naive_truncation,rolling_summary,task_aware}.ts`. Промпты в `prompts/*.yaml`.
- **Day 3:** `auto` стратегия с dependency injection LivenessGraph и trajectory-class heuristic (toolCallRate < 0.2).
- **Day 4:** `@holo/cache-budgeter` — shouldCompact, knapsack 4-of-N, server-side clearing detector. Synthetic simulator.
- **Day 5:** интегрировать в `@holo/vercel-ai/middleware.ts` complete cycle (M0 → M1 → M2 → M3 → M4 → toCoreMessages). End-to-end smoke на τ-bench retail n=5 с auto-gate-rolling. **C3 check.**

**Acceptance:**
- [ ] Tool-pair invariant tests проходят на 100 random traces для всех 5 compactor'ов.
- [ ] `auto-gate-rolling` на τ-bench retail smoke n=5: pass@1 ≥ 0.40 (vs прошлой 0.00 для task_aware).
- [ ] Synthetic cache simulator: 4-of-N даёт ≥ 18 % cost reduction vs single-trailing на 1000 трасс.

**Бюджет:** ~$2 (compactor LLM-вызовы + smoke).

## Week 4 — Main empirical sweep + tier-3 LLM rescue

**Deliverable:** Headline numbers по всем 5 metrics axes. Tier-3 LLM rescue добавлен в M1.

**Tasks:**

- **Day 1 (Monday morning):** dry-run cost estimation на n=5 smoke per benchmark. Если cross-vendor > $25 — урезать до 3 vendors. **Commit pre-registration document** (`eval/preregistration_2026-XX-XX.md`).
- **Day 1 (afternoon):** add tier-3 LLM rescue к M1 (Gemini-3-Flash или Haiku-4.5 модель). Tests на airline gold (target 94 % recall с rescue).
- **Day 2–3:** запускаем main sweep:
  - τ-bench retail (n=50 × 3 seeds × 6 strategies) — ~12 hours wall, $12
  - τ-bench airline (n=30 × 2 seeds × 6 strategies) — ~6 hours, $6
  - LongMemEval-S main + replication — ~10 hours, $8
  - LoCoMo — ~3 hours, $1
  - Cross-vendor probe (n=15 × 4 vendors × 4 strategies) — ~15 hours, $25
- **Day 4:** обработка результатов. Pooled permutation test для τ-bench retail. Bootstrap CIs. Per-type breakdown.
- **Day 5:** генерация всех 5 figures через `make_figures.py` (port from prior session). Sanity check numbers. **C4 check.**

**Acceptance:**
- [ ] τ-bench retail `auto-gate-rolling` pass@1 ≥ 0.55 (pooled n=100, p < 0.05).
- [ ] Cache-read fraction ≥ 60 % на Anthropic Haiku 4.5 для `auto-gate-rolling`.
- [ ] Cross-vendor Spearman ρ ≥ 0.3 для `auto-gate-rolling`.
- [ ] Cost-log закрыт; реальный расход ≤ $55 (margin для Week 5 polish-вызовов).

**Бюджет:** ~$55 (основной burn).

## Week 5 — Paper writing + UX probe

**Deliverable:** Draft 4–6 стр. NLP Course Template отчёта. 3 dev'а прошли setup-walkthrough.

**Tasks:**

- **Day 1:** paper skeleton (`paper/main.tex`) по структуре из [08_paper_outline.md](08_paper_outline.md). Скопировать template style file (`NLP_Course_Template.sty` или fallback `agents4science_2025.sty`). Bibliography skeleton (`paper/refs.bib`).
- **Day 1–2:** написать Introduction, Related Work, Model Description (3 секции с диаграммой и формулами).
- **Day 3:** написать Dataset, Experiments, Results — наполнить числами из Week 4 results.
- **Day 4:** дописать Conclusion + Limitations; bibliography; final pass. Скомпилировать PDF.
- **Day 5:** UX probe — recruit 3 dev'а (внутри команды или friends). Засечь setup time. Записать `eval/results/ux_probe.md`. **C5 check.**

**Acceptance:**
- [ ] `paper/main.tex` компилируется без warnings, PDF в `paper.pdf`.
- [ ] 4–6 страниц основного текста.
- [ ] Все 5 figures embedded.
- [ ] UX probe: median setup time ≤ 5 минут.

**Бюджет:** ~$3 (figure-regeneration + smoke calls).

## Week 6 — Reviewer pass + npm preview + release

**Deliverable:** Финальная версия paper'а; npm packages опубликованы под preview tag; полный README + GitHub repo.

**Tasks:**

- **Day 1–2:** запустить **internal reviewer-agent** (Holosophus reviewer subagent) на paper draft. Получить ≤ 3 must-address issues. Применить.
- **Day 3:** dogfooding — самостоятельно установить библиотеку в чистом Next.js app, пройти 3-step README. Зафиксировать каждый friction. Исправить README, добавить FAQ.
- **Day 4:** scope cleanup; CHANGELOG.md; LICENSE (MIT); CONTRIBUTING.md. `npm publish --tag preview` все 6 пакетов.
- **Day 5:** GitHub repo public; README; example app demo-link; financial closeout. **C6 check.**

**Acceptance:**
- [ ] Internal reviewer-pass с no «must address» (или все закрыты).
- [ ] npm packages: `@holo/typed-segment@0.1.0-preview`, ... все 6 на npm.
- [ ] GitHub repo public с README + example app.
- [ ] Cost-log JSONL опубликован для воспроизводимости.

**Бюджет:** ~$2.

## Параллелизм / делегирование

| Week | Что можно параллелизовать | На кого делегировать |
|---|---|---|
| 1–3 | Engineering модулей — каждый модуль независим | Каждый M_i можно поручить разному engineer / AI-агенту с его design-доком (см. `docs/design/0X_*.md`). |
| 2 Day 1 | 200-label gold annotation | `mle-solver-remote` agent через Holosophos orchestrator (Python-based отметка). |
| 4 Day 1 | Pre-registration document writing | `writer` agent (короткий документ). |
| 4 Day 2–3 | Eval-runs (mostly waiting on LLM responses) | Параллельно — writing paper-skeleton (Week 5 work pulled forward). |
| 5 Day 1–4 | Paper writing | `writer` agent для draft'а; человек — для final pass. |
| 6 Day 1–2 | Reviewer-pass | `reviewer` agent через Holosophos. |

## Контрольные риски и мitigations

- **Slip Week 2 (extractor recall < 85 %):** добавить ещё 5 regex patterns; включить tier-3 LLM rescue раньше (Week 3 вместо Week 4); приоритизировать retail над airline в первой gold annotation.
- **Slip Week 3 (auto-gate-rolling pass@1 < 0.40 на smoke):** debug — какие entity_ids gate пропускает; добавить open-slots auto-detection из user-message; tighten threshold с 0.3 до 0.25.
- **Slip Week 4 (cross-vendor budget overshoot):** урезать до 3 vendors; снизить n с 15 до 10.
- **Slip Week 4 (pooled p > 0.05):** report как «directional, replication-supported»; в paper softening verbs; добавить ablation в Week 5 если бюджет позволяет.
- **Slip Week 5 (paper draft не готов):** растянуть Week 5 на 7 дней; cut Week 6 polish; reviewer-pass — async через issue tracking.

## Расход бюджета (running total)

| Week | Plan | Cumulative |
|---|---|---|
| Week 1 (smoke) | $0.50 | $0.50 |
| Week 2 (no LLM) | $0 | $0.50 |
| Week 3 (smoke compactors) | $2 | $2.50 |
| Week 4 (main sweep) | $55 | $57.50 |
| Week 5 (figures + UX) | $3 | $60.50 |
| Week 6 (polish) | $2 | $62.50 |

**Plan budget $60; buffer $2.50** (allow small overrun). Если выходим за $65 — стоп и переоценка.

## Tracking

Все недели — task в `docs/plans/2026-05-11-medium-traj-compaction.md` + workdir/medium_traj_research_20260511/. Per-week progress reports в Markdown:

```
workdir/medium_traj_research_20260511/progress/
├── week1_report.md
├── week2_report.md
├── ...
└── week6_report.md
```

Каждый report: что сделано, что осталось, контрольная точка C_k passed/missed, бюджет фактический vs план.
