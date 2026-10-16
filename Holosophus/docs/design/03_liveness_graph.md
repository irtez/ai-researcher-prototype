# 03 — M2: LivenessGraph

**Связано:** [overview](00_overview.md), [02_entity_extractor](02_entity_extractor.md), [05_compactor](05_compactor.md).

## Цель

Per-entity «вероятность нужности в следующих ходах». Используется как **gate-предикат**: сегмент считается eligible для компакции, только если все его entity_refs имеют liveness ниже порога.

## Зачем

Это второй ключевой компонент ELR-гипотезы (Entity-Liveness Retention) — главного proposer-предложения (P2, score 9.2). Без него M1-extractor бесполезен: мы знаем, какие entities есть в сегменте, но не знаем, можно ли его компактить.

## Зависимости

- M0 TypedSegment (читает `content_blocks[i].entity_refs` и `meta.turn_index`).
- M1 EntityExtractor (поставляет `entity_refs`).

## Сигналы liveness

Liveness — взвешенная сумма трёх сигналов:

| Сигнал | Что измеряет | Диапазон | Вес по умолчанию |
|---|---|---|---|
| **L_schema** | Достижимость entity-типа через input-схемы зарегистрированных инструментов. Если хоть один инструмент принимает `order_id` на вход — `L_schema(order_id) = 1`. Иначе 0. | {0, 1} | 0.4 |
| **L_recency** | Экспоненциально затухающее окно последних ссылок на entity_value. `exp(-α × turns_since_last_ref)`, α по умолчанию 0.3. | [0, 1] | 0.3 |
| **L_slot** | Явная привязка к open-task slots — текущим «целям» агента (например, `order_id` в работе). | {0, 1} | 0.3 |

Финальный score: `liveness = 0.4 × L_schema + 0.3 × L_recency + 0.3 × L_slot`.

**Default threshold для gate:** `0.3`. Если `liveness(entity) < 0.3` для **всех** entities сегмента — сегмент eligible.

## API контракт

```ts
// packages/liveness-graph/src/types.ts
import type { EntityRef, TypedSegment } from '@holo/typed-segment';

export interface LivenessConfig {
  /** Веса трёх сигналов. По умолчанию [0.4, 0.3, 0.3]. */
  weights?: { schema: number; recency: number; slot: number };
  /** Параметр экспоненциального затухания для L_recency. По умолчанию 0.3. */
  recencyAlpha?: number;
  /** Порог для gate-предиката. По умолчанию 0.3. */
  threshold?: number;
}

export interface ToolInputSchema {
  toolName: string;
  /** Список типов сущностей, которые инструмент принимает на вход. */
  acceptsTypes: string[];
}

export class LivenessGraph {
  constructor(config?: LivenessConfig);

  /**
   * Регистрирует tool input schemas. Влияет на L_schema.
   * Должно быть вызвано один раз при инициализации middleware.
   */
  setToolInputs(schemas: ToolInputSchema[]): void;

  /**
   * Устанавливает open-task slots (например, текущий order_id в работе).
   * Может меняться mid-session. Влияет на L_slot.
   */
  setOpenSlots(slots: { type: string; value: string }[]): void;

  /**
   * Обновляет граф новыми сегментами. Записывает last_ref_turn для entities.
   * Идемпотентно: повторное observe тех же сегментов не меняет состояние.
   */
  observe(segments: TypedSegment[]): void;

  /**
   * Вычисляет liveness для конкретной entity на текущем turn'е.
   */
  score(entity: EntityRef, currentTurn: number): number;

  /**
   * Gate-предикат: можно ли компактить этот сегмент?
   * = все его entity_refs имеют liveness < threshold.
   */
  isEligibleForCompaction(segment: TypedSegment, currentTurn: number): boolean;

  /** Telemetry. */
  getStats(): {
    entitiesTracked: number;
    eligibilityRate: number;  // доля сегментов eligible на последнем check
  };
}
```

## Реализация

### Структура графа

```ts
// packages/liveness-graph/src/graph.ts

interface EntityNode {
  type: string;
  value: string;
  firstSeenTurn: number;
  lastRefTurn: number;
  refCount: number;
  /** Сегменты, которые упоминают этот entity. Для debug / replay. */
  appearingIn: string[];  // segment.id list
}

export class LivenessGraph {
  private entities = new Map<string, EntityNode>();  // key: `${type}:${value}`
  private toolInputTypes = new Set<string>();
  private openSlots = new Set<string>();              // key: `${type}:${value}`
  private config: Required<LivenessConfig>;
  private lastEligibilityCheck: { eligible: number; total: number } = { eligible: 0, total: 0 };

  constructor(config: LivenessConfig = {}) {
    this.config = {
      weights: { schema: 0.4, recency: 0.3, slot: 0.3, ...(config.weights ?? {}) },
      recencyAlpha: config.recencyAlpha ?? 0.3,
      threshold: config.threshold ?? 0.3,
    };
  }

  setToolInputs(schemas: ToolInputSchema[]): void {
    this.toolInputTypes.clear();
    for (const s of schemas) {
      for (const t of s.acceptsTypes) this.toolInputTypes.add(t);
    }
  }

  setOpenSlots(slots: { type: string; value: string }[]): void {
    this.openSlots.clear();
    for (const s of slots) this.openSlots.add(`${s.type}:${s.value}`);
  }

  observe(segments: TypedSegment[]): void {
    for (const s of segments) {
      const turn = s.meta.turn_index;
      for (const block of s.content_blocks) {
        for (const ref of block.entity_refs ?? []) {
          const key = `${ref.type}:${ref.value}`;
          let node = this.entities.get(key);
          if (!node) {
            node = {
              type: ref.type,
              value: ref.value,
              firstSeenTurn: turn,
              lastRefTurn: turn,
              refCount: 0,
              appearingIn: [],
            };
            this.entities.set(key, node);
          }
          node.lastRefTurn = Math.max(node.lastRefTurn, turn);
          node.refCount++;
          if (!node.appearingIn.includes(s.id)) node.appearingIn.push(s.id);
        }
      }
    }
  }

  score(entity: EntityRef, currentTurn: number): number {
    const { schema, recency, slot } = this.config.weights;
    const alpha = this.config.recencyAlpha;

    const L_schema = this.toolInputTypes.has(entity.type) ? 1.0 : 0.0;

    const node = this.entities.get(`${entity.type}:${entity.value}`);
    const turnsSinceLastRef = node ? currentTurn - node.lastRefTurn : Infinity;
    const L_recency = node ? Math.exp(-alpha * Math.max(0, turnsSinceLastRef)) : 0.0;

    const L_slot = this.openSlots.has(`${entity.type}:${entity.value}`) ? 1.0 : 0.0;

    return schema * L_schema + recency * L_recency + slot * L_slot;
  }

  isEligibleForCompaction(segment: TypedSegment, currentTurn: number): boolean {
    this.lastEligibilityCheck.total++;
    const allRefs = segment.content_blocks.flatMap((b) => b.entity_refs ?? []);
    if (allRefs.length === 0) {
      this.lastEligibilityCheck.eligible++;
      return true;
    }
    const allBelowThreshold = allRefs.every(
      (e) => this.score(e, currentTurn) < this.config.threshold,
    );
    if (allBelowThreshold) this.lastEligibilityCheck.eligible++;
    return allBelowThreshold;
  }

  getStats() {
    return {
      entitiesTracked: this.entities.size,
      eligibilityRate:
        this.lastEligibilityCheck.total > 0
          ? this.lastEligibilityCheck.eligible / this.lastEligibilityCheck.total
          : 0,
    };
  }
}
```

### Извлечение `toolInputs` из Vercel AI SDK

```ts
// packages/liveness-graph/src/from-vercel.ts
import type { Tool } from 'ai';
import { z } from 'zod';

/**
 * Сканирует Zod-схему параметров инструмента и извлекает
 * имена top-level полей (которые становятся типами в EntityRef).
 */
export function extractToolInputs(tools: Record<string, Tool>): ToolInputSchema[] {
  return Object.entries(tools).map(([toolName, tool]) => ({
    toolName,
    acceptsTypes: zodSchemaToFieldTypes(tool.inputSchema as z.ZodObject<any>),
  }));
}

function zodSchemaToFieldTypes(schema: z.ZodObject<any>): string[] {
  if (!(schema instanceof z.ZodObject)) return [];
  return Object.keys(schema.shape).map((field) =>
    field.replace(/([a-z])([A-Z])/g, '$1_$2').toLowerCase(),
  );
}
```

Это позволяет в middleware автоматически:
```ts
const livenessGraph = new LivenessGraph();
livenessGraph.setToolInputs(extractToolInputs(tools));
```

без ручной регистрации.

### Open-task slots — UX вопрос

Open slots — это «вещи в работе». В retail-агенте: текущий `order_id`, который пользователь обсуждает. По умолчанию слотов нет — `L_slot = 0`. Пользователь библиотеки может явно установить:

```ts
livenessGraph.setOpenSlots([
  { type: 'order_id', value: '#W12345678' },
]);
```

Опционально: middleware может **автоматически выводить** слоты из последнего user-message (если в нём упомянут entity, типичный для open task). Пока эта эвристика — future work (см. R3).

## Acceptance criteria

- [ ] **Gate F1 ≥ 0.85** на 50 manually-labeled τ-bench airline trajectories (нужно создать gold).
- [ ] **τ-bench retail pass@1** с `ELR-gated rolling_summary` ≥ 0.55 (vs прошлой сессии task_aware = 0.00).
- [ ] **Идемпотентность `observe()`**: повторный вызов на тех же сегментах не меняет `entities` map.
- [ ] **Latency `score()`**: < 0.5 ms p99 (Map lookup + 3 умножения).
- [ ] **Latency `isEligibleForCompaction()`**: < 2 ms p99 на сегменте с 5 entity_refs.

## Тестовый план

```typescript
// packages/liveness-graph/tests/eligibility.spec.ts
import { test, expect } from 'vitest';
import { LivenessGraph } from '../src';
import { goldLabels } from './fixtures/taubench-airline-eligibility-50.json';

test('gate F1 on τ-bench airline gold ≥ 0.85', () => {
  let tp = 0, fp = 0, fn = 0;
  for (const trajectory of goldLabels) {
    const g = new LivenessGraph();
    g.setToolInputs(trajectory.toolInputs);
    g.setOpenSlots(trajectory.openSlots);
    g.observe(trajectory.segments);
    for (const seg of trajectory.segments) {
      const predicted = g.isEligibleForCompaction(seg, trajectory.currentTurn);
      const actual = trajectory.gold_eligibility[seg.id];
      if (predicted && actual) tp++;
      else if (predicted && !actual) fp++;
      else if (!predicted && actual) fn++;
    }
  }
  const precision = tp / (tp + fp);
  const recall = tp / (tp + fn);
  const f1 = 2 * precision * recall / (precision + recall);
  expect(f1).toBeGreaterThanOrEqual(0.85);
});
```

```typescript
// packages/liveness-graph/tests/recency.spec.ts
test('L_recency decays exponentially', () => {
  const g = new LivenessGraph({ recencyAlpha: 0.3 });
  g.observe([makeSegment({ entity: 'order_id:#W123', turn: 0 })]);
  expect(g.score({ type: 'order_id', value: '#W123' }, 0)).toBeCloseTo(0.3, 2);   // L_recency=1, schema=slot=0
  expect(g.score({ type: 'order_id', value: '#W123' }, 5)).toBeCloseTo(0.3 * Math.exp(-1.5), 2);
});
```

## Конфигурация — рекомендации по тюнингу

| Сценарий | Рекомендуемые knobs |
|---|---|
| **Default (5-15 ходов retail-style)** | `weights: { schema: 0.4, recency: 0.3, slot: 0.3 }`, `recencyAlpha: 0.3`, `threshold: 0.3`. |
| **Длинная сессия (>30 ходов)** | `recencyAlpha: 0.15` (медленнее затухает), `threshold: 0.2` (агрессивнее компактить). |
| **Чисто разговорный режим (без tools)** | Отключить ELR: не вызывать `livenessGraph.observe()`; компактор работает без gate. |
| **Tool-heavy с большим schema** | Если много инструментов с пересекающимися типами входов, `L_schema` будет почти всегда 1 → почти ничего не eligible. Mitigation: уменьшить `weights.schema` до 0.25. |

## Переиспользуем из прошлой сессии

Нет прямых аналогов в `compaction_policies__20260508_0231/`. Это полностью новый модуль. Но идея «importance-based retention» концептуально аналогична `type_aware` стратегии (`compactors.py:202-431`), где per-role budget shares играли роль «importance». ELR — это generalization этой идеи на per-entity уровень.

## Пакет

```
packages/liveness-graph/
├── src/
│   ├── index.ts                  # public exports
│   ├── graph.ts                  # LivenessGraph class
│   ├── from-vercel.ts            # extractToolInputs helper
│   └── types.ts
├── tests/
│   ├── eligibility.spec.ts
│   ├── recency.spec.ts
│   ├── schema-reachability.spec.ts
│   ├── open-slots.spec.ts
│   └── fixtures/
│       └── taubench-airline-eligibility-50.json
└── package.json
```

## Риски

- **R1 — Gold set для F1.** Нужно вручную разметить 50 airline-трасс на eligibility. **Action**: задача на Week 2 Day 1, ~2 часа работы.
- **R2 — Liveness threshold нестабильный across drivers** (прошлая сессия показала ρ = −0.5 cross-vendor). Mitigation: gate-предикат **детерминирован** (не зависит от LLM), но *threshold value* может потребовать per-vendor тюнинга. Sweep в эксперименте E3 (см. `proposals.md#P2`).
- **R3 — Open-task slots — открытый вопрос UX.** В v0.1 — ручная установка через API. В v0.2 — рассмотреть авто-вывод из user-message via лёгкий NLU-классификатор. **Acceptable в v0.1**: zero-slots default (`L_slot=0`) даёт приемлемые числа на τ-bench retail.
- **R4 — Память.** Map of entities может расти безгранично в длинных сессиях. Mitigation: при `entitiesTracked > 10_000` — evict entities с `lastRefTurn < currentTurn - 100` (мертвые более 100 ходов).
