# 02 — M1: EntityExtractor

**Связано:** [overview](00_overview.md), [01_typed_segment](01_typed_segment.md), [03_liveness_graph](03_liveness_graph.md).

## Цель

Парсит `tool_call` и `tool_result` сегменты на write-time, извлекает **структурные ссылки** на entities (order_id, user_id, email, и т.д.). Результат пишется в `content_block.entity_refs` для использования M2 LivenessGraph.

## Зачем

**Это сердце новизны библиотеки.** Прошлая сессия диагностировала: task_aware → **0.00 pass@1** на τ-bench retail именно из-за выкидывания entity_id из tool_results. Решение — extract entity_refs на момент append'а, и затем gate компакцию через LivenessGraph.

## Зависимости

- M0 TypedSegment (читает `content_blocks`, пишет в `entity_refs`).
- Внешний: `ai` (Vercel AI SDK) — для LLM-вызовов в tier-3.

## API контракт

```ts
// packages/entity-extractor/src/types.ts
import type { ContentBlock, EntityRef } from '@holo/typed-segment';

export interface EntityPattern {
  type: string;            // 'order_id', 'user_id', etc.
  pattern: RegExp;         // matched against text
}

export interface ExtractorConfig {
  /** Tier-1 regex patterns. Default = DEFAULT_REGEXES (ниже). */
  entityPatterns?: EntityPattern[];
  /** Tier-2: какие JSON-ключи считать ID-полями (по имени поля). */
  jsonIdFieldRegex?: RegExp;
  /** Tier-3 LLM rescue model (Vercel-compatible). Если null → tier-3 off. */
  rescueModel?: import('ai').LanguageModel | null;
  /** Cap на долю сегментов, на которых tier-3 может стрелять. По умолчанию 0.05. */
  rescueBudgetFractionMax?: number;
  /** Min length tool_result в токенах, чтобы tier-3 на нём стрелял. По умолчанию 200. */
  rescueMinTokens?: number;
}

export class EntityExtractor {
  constructor(config?: ExtractorConfig);

  /**
   * Извлекает entity_refs из всех content_blocks одного сегмента.
   * Мутирует сегмент: пишет refs в content_blocks[i].entity_refs.
   * Возвращает обогащённый сегмент.
   *
   * Идемпотентно: если entity_refs уже заполнены — no-op.
   */
  async extract(segment: TypedSegment): Promise<TypedSegment>;

  /**
   * Bulk-вариант, фильтрует уже-обработанные.
   * Используется в горячем пути после получения новых сегментов.
   */
  async extractBatch(segments: TypedSegment[]): Promise<TypedSegment[]>;

  /** Telemetry для UI / логов. */
  getStats(): { tier1_hits: number; tier2_hits: number; tier3_calls: number; tier3_total: number };
}
```

## Алгоритм

Три tier'а, исполняются последовательно, каждый последующий стреляет только когда предыдущие не нашли:

### Tier 1 — Regex (default patterns)

```ts
// packages/entity-extractor/src/tier1_regex.ts

export const DEFAULT_REGEXES: EntityPattern[] = [
  // τ-bench-style retail/airline order IDs (с # или без)
  { type: 'order_id', pattern: /\b#?W?\d{6,10}\b/g },
  { type: 'user_id', pattern: /\b[Uu]ser[_-]?\d{6,10}\b/g },
  { type: 'product_sku', pattern: /\bSKU-[A-Z0-9]{6,12}\b/gi },
  { type: 'reservation_id', pattern: /\b[Rr]es[_-]?\d{6,10}\b/g },
  // Универсальные форматы
  { type: 'email', pattern: /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g },
  { type: 'iso_date', pattern: /\b\d{4}-\d{2}-\d{2}\b/g },
  { type: 'iso_datetime', pattern: /\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?\b/g },
  { type: 'currency_usd', pattern: /\$\d+(?:\.\d{2})?/g },
  { type: 'phone', pattern: /\+?\d[\d\s\-()]{8,}\d/g },
  { type: 'tracking_number', pattern: /\b1Z[0-9A-Z]{16}\b|\b\d{12,15}\b/g },
];

function tier1Extract(text: string, patterns: EntityPattern[]): EntityRef[] {
  const refs: EntityRef[] = [];
  for (const { type, pattern } of patterns) {
    for (const match of text.matchAll(pattern)) {
      refs.push({ type, value: match[0], path: `offset:${match.index}` });
    }
  }
  return refs;
}
```

**Стоимость:** O(text_length × n_patterns). Для типичного 2 KB tool_result — <5 ms p95.

### Tier 2 — JSON-path

Если `content_block.kind === 'json'`, рекурсивно ходим по дереву и собираем поля, имена которых матчат `jsonIdFieldRegex`.

```ts
// packages/entity-extractor/src/tier2_jsonpath.ts

const DEFAULT_JSON_ID_REGEX = /^(id|.*_id|order|orderId|user|userId|productId|itemId|reservationId|trackingNumber)$/i;

function tier2Extract(obj: unknown, regex: RegExp, path = '$'): EntityRef[] {
  if (obj === null || typeof obj !== 'object') return [];
  const refs: EntityRef[] = [];
  for (const [k, v] of Object.entries(obj)) {
    const newPath = `${path}.${k}`;
    if (regex.test(k) && (typeof v === 'string' || typeof v === 'number')) {
      refs.push({
        type: inferTypeFromKey(k),  // 'order_id' from 'orderId', 'orderID', etc.
        value: String(v),
        path: newPath,
      });
    }
    if (typeof v === 'object' && v !== null) {
      refs.push(...tier2Extract(v, regex, newPath));
    }
  }
  return refs;
}

function inferTypeFromKey(key: string): string {
  // 'orderId' / 'order_id' / 'ORDER_ID' → 'order_id'
  return key
    .replace(/([a-z])([A-Z])/g, '$1_$2')
    .replace(/^_+|_+$/g, '')
    .toLowerCase();
}
```

**Стоимость:** O(json_node_count). Для типичного 2 KB JSON-tool_result — <10 ms p95.

### Tier 3 — LLM rescue

Стреляет когда tier 1+2 не нашли ни одной ссылки на сегменте, который **выглядит как «отчёт от инструмента»**:

```ts
// packages/entity-extractor/src/tier3_llm.ts

const RESCUE_PROMPT = `
You are extracting structured entity references from a tool result.
Return a JSON array of objects with fields:
- type: short noun (e.g. "order_id", "product_sku", "tracking_number", "email")
- value: the literal token as it appears in the text
- path: a JSON-path or text-offset (e.g. "$.orderId" or "offset:142")

Only extract identifiers that downstream tool calls might reference (IDs, names, dates, currencies).
DO NOT extract free-form descriptions or generic words.

Tool result:
{content}

Return ONLY the JSON array, no other text.
`.trim();

async function tier3Extract(
  segment: TypedSegment,
  model: LanguageModel,
): Promise<EntityRef[]> {
  // Concatenate all text content for the segment
  const content = segment.content_blocks
    .map((b) => b.text ?? JSON.stringify(b.json ?? ''))
    .join('\n')
    .slice(0, 4000);  // hard cap to avoid blowing the rescue budget

  const result = await generateText({
    model,
    prompt: RESCUE_PROMPT.replace('{content}', content),
    maxTokens: 800,
    temperature: 0,
  });

  try {
    const parsed = JSON.parse(result.text);
    return Array.isArray(parsed) ? parsed.filter(isValidEntityRef) : [];
  } catch {
    return [];
  }
}

function shouldRunTier3(segment: TypedSegment, stats: ExtractorStats): boolean {
  // Not a tool_result → don't rescue
  if (segment.role !== 'tool_result') return false;
  // Tier 1+2 already found refs → don't rescue
  const hasRefs = segment.content_blocks.some((b) => (b.entity_refs?.length ?? 0) > 0);
  if (hasRefs) return false;
  // Tokens below threshold → not worth the LLM call
  if ((segment.meta.tokens ?? 0) < (config.rescueMinTokens ?? 200)) return false;
  // Budget cap: rescue can fire on at most rescueBudgetFractionMax * segments_seen
  const fired_frac = stats.tier3_calls / Math.max(stats.tier3_total, 1);
  if (fired_frac >= (config.rescueBudgetFractionMax ?? 0.05)) return false;
  return true;
}
```

**Стоимость:** один LLM-вызов 200-500 ms (Gemini-3-Flash или Haiku-4.5). Cap = 5 % сегментов в среднем.

### Композиция (главная функция)

```ts
// packages/entity-extractor/src/index.ts

export class EntityExtractor {
  private stats: ExtractorStats = { tier1_hits: 0, tier2_hits: 0, tier3_calls: 0, tier3_total: 0 };
  constructor(private config: ExtractorConfig = {}) {}

  async extract(segment: TypedSegment): Promise<TypedSegment> {
    // Idempotency
    if (segment.content_blocks.every((b) => b.entity_refs !== undefined)) {
      return segment;
    }

    this.stats.tier3_total++;
    let foundAny = false;

    for (const block of segment.content_blocks) {
      const refs: EntityRef[] = [];

      // Tier 1
      if (block.kind === 'text' && block.text) {
        const t1 = tier1Extract(block.text, this.config.entityPatterns ?? DEFAULT_REGEXES);
        refs.push(...t1);
        this.stats.tier1_hits += t1.length;
      }

      // Tier 2
      if (block.kind === 'json' && block.json !== undefined) {
        const t2 = tier2Extract(block.json, this.config.jsonIdFieldRegex ?? DEFAULT_JSON_ID_REGEX);
        refs.push(...t2);
        this.stats.tier2_hits += t2.length;
      }

      block.entity_refs = refs;
      if (refs.length > 0) foundAny = true;
    }

    // Tier 3 rescue
    if (!foundAny && this.config.rescueModel && shouldRunTier3(segment, this.stats, this.config)) {
      this.stats.tier3_calls++;
      const rescueRefs = await tier3Extract(segment, this.config.rescueModel);
      if (rescueRefs.length > 0 && segment.content_blocks[0]) {
        // Attach to the first text/json block (heuristic)
        segment.content_blocks[0].entity_refs = [
          ...(segment.content_blocks[0].entity_refs ?? []),
          ...rescueRefs,
        ];
      }
    }

    return segment;
  }
}
```

## Конфиг по умолчанию

```ts
const DEFAULT_CONFIG: Required<ExtractorConfig> = {
  entityPatterns: DEFAULT_REGEXES,        // 10 builtin patterns
  jsonIdFieldRegex: DEFAULT_JSON_ID_REGEX,
  rescueModel: null,                       // off by default; users opt-in
  rescueBudgetFractionMax: 0.05,
  rescueMinTokens: 200,
};
```

## Acceptance criteria

- [ ] **Recall ≥ 85 %** на 200-trace gold set (carry-over из прошлой сессии — `workdir/compaction_policies__20260508_0231/` имеет τ-bench retail трассы) при tier-1+2.
- [ ] **Recall ≥ 92 %** retail / **≥ 78 %** airline на tier-1+2 only. С tier-3 rescue: **≥ 94 %** retail / **≥ 94 %** airline.
- [ ] **Latency**: tier-1+2 < 5 ms p95 на типичном 2 KB сегменте. Tier-3: 200–500 ms (LLM-вызов).
- [ ] **Idempotency**: повторный `extract()` на уже-обработанном сегменте не меняет `entity_refs` и не делает LLM-вызовов.
- [ ] **Budget cap**: tier-3 fires не чаще 5 % от общего числа `extractBatch()` вызовов; telemetry alarm иначе.

## Тестовый план

```typescript
// packages/entity-extractor/tests/recall.spec.ts
import { test, expect } from 'vitest';
import { EntityExtractor } from '../src';
import goldTraces from './fixtures/taubench-retail-200.json';

test('tier 1+2 recall on τ-bench retail gold ≥ 85 %', async () => {
  const extractor = new EntityExtractor();
  let hits = 0;
  let expected = 0;
  for (const trace of goldTraces) {
    for (const segment of trace.segments) {
      const enriched = await extractor.extract(segment);
      const extracted = enriched.content_blocks
        .flatMap((b) => b.entity_refs ?? [])
        .map((r) => r.value);
      const gold = trace.entity_gold[segment.id] ?? [];
      hits += gold.filter((g) => extracted.includes(g)).length;
      expected += gold.length;
    }
  }
  expect(hits / expected).toBeGreaterThanOrEqual(0.85);
});
```

Аналогичные тесты на τ-bench airline (с tier-3 rescue) и idempotency.

## Переиспользуем из прошлой сессии

| Источник | Что брать |
|---|---|
| `workdir/compaction_policies__20260508_0231/mle/code/type_aware` (в `compactors.py`) | Per-type обработка `tool_call` / `tool_result` сегментов как ориентир, какие поля считать ID-полями. |
| `workdir/compaction_policies__20260508_0231/mle/results/taubench_main.jsonl` | Источник 200-trace gold set (нужно вручную аннотировать entity_refs на 200 segment'ах) — основа для recall теста. |
| `workdir/compaction_policies__20260508_0231/mle/code/llm_client.py` | Cost-log pattern для tier-3 LLM-вызовов. |

## Пакет

```
packages/entity-extractor/
├── src/
│   ├── index.ts                   # public exports
│   ├── tier1_regex.ts             # DEFAULT_REGEXES + tier1Extract
│   ├── tier2_jsonpath.ts          # tier2Extract + DEFAULT_JSON_ID_REGEX
│   ├── tier3_llm.ts               # tier3Extract + RESCUE_PROMPT
│   ├── extractor.ts               # main class
│   └── types.ts
├── tests/
│   ├── recall.spec.ts
│   ├── tier1.spec.ts
│   ├── tier2.spec.ts
│   ├── tier3.spec.ts              # with mocked LLM
│   └── fixtures/
│       └── taubench-retail-200.json
└── package.json
```

## Риски

- **R1**: тестовый gold set — 200 manually-annotated trace'ов — нужно создать. Source — `workdir/compaction_policies__20260508_0231/mle/results/taubench_main.jsonl`. **Action**: написать notebook `eval/annotate_entities.ipynb` (Python, через Holosophus orchestrator) или вручную в первую неделю проекта.
- **R2**: regex patterns могут пропускать domain-specific форматы (например, custom hash patterns). Mitigation: `config.entityPatterns` — pluggable; cookbook в README показывает, как добавить свои.
- **R3**: tier-3 LLM может галлюцинировать «extracted» values, которых не было в тексте. Mitigation: validate каждое `value` через `text.includes(value) || jsonContains(json, value)` перед сохранением.
- **R4**: при большом потоке tool_result'ов tier-3 budget может быть слишком жёстким; telemetry alarm если recall падает. Mitigation: динамическая регулировка `rescueBudgetFractionMax` по telemetry.
