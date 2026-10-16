# 07 — План эмпирической валидации

**Связано:** [overview](00_overview.md), [08_paper_outline](08_paper_outline.md), [09_roadmap](09_roadmap.md), прошлая сессия `workdir/compaction_policies__20260508_0231/`.

## Цель

Эмпирически показать, что библиотека достигает success-criteria, **с воспроизводимым статистическим протоколом**. Использовать harness и judge'и прошлой сессии для прямой сравнимости.

## Бенчмарки и наборы данных

Все бенчмарки — те же, что в прошлой сессии (datasets реплицируются), плюс расширенный cross-vendor probe.

| Бенчмарк | n | Сиды | Стратегии | Драйвер | Доля бюджета |
|---|---|---|---|---|---|
| **τ-bench retail** | 50/strategy | 3 (42, 43, 44) | full_context, naive_truncation, rolling_summary, task_aware, **auto-gate-rolling** (наш), **auto-gate-task_aware** (наш) | Claude Haiku 4.5 | $12 |
| **τ-bench airline** | 30/strategy | 2 (42, 43) | те же 6 | Claude Haiku 4.5 | $6 |
| **LongMemEval-S main** | 100 | 1 (42) | те же 6 | Claude Haiku 4.5 | $5 |
| **LongMemEval-S replication** | 50 | 1 (43) | full_context, task_aware, auto-gate-task_aware | Claude Haiku 4.5 | $3 |
| **LoCoMo** | 25 | 1 (42) | full_context, rolling_summary, task_aware, auto-gate-task_aware | Claude Haiku 4.5 | $1 |
| **Cross-vendor probe** | 15 | 1 (42) | full_context, rolling_summary, task_aware, **auto-gate-rolling** | Haiku 4.5, Sonnet 4.6, Gemini-3-Flash, GPT-4.1-mini | $25 |
| Compactor side-cost | — | — | — | — | $6 |
| Iteration / smoke | — | — | — | — | $2 |
| **Итого** | | | | | **$60** |

**Стратегии в нотации:**
- `auto-gate-rolling` = ELR gate (M2) + rolling_summary backend (M4). Главный candidate для headline.
- `auto-gate-task_aware` = ELR gate + task_aware backend. Cleaner показатель для chat-band (LongMemEval, LoCoMo).
- `auto-gate-naive` = ELR + naive_truncation. Ablation для проверки, что выигрыш от gate'а, а не от backend'а.

## Метрики (5 осей)

### A1 — Headline accuracy/cost Pareto

Pass@1 (или accuracy для LongMemEval) на vs `mean_total_usd_per_query` и vs `mean_input_tokens`. Pareto-plot Figure 1 в отчёте.

**Ожидаемое:**
- `auto-gate-rolling` доминирует `rolling_summary` (наш variant дешевле или паритет по точности при меньшем токен-cost'е).
- `auto-gate-task_aware` поднимает τ-bench retail из `task_aware` = 0.00 → ≥ 0.55.
- `auto-gate-naive` показывает скромные выигрыши (gate-эффект без хорошего backend'а).

### A2 — Cache effectiveness

`cache_read_input_tokens / (cache_read_input_tokens + cache_creation_input_tokens + uncached_input_tokens)` на Anthropic Haiku 4.5 и Sonnet 4.6, по каждой стратегии.

**Ожидаемое:**
- `passthrough` (append-only baseline) — верхняя граница ~75 %.
- `auto-gate-rolling` (наш) — ≥ 60 %.
- `task_aware` (cache-hostile) — близко к 0 %.

### A3 — Entity-extractor recall + Liveness-gate F1

200-trace gold set из прошлой сессии (нужно вручную аннотировать entity_refs на 200 segment'ах) + 50 новых τ-bench airline трасс. Per-tier:

| Tier | Recall target |
|---|---|
| tier-1 (regex) | ≥ 60 % |
| tier-1+2 (regex + JSON-path) | ≥ 85 % retail, ≥ 78 % airline |
| tier-1+2+3 (LLM rescue) | ≥ 92 % retail, ≥ 94 % airline |

Liveness-gate F1 на 50 manually-labeled τ-bench airline trajectories: ≥ 0.85.

### A4 — Cross-vendor стабильность

n=15 / vendor, 4 vendors: Haiku 4.5, Sonnet 4.6, Gemini-3-Flash, GPT-4.1-mini. Spearman ρ между ранжированиями 4 стратегий (full / rolling / task_aware / auto-gate-rolling) по pass@1.

**Ожидаемое:** ρ ≥ 0.3 (vs ρ = −0.5 в прошлой сессии). Гипотеза: ELR gate структурно менее чувствителен к driver-у (gate — детерминирован, не LLM).

### A5 — Setup-time UX

3 внешних разработчика устанавливают и используют библиотеку в чистом Next.js. Замеряем медиану времени от `git clone` до первой работающей session с компакцией.

**Цель:** медиана ≤ 5 минут.

## Статистический протокол

Mirror прошлой сессии:

- **Paired permutation test** (10 000 permutations) на каждую стратегию vs `full_context`.
- **95 % bootstrap CI** (10 000 resamples).
- **Pooled two-seed test** для τ-bench retail (seed 42 n=50 + seed 43 n=50 = 100 pooled). Это нужно потому что одиночный seed может дать p > 0.05, а pooled — гораздо сильнее (как в прошлой сессии: seed 42 p=0.108, pooled p=0.0042).
- **Pre-register** null + minimum detectable effect перед запуском sweep'а. Документ — `eval/preregistration_2026-XX-XX.md`, коммит до запуска.

### Pre-registration (template)

```markdown
# Pre-registration — Main empirical sweep, $PROJECT_NAME

Date: $DATE
Author: $AUTHOR
Project: Cache-preserving, tool-aware compaction framework

## Primary hypothesis

H1: `auto-gate-rolling` Δ pass@1 vs `full_context` on τ-bench retail (pooled n=100, seeds 42+43) ≥ 0.10, AND `mean_input_tokens` reduction ≥ 30 % vs `full_context`.

## Statistical protocol

- Test: pooled paired permutation, two-sided
- α = 0.05
- 10 000 permutations
- 95 % bootstrap CI (10 000 resamples)

## Decision rule

If pooled p < 0.05 AND Δ ≥ 0.10 AND token-reduction ≥ 30 %: H1 supported, report as headline.
Else: report as «directional, replication-supported» if seed-43 alone passes; or as null result if both fail.

## Power analysis

Pre-run on synthetic with effect Δ = 0.10, σ = 0.25, n = 100 paired:
expected p < 0.05 with probability ≈ 0.84 (calculated via simulation).

## Stop criterion

100 trajectories total (50 per seed). No early stopping.

## What we will NOT do

- No cherry-picking: all six strategies will be reported.
- No post-hoc subgroup analyses for headline.
- No re-running with different seeds if the first run fails.
```

## Бюджет — детальная разбивка ($60 общий)

Базируется на прошлой сессии cost-distribution + extrapolation на расширенные n.

| Категория | Подсчёт | $ |
|---|---|---|
| τ-bench retail (n=50 × 6 стратегий × 3 сида = 900 trajectories) | Haiku 4.5 @ $1/M in, $5/M out, среднее 50k токенов/trajectory | $12 |
| τ-bench airline (n=30 × 6 стратегий × 2 сида = 360 trajectories) | Аналогично, меньше | $6 |
| LongMemEval-S main (n=100 × 6 = 600 calls) | Большой input (~106k tokens/call) | $5 |
| LongMemEval-S replication (n=50 × 3 = 150 calls) | Аналогично | $3 |
| LoCoMo (n=25 × 4 = 100 calls) | Малый | $1 |
| Cross-vendor (n=15 × 4 stratей × 4 vendors = 240 calls) | Mixed prices | $25 |
| Compactor side-cost (rolling_summary + task_aware LLM-вызовы) | ~10 % от общего | $6 |
| Smoke + iteration | Small probes | $2 |
| **Итого** | | **$60** |

**Sensitivity check (Week 4 Day 1):** запустить 5-trace smoke на каждом из 4 vendor'ов; extrapolate. Если cross-vendor проекция > $25 — урезать до 3 vendors или n=10/vendor.

## Harness reuse

```bash
# Запускаем eval из проекта Holosophus, используя прошлой сессии judge
cd /Users/Aleksei/Projects/ai_scientists/Holosophus
uv run python -m eval.bench_runner --benchmark taubench-retail --strategy auto-gate-rolling --n 50 --seed 42
```

`eval/bench_runner.ts` — TS-orchestrator, который:
1. Загружает trajectory dataset из `workdir/compaction_policies__20260508_0231/mle/data/`.
2. Прогоняет каждую trajectory через Vercel AI SDK + наше middleware.
3. Логирует cost / cache-hits / outcome в `eval/results/{benchmark}_{strategy}_{seed}.jsonl`.
4. Вызывает Python judge через subprocess (`eval/judge_proxy.py`) для оценки accuracy.

## Результаты — структура

```
eval/results/
├── preregistration_2026-XX-XX.md     # pre-reg
├── raw/
│   ├── taubench_retail_full_seed42.jsonl
│   ├── taubench_retail_full_seed43.jsonl
│   ├── ...
│   └── locomo_*.jsonl
├── summaries/
│   ├── taubench_retail_summary.json   # per-strategy: mean, ci95, p_vs_full
│   ├── ...
│   └── pooled_permutation_test.json   # headline pooled test
├── figures/
│   ├── fig1_pareto_usd.pdf
│   ├── fig2_per_type_bars.pdf
│   ├── fig3_taubench.pdf
│   ├── fig4_xvendor.pdf
│   └── fig5_cache_read_fraction.pdf
└── cost_log.jsonl                     # 100% LLM calls
```

## Acceptance — гарантии до запуска main sweep'а

- [ ] Smoke на n=5 для каждого бенчмарка прошёл (Week 1–3).
- [ ] τ-bench retail с auto-gate-rolling даёт pass@1 ≥ 0.40 на smoke (Week 3 контрольная точка).
- [ ] Pre-registration коммит'нут до Week 4 Day 1.
- [ ] Cost projection из smoke ≤ $60.

## Acceptance — итоги main sweep'а (Week 4 end)

- [ ] Все 6 стратегий x 4 бенчмарка прогнаны.
- [ ] `pooled_permutation_test.json` есть для headline τ-bench retail.
- [ ] Все 5 figures (Pareto, per-type, τ-bench, cross-vendor, cache-fraction) сгенерированы.
- [ ] Cost-log закрыт; реальный расход документирован.

## Риски (eval-специфические)

- **R1 — τ-bench retail все ещё малое n.** n=50 × 3 сида = 150 total — лучше прошлых 10, но CI всё равно широкий. Mitigation: явно reporting CI + soften claim verbs если p > 0.05.
- **R2 — Cross-vendor route-cost variance.** Sonnet 4.6 в ~3× дороже Haiku 4.5; GPT-4.1-mini может уйти за $5 / M токенов. Sensitivity check на Week 4 Day 1.
- **R3 — Judge model availability.** LongMemEval использует gpt-4o-2024-08-06; если OpenRouter sunset'нул этот dated route в течение sweep'а — переключаемся на `openai/gpt-4o` + повторный meta-eval. (Применили в прошлой сессии.)
- **R4 — Reproducibility.** Все judge / compactor вызовы кешируются по `(prompt_hash, model, seed)`. Если route меняется — заранее объявляем и фиксируем версию.
- **R5 — Stat-protocol error: pre-registration violation.** Если в Week 4 будет соблазн «дозапустить» из-за нестабильности — НЕ делать. Если первая попытка fails — report как null.
