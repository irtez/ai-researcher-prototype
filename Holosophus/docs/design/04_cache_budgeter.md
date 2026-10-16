# 04 — M3: CacheBudgeter

**Связано:** [overview](00_overview.md), [03_liveness_graph](03_liveness_graph.md), [05_compactor](05_compactor.md), [`docs/research/lib_a_cache.md`](../research/lib_a_cache.md).

## Цель

Планировщик cache-breakpoint'ов и trigger компакции. Два вопроса:

1. **КОГДА компактить** — по watermark / budget breach / forecast'у инвалидации breakpoint'а.
2. **ГДЕ ставить breakpoint'ы** — оптимальное размещение до 4 cache_control markers по правилу 4-of-N knapsack: максимизируем `Σ(expected_reads × 0.1×) − writes × 1.25×`.

## Зачем

Без cache-aware планирования любая компакция теряет 41–80 % экономии от prompt-caching (см. `lib_a_cache.md`, «Don't Break the Cache», arXiv 2601.06007). С Anthropic API: 4 breakpoint'а, 20-block lookback, 1.25× write / 0.1× read. Грубая стратегия (single trailing breakpoint) оставляет 18–60 % экономии на столе.

## Зависимости

- M0 TypedSegment (читает `meta.tokens`, пишет `meta.cache_breakpoint_after`).
- M2 LivenessGraph (опционально — для forecast'а ожидаемых ссылок на entities).

## API контракт

```ts
// packages/cache-budgeter/src/types.ts
import type { TypedSegment } from '@holo/typed-segment';

export interface CacheBudgeterConfig {
  /** Целевая ёмкость контекста (входные токены) перед триггером. По умолчанию 50_000. */
  budget?: number;
  /** Дополнительный safety-margin перед budget breach. По умолчанию 5_000. */
  watermarkMargin?: number;
  /** Минимальный размер блока для cache-breakpoint (model min, в токенах). По умолчанию 1024. */
  minCacheBlock?: number;
  /** Максимум breakpoint'ов (Anthropic = 4). */
  maxBreakpoints?: number;
  /** Forecast: сколько ходов агент ещё проживёт. По умолчанию 8. */
  remainingTurnsForecast?: number;
  /** Cost model. */
  pricing?: {
    writeMultiplier: number;   // Anthropic 5-min = 1.25; 1-hour = 2.0
    readMultiplier: number;    // Anthropic = 0.1
  };
}

export interface CacheBreakpointPlan {
  /** Сегменты, после которых ставится breakpoint. */
  breakpointAfter: string[];   // segment.id list
  /** Расчётная экономия в условных единицах (для логов). */
  estimatedSavings: number;
}

export interface CompactionDecision {
  shouldCompact: boolean;
  reason: 'budget_breach' | 'breakpoint_expiry' | 'forecast_breach' | 'none';
  /** Какой span компактить (segment ids). */
  spanToCompact?: { startId: string; endId: string };
}

export class CacheBudgeter {
  constructor(config?: CacheBudgeterConfig);

  /**
   * Решает, нужно ли стрелять компакцией перед следующим turn'ом.
   */
  shouldCompact(segments: TypedSegment[], currentTurn: number): CompactionDecision;

  /**
   * Рассчитывает оптимальное размещение cache breakpoints.
   * Записывает meta.cache_breakpoint_after на выбранных сегментах.
   */
  placeBreakpoints(segments: TypedSegment[], currentTurn: number): CacheBreakpointPlan;

  /**
   * Детектор Anthropic'овской server-side TTL clearing.
   * Вызывается после каждого API-ответа.
   */
  detectServerSideClearing(prev: ApiCacheStats, curr: ApiCacheStats): boolean;
}

export interface ApiCacheStats {
  cache_read_input_tokens: number;
  cache_creation_input_tokens: number;
  input_tokens: number;
}
```

## Алгоритм 1 — shouldCompact()

```ts
shouldCompact(segments: TypedSegment[], currentTurn: number): CompactionDecision {
  const totalTokens = segments.reduce((sum, s) => sum + (s.meta.tokens ?? 0), 0);
  const lastBreakpointIdx = this.findLastBreakpoint(segments);
  const tokensSinceLastBp = segments
    .slice(lastBreakpointIdx + 1)
    .reduce((sum, s) => sum + (s.meta.tokens ?? 0), 0);

  // 1. Hard budget breach
  if (totalTokens > this.config.budget) {
    return {
      shouldCompact: true,
      reason: 'budget_breach',
      spanToCompact: this.findEligibleSpan(segments),
    };
  }

  // 2. Soft watermark + forecast says we'll breach within K turns
  const watermark = this.config.budget - this.config.watermarkMargin;
  const recentGrowthRate = this.estimateGrowthRate(segments);
  const forecastBreach = totalTokens + recentGrowthRate * this.config.remainingTurnsForecast > this.config.budget;
  if (totalTokens > watermark && forecastBreach) {
    return {
      shouldCompact: true,
      reason: 'forecast_breach',
      spanToCompact: this.findEligibleSpan(segments),
    };
  }

  // 3. Cache breakpoint about to expire (turn-based heuristic for 5-min TTL)
  const turnsSinceLastBp = currentTurn - (this.getBreakpointTurn(segments, lastBreakpointIdx) ?? 0);
  if (turnsSinceLastBp > 18) {  // 20-block lookback - 2 safety margin
    return {
      shouldCompact: true,
      reason: 'breakpoint_expiry',
      spanToCompact: this.findEligibleSpan(segments),
    };
  }

  return { shouldCompact: false, reason: 'none' };
}

private estimateGrowthRate(segments: TypedSegment[]): number {
  // EMA over last 3 turns: per-turn token delta
  const last3 = segments.slice(-3);
  if (last3.length < 2) return 0;
  const tokensPerTurn = last3.map((s) => s.meta.tokens ?? 0);
  return tokensPerTurn.reduce((a, b) => a + b, 0) / last3.length;
}
```

## Алгоритм 2 — placeBreakpoints() (4-of-N knapsack)

```ts
placeBreakpoints(segments: TypedSegment[], currentTurn: number): CacheBreakpointPlan {
  const candidates = this.enumerateCandidates(segments);
  // Each candidate: { afterSegmentId, prefix_tokens, expected_reads }
  // expected_reads = forecast remaining turns × prob(prefix replayed)

  // Solve 4-of-N: pick k breakpoints maximizing Σ(reads × 0.1) − writes × 1.25
  const k = this.config.maxBreakpoints ?? 4;
  const best = this.knapsack(candidates, k);

  // Reset breakpoints
  for (const s of segments) s.meta.cache_breakpoint_after = null;
  for (const c of best.selected) {
    const target = segments.find((s) => s.id === c.afterSegmentId);
    if (target) target.meta.cache_breakpoint_after = 'ephemeral';
  }

  return {
    breakpointAfter: best.selected.map((c) => c.afterSegmentId),
    estimatedSavings: best.totalSavings,
  };
}

private enumerateCandidates(segments: TypedSegment[]) {
  const candidates: Candidate[] = [];
  let prefixTokens = 0;
  for (const seg of segments) {
    prefixTokens += seg.meta.tokens ?? 0;
    if (prefixTokens < (this.config.minCacheBlock ?? 1024)) continue;
    candidates.push({
      afterSegmentId: seg.id,
      prefixTokens,
      expectedReads: this.estimateReads(seg, segments),
    });
  }
  return candidates;
}

private estimateReads(seg: TypedSegment, allSegments: TypedSegment[]): number {
  // Simple model: if forecast says K more turns,
  // and the cached prefix is read on each subsequent call, expected_reads = K
  // (assuming no other invalidation events)
  const remaining = this.config.remainingTurnsForecast ?? 8;
  return remaining;
}

private knapsack(candidates: Candidate[], k: number): { selected: Candidate[]; totalSavings: number } {
  // For N up to 30 and k=4, brute force 4-subset enumeration: O(C(N,4)) ≈ 27_000 ops
  // <<1ms in JavaScript. No need for fancy DP.
  const n = candidates.length;
  let bestSavings = -Infinity;
  let bestSet: Candidate[] = [];

  const indices = Array.from({ length: n }, (_, i) => i);
  for (const combo of combinations(indices, Math.min(k, n))) {
    const set = combo.map((i) => candidates[i]);
    const savings = this.computeSavings(set);
    if (savings > bestSavings) {
      bestSavings = savings;
      bestSet = set;
    }
  }
  return { selected: bestSet, totalSavings: bestSavings };
}

private computeSavings(set: Candidate[]): number {
  const writeMult = this.config.pricing?.writeMultiplier ?? 1.25;
  const readMult = this.config.pricing?.readMultiplier ?? 0.1;
  // Cost: each breakpoint writes its prefix once → prefix_tokens × write_mult
  //       savings: each read saves prefix_tokens × (1 − read_mult)
  let savings = 0;
  for (const c of set) {
    const writeCost = c.prefixTokens * writeMult;
    const readSavings = c.prefixTokens * c.expectedReads * (1 - readMult);
    savings += readSavings - writeCost;
  }
  return savings;
}
```

**Computational cost:** N ≤ 30, k=4 → brute force C(30,4) ≈ 27 000 операций → <1 мс. Никаких сложных DP не нужно.

## Алгоритм 3 — detectServerSideClearing()

Anthropic'овский `clear_tool_uses_20250919` может сработать на стороне сервера и перерезать наш breakpoint. Детектируем по скачку `cache_creation_input_tokens` без локального write-события (которое мы зафиксировали бы через `placeBreakpoints()`):

```ts
detectServerSideClearing(prev: ApiCacheStats, curr: ApiCacheStats): boolean {
  const localWriteExpected = this.lastLocalWriteTokens;  // tracked внутри класса
  const curr_write_delta = curr.cache_creation_input_tokens - prev.cache_creation_input_tokens;
  const unexplained_write = curr_write_delta - localWriteExpected;
  return unexplained_write > 1000;  // beyond noise threshold
}
```

При детекте — middleware (caller) может:
1. Re-инициализировать планирование.
2. Опционально: установить `context_management: { type: 'disabled' }` на следующих запросах, чтобы выключить server-side clearing полностью.

## Acceptance criteria

- [ ] **Synthetic simulator**: 4-of-N scheduler даёт ≥ 18 % cost reduction vs single-trailing-breakpoint на 1000 синтетических трасс с разнообразными growth-rate'ами.
- [ ] **Real τ-bench retail**: cache-read fraction ≥ 60 % на Haiku 4.5 в multi-turn smoke (n=10).
- [ ] **Latency**: `shouldCompact()` < 1 мс p99. `placeBreakpoints()` < 5 мс p99 для N=30 кандидатов.
- [ ] **Idempotency**: при отсутствии новых сегментов повторный `placeBreakpoints()` даёт ту же раскладку.
- [ ] **Server-clearing detector**: precision ≥ 0.9 на 50 синтетических кейсах (mix реальных Anthropic-ответов с clearing-событиями и без).

## Тестовый план

```typescript
// packages/cache-budgeter/tests/knapsack.spec.ts
import { test, expect } from 'vitest';
import { CacheBudgeter } from '../src';

test('knapsack picks high-savings breakpoints', () => {
  const cb = new CacheBudgeter({ remainingTurnsForecast: 8 });
  const segments = generateSegments(20, { meanTokens: 800 });
  const plan = cb.placeBreakpoints(segments, 5);
  expect(plan.breakpointAfter.length).toBeLessThanOrEqual(4);
  expect(plan.estimatedSavings).toBeGreaterThan(0);
});

test('budget breach triggers compaction', () => {
  const cb = new CacheBudgeter({ budget: 10_000 });
  const segments = generateSegments(15, { meanTokens: 900 });  // 13.5k total
  const decision = cb.shouldCompact(segments, 10);
  expect(decision.shouldCompact).toBe(true);
  expect(decision.reason).toBe('budget_breach');
});

test('server-clearing detector flags unexplained writes', () => {
  const cb = new CacheBudgeter();
  const prev: ApiCacheStats = { input_tokens: 5000, cache_read_input_tokens: 4000, cache_creation_input_tokens: 1000 };
  const curr: ApiCacheStats = { input_tokens: 5500, cache_read_input_tokens: 500, cache_creation_input_tokens: 5500 };
  // 4500 unexpected write
  expect(cb.detectServerSideClearing(prev, curr)).toBe(true);
});
```

## Synthetic simulator

Для проверки P6 (Cache-Breakpoint Scheduler) гипотезы — отдельный simulator:

```ts
// packages/cache-budgeter/sim/simulator.ts
function simulateSession(strategy: 'single-trailing' | 'k4-knapsack' | '4-static') {
  // Generate trajectory: 12 turns, mean 800 tokens/turn, 30% tool-result turns
  // For each turn, compute cumulative cost using strategy's breakpoint placement
  // Return total cost in pricing units
}

const trials = 1000;
const results = {
  'single-trailing': [] as number[],
  'k4-knapsack': [] as number[],
  '4-static': [] as number[],
};
for (let i = 0; i < trials; i++) {
  for (const strat of ['single-trailing', 'k4-knapsack', '4-static']) {
    results[strat].push(simulateSession(strat));
  }
}
// Report mean cost reduction of k4-knapsack vs single-trailing
```

Цель симулятора — pre-flight check перед запуском дорогих real API runs.

## Переиспользуем из прошлой сессии

| Источник | Что брать |
|---|---|
| `workdir/compaction_policies__20260508_0231/mle/code/llm_client.py:75-120` | `cached_tokens` поле уже трекалось → расширить до `cache_read_input_tokens` / `cache_creation_input_tokens` (нужно для detect-server-clearing). |
| `workdir/compaction_policies__20260508_0231/mle/cost_log.jsonl` | 12 274 LLM-вызова из прошлой сессии — есть `cache_hit` поле. Можно вытащить эмпирические распределения для синтетического simulator'а. |

## Пакет

```
packages/cache-budgeter/
├── src/
│   ├── index.ts                   # public exports
│   ├── budgeter.ts                # CacheBudgeter class
│   ├── knapsack.ts                # 4-of-N solver
│   ├── detector.ts                # server-side clearing detection
│   ├── forecast.ts                # growth-rate estimation
│   └── types.ts
├── sim/
│   ├── simulator.ts               # synthetic trajectories
│   └── report.ts                  # results table
├── tests/
│   ├── knapsack.spec.ts
│   ├── shouldCompact.spec.ts
│   ├── detector.spec.ts
│   └── fixtures/
│       └── anthropic-cache-stats.json
└── package.json
```

## Риски

- **R1 — Forecast accuracy**. `remainingTurnsForecast = 8` — жёсткая константа. В реальности агенты-короткие и длинные. Mitigation: динамически калибровать после каждой завершённой сессии; начальный default = историческая медиана.
- **R2 — Server-side clearing detector ложноположителен**. Если пользователь сам вызвал invalidating-операцию (например, изменил tool set) — будет false positive. Mitigation: middleware трекает все локальные invalidating-события и вычитает их.
- **R3 — Pricing зависит от провайдера и модели**. Anthropic Haiku 4.5 != Sonnet 4.6 != Opus 4.5. `pricing` config — pluggable.
- **R4 — Knapsack игнорирует TTL**. Если breakpoint живёт >5 мин и сессия активна — он refresh'ится «бесплатно». Текущая модель этого не учитывает. Acceptable в v0.1; v0.2 — добавить TTL-aware scoring.
- **R5 — Knapsack solver brute force на N>50** — не масштабируется. Mitigation: для N>30 — переход на greedy approximation (выбираем top-k по `expectedReads × prefixTokens`).
